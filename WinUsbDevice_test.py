"""Directly verify the bootloader's WCID / Microsoft OS descriptor responses.

Windows only sends these requests automatically during the first enumeration of
an "unknown" device, so they are easy to miss in a bus capture. This script
replays the same vendor requests through WinUSB and checks the returned
descriptors field-by-field.

直接验证 Bootloader 的 WCID / Microsoft OS 描述符响应。

Windows 只会在一个“未知”设备第一次枚举时自动发送这些请求，所以在
USB 抓包里很容易错过。这个脚本会通过 WinUSB 手动重放同样的厂商请求，
并逐字段检查设备返回的描述符内容是否正确。

Usage:
  python wcid_test.py
  python wcid_test.py --vid 0x16C0 --pid 0x05DF

Optional:
  --list      print matching Plug-and-Play nodes before probing
  --quiet     skip the extra interface diagnostics
"""

import argparse
import ctypes
import ctypes.wintypes as wintypes
import re
import subprocess
import sys
from typing import Iterable, Optional, Tuple

from usb_transport import BOOT_PID, BOOT_PRODUCT, BOOT_VID, WinUsbTransport


class SetupPacket(ctypes.Structure):
    _fields_ = [
        ("RequestType", ctypes.c_ubyte),
        ("Request", ctypes.c_ubyte),
        ("Value", ctypes.c_ushort),
        ("Index", ctypes.c_ushort),
        ("Length", ctypes.c_ushort),
    ]


class WcidProbe(WinUsbTransport):
    """WinUsbTransport plus a raw control-transfer helper."""

    _GUIDS = list(dict.fromkeys(
        WinUsbTransport._GUIDS
        + [
            "{88bae032-5a81-49f0-bc3d-a4ff138216d6}",  # WinUSB class GUID
        ]
    ))

    def control_transfer(
        self, req_type: int, request: int, value: int, index: int, length: int
    ) -> bytes:
        if self._whandle is None:
            self._set_last_error("WinUsb_ControlTransfer", 0)
            return b""
        wu = self._get_winusb()
        if not hasattr(wu, "WinUsb_ControlTransfer"):
            wu.WinUsb_ControlTransfer.argtypes = [
                wintypes.HANDLE,
                SetupPacket,
                ctypes.POINTER(ctypes.c_ubyte),
                wintypes.ULONG,
                ctypes.POINTER(wintypes.DWORD),
                wintypes.BOOL,
            ]
            wu.WinUsb_ControlTransfer.restype = wintypes.BOOL
        pkt = SetupPacket(req_type, request, value, index, length)
        buf = (ctypes.c_ubyte * length)()
        got = wintypes.DWORD()
        ok = wu.WinUsb_ControlTransfer(
            self._whandle, pkt, buf, length, ctypes.byref(got), False
        )
        if not ok:
            err = ctypes.windll.kernel32.GetLastError()
            self._set_last_error("WinUsb_ControlTransfer", err)
            return b""
        self._set_last_error("", 0)
        return bytes(buf[: got.value])


def le16(data: bytes, off: int) -> Optional[int]:
    if len(data) < off + 2:
        return None
    return data[off] | (data[off + 1] << 8)


def le32(data: bytes, off: int) -> Optional[int]:
    if len(data) < off + 4:
        return None
    return (
        data[off]
        | (data[off + 1] << 8)
        | (data[off + 2] << 16)
        | (data[off + 3] << 24)
    )


def slice_bytes(data: bytes, off: int, size: int) -> Optional[bytes]:
    if len(data) < off + size:
        return None
    return bytes(data[off : off + size])


def hexdump(data: bytes) -> str:
    return " ".join(f"{x:02X}" for x in data) if data else "<empty>"


def expect(label: str, actual, expected) -> bool:
    print(f"  {label:<18}: {actual} (expect {expected})")
    return actual == expected


def print_section(title: str) -> None:
    print(f"\n{title}")


def check_compat(data: bytes) -> bool:
    ok = True
    ok &= expect("length", len(data), 40)
    ok &= expect("dwLength", le32(data, 0), 40)
    ok &= expect("bcdVersion", f"0x{(le16(data, 4) or 0):04X}", "0x0100")
    ok &= expect("wIndex", le16(data, 6), 4)
    b_count = data[10] if len(data) > 10 else None
    ok &= expect("bCount", b_count, 1)
    first_if = data[15] if len(data) > 15 else None
    ok &= expect("bFirstInterface", first_if, "3 = WinUSB")
    cid = slice_bytes(data, 16, 8)
    ok &= expect("CompatibleID", repr(cid), "b'WINUSB\\x00\\x00'")
    if cid is not None:
        ok &= cid == b"WINUSB\x00\x00"
    print("  COMPAT ID         ==> " + ("PASS" if ok else "FAIL"))
    return ok


def check_extprops(data: bytes) -> bool:
    ok = True
    ok &= expect("length", len(data), 142)
    ok &= expect("dwLength", le32(data, 0), 142)
    ok &= expect("bcdVersion", f"0x{(le16(data, 4) or 0):04X}", "0x0100")
    ok &= expect("wIndex", le16(data, 6), 5)
    ok &= expect("wCount", le16(data, 8), 1)

    name_len = le16(data, 18)
    name = None
    if name_len is not None:
        raw_name = slice_bytes(data, 20, name_len)
        if raw_name is not None:
            name = raw_name.decode("utf-16-le", errors="replace").rstrip("\x00")
    ok &= expect("property name", repr(name), "'DeviceInterfaceGUID'")
    if name is not None:
        ok &= name == "DeviceInterfaceGUID"

    data_len = None
    guid = None
    if name_len is not None:
        data_len = le32(data, 20 + name_len)
        if data_len is not None:
            raw_guid = slice_bytes(data, 24 + name_len, data_len)
            if raw_guid is not None:
                guid = raw_guid.decode("utf-16-le", errors="replace").rstrip("\x00")
    ok &= expect("data length", data_len, 78)
    print(f"  DeviceInterfaceGUID: {guid!r}")
    print("  EXT PROPS         ==> " + ("PASS" if ok else "FAIL"))
    return ok


def check_msos20(data: bytes) -> bool:
    ok = True
    ok &= expect("length", len(data), 178)
    ok &= expect("wDescriptorVersion", f"0x{(le16(data, 4) or 0):04X}", "0x0100")
    ok &= expect("wIndex", le16(data, 6), 7)
    ok &= expect("wTotalLength", le16(data, 8), 178)
    cid = slice_bytes(data, 26, 8)
    ok &= expect("CompatibleID", repr(cid), "b'WINUSB\\x00\\x00'")
    if cid is not None:
        ok &= cid == b"WINUSB\x00\x00"
    print("  MS OS 2.0         ==> " + ("PASS" if ok else "FAIL"))
    return ok


def list_bootloader_nodes(vid: int, pid: int) -> Iterable[Tuple[str, str]]:
    script = rf"""
$items = Get-PnpDevice | Where-Object {{
    $_.InstanceId -match 'VID_{vid:04X}&PID_{pid:04X}'
}} | Select-Object FriendlyName, InstanceId
$items | ForEach-Object {{
    "$($_.FriendlyName)`t$($_.InstanceId)"
}}
"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return []
    result = []
    for line in (out.stdout or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) == 2:
            result.append((parts[0].strip(), parts[1].strip()))
    return result


def collect_probe_diagnostics(dev: WcidProbe) -> dict:
    registry_guids = list(dev._read_interface_guids())
    scanned_guids = list(dev._scan_device_classes())
    candidate_guids = list(dict.fromkeys(registry_guids + scanned_guids + list(dev._GUIDS)))
    device_path = dev._find_device()
    return {
        "registry_guids": registry_guids,
        "scanned_guids": scanned_guids,
        "candidate_guids": candidate_guids,
        "device_path": device_path,
    }


def print_probe_diagnostics(
    vid: int, pid: int, nodes: Iterable[Tuple[str, str]], diagnostics: dict
) -> None:
    print_section("probe diagnostics:")
    print(f"  target identity    : {vid:04X}:{pid:04X}")

    node_list = list(nodes)
    if node_list:
        print("  matching PnP nodes :")
        for friendly_name, instance_id in node_list:
            print(f"    {friendly_name or '<no name>'}: {instance_id}")
    else:
        print("  matching PnP nodes : <none>")

    registry_guids = diagnostics["registry_guids"]
    scanned_guids = diagnostics["scanned_guids"]
    candidate_guids = diagnostics["candidate_guids"]
    device_path = diagnostics["device_path"]

    print("  registry GUIDs     : " + (", ".join(registry_guids) if registry_guids else "<none>"))
    print("  scanned GUIDs      : " + (", ".join(scanned_guids) if scanned_guids else "<none>"))
    print("  candidate GUIDs    : " + (", ".join(candidate_guids) if candidate_guids else "<none>"))
    print("  resolved path      : " + (device_path or "<not found>"))


def autodetect_vidpid(default_vid: int, default_pid: int) -> Optional[Tuple[int, int]]:
    """Find the connected bootloader node's VID/PID via Get-PnpDevice."""
    for friendly_name, instance_id in list_bootloader_nodes(default_vid, default_pid):
        if BOOT_PRODUCT in friendly_name or "MI_03" in instance_id.upper():
            match = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", instance_id)
            if match:
                return int(match.group(1), 16), int(match.group(2), 16)
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe the bootloader's WCID / Microsoft OS descriptors."
    )
    parser.add_argument("--vid", type=lambda x: int(x, 0), default=BOOT_VID)
    parser.add_argument("--pid", type=lambda x: int(x, 0), default=BOOT_PID)
    parser.add_argument(
        "--list",
        action="store_true",
        help="list matching Plug-and-Play nodes before probing",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="skip the extra interface diagnostics",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    vid, pid = args.vid, args.pid

    if (vid, pid) == (BOOT_VID, BOOT_PID):
        detected = autodetect_vidpid(vid, pid)
        if detected:
            vid, pid = detected
            print(f"auto-detected bootloader identity: {vid:04X}:{pid:04X}")
        else:
            print(f"using configured default identity: {vid:04X}:{pid:04X}")
    else:
        print(f"using manual identity override: {vid:04X}:{pid:04X}")

    nodes = list(list_bootloader_nodes(vid, pid))
    if args.list:
        if nodes:
            print_section("matching PnP nodes:")
            for friendly_name, instance_id in nodes:
                print(f"  {friendly_name or '<no name>'}: {instance_id}")
        else:
            print_section("matching PnP nodes:")
            print("  <none>")

    dev = WcidProbe(vid=vid, pid=pid)
    diagnostics = collect_probe_diagnostics(dev) if not args.quiet else None
    if diagnostics is not None:
        print_probe_diagnostics(vid, pid, nodes, diagnostics)

    if not dev.open():
        print(f"open {vid:04X}:{pid:04X} failed: {dev.describe_last_error()}")
        print("Hint: make sure the bootloader exposes the WinUSB interface on MI_03.")
        print("      If needed, bind the correct WinUSB-based driver to the")
        print(f"      '{BOOT_PRODUCT}' device node before rerunning this test.")
        return 1

    if diagnostics is not None and diagnostics.get("device_path"):
        print("  opened path        : " + diagnostics["device_path"])

    all_ok = True

    requests = [
        (
            "[1] MS OS 1.0 Compatible ID  (C0 07 00 00 04 00 28 00)",
            (0xC0, 0x07, 0x0000, 0x0004, 40),
            check_compat,
            True,
        ),
        (
            "[2] MS OS 1.0 Extended Properties, wValue=3 (C0 07 03 00 05 00 8E 00)",
            (0xC0, 0x07, 0x0003, 0x0005, 142),
            check_extprops,
            True,
        ),
        (
            "[3] MS OS 1.0 Extended Properties, wValue=0 (C0 07 00 00 05 00 8E 00)",
            (0xC0, 0x07, 0x0000, 0x0005, 142),
            None,
            False,
        ),
        (
            "[4] MS OS 2.0 Descriptor Set (C0 07 00 00 07 00 B2 00)",
            (0xC0, 0x07, 0x0000, 0x0007, 178),
            check_msos20,
            True,
        ),
    ]

    for title, transfer_args, checker, required in requests:
        print(f"\n{title}")
        response = dev.control_transfer(*transfer_args)
        print("  raw: " + hexdump(response))
        if response and checker is not None:
            all_ok &= checker(response)
        elif required:
            all_ok = False
            print(f"  request failed: {dev.describe_last_error() or 'no data returned'}")
        else:
            print("  (empty/stall is expected if firmware requires wValue=3)")

    dev.close()
    print("\nRESULT: " + ("ALL PASS" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 2


if __name__ == "__main__":
    sys.exit(main())
