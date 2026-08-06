"""Directly verify the bootloader's WCID / MS-OS descriptor responses.

Windows normally performs this handshake during the very first enumeration
after a device identity becomes "unknown", which is easy to miss on a bus
capture.  This script sends the same vendor control requests by hand and
checks the firmware's answers byte-by-byte.

Usage:
  1. In Device Manager, manually install the WinUSB driver on the
     "DFM Bootloader" node whose hardware ID ends with &MI_03
     (right-click -> Update driver -> pick from list -> WinUSB device).
  2. Run:  python wcid_test.py

Optional VID/PID override:
  python wcid_test.py 0x16C8 0x15DF
"""

import ctypes
import ctypes.wintypes as wintypes
import re
import subprocess
import sys

from usb_transport import WinUsbTransport


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

    _GUIDS = [
        "{9B0D1CA8-2D68-4BA2-9E72-401055510001}",
        "{88bae032-5a81-49f0-bc3d-a4ff138216d6}",  # WinUSB class GUID
        "{9F08D6F6-9CC2-4F2F-8BFB-69E0743D31B9}",
    ]

    def control_transfer(self, req_type, request, value, index, length):
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
        return bytes(buf[: got.value])


def le16(b, off):
    return b[off] | (b[off + 1] << 8)


def le32(b, off):
    return b[off] | (b[off + 1] << 8) | (b[off + 2] << 16) | (b[off + 3] << 24)


def hexdump(b):
    return " ".join(f"{x:02X}" for x in b)


def check_compat(b):
    ok = True
    print(f"  length            : {len(b)} (expect 40)")
    ok &= len(b) == 40
    print(f"  dwLength          : {le32(b, 0)} (expect 40)")
    ok &= le32(b, 0) == 40
    print(f"  bcdVersion        : 0x{le16(b, 4):04X} (expect 0x0100)")
    ok &= le16(b, 4) == 0x0100
    print(f"  wIndex            : {le16(b, 6)} (expect 4)")
    ok &= le16(b, 6) == 4
    print(f"  bCount            : {b[10]} (expect 1)")
    ok &= b[10] == 1
    print(f"  bFirstInterface   : {b[15]} (expect 3 = WinUSB)")
    ok &= b[15] == 3
    cid = bytes(b[16:24])
    print(f"  CompatibleID      : {cid!r} (expect b'WINUSB\\x00\\x00')")
    ok &= cid == b"WINUSB\x00\x00"
    print("  COMPAT ID  ==> " + ("PASS" if ok else "FAIL"))
    return ok


def check_extprops(b):
    ok = True
    print(f"  length            : {len(b)} (expect 142)")
    ok &= len(b) == 142
    print(f"  dwLength          : {le32(b, 0)} (expect 142)")
    ok &= le32(b, 0) == 142
    print(f"  bcdVersion        : 0x{le16(b, 4):04X} (expect 0x0100)")
    ok &= le16(b, 4) == 0x0100
    print(f"  wIndex            : {le16(b, 6)} (expect 5)")
    ok &= le16(b, 6) == 5
    print(f"  wCount            : {le16(b, 8)} (expect 1)")
    ok &= le16(b, 8) == 1
    name_len = le16(b, 18)
    name = bytes(b[20 : 20 + name_len]).decode("utf-16-le", errors="replace")
    print(f"  property name     : {name!r} (expect 'DeviceInterfaceGUID')")
    ok &= name.rstrip("\x00") == "DeviceInterfaceGUID"
    data_len = le32(b, 20 + name_len)
    guid = bytes(b[24 + name_len : 24 + name_len + data_len]).decode(
        "utf-16-le", errors="replace"
    )
    print(f"  data length       : {data_len} (expect 78)")
    ok &= data_len == 78
    print(f"  DeviceInterfaceGUID: {guid}")
    print("  EXT PROPS  ==> " + ("PASS" if ok else "FAIL"))
    return ok


def check_msos20(b):
    ok = True
    print(f"  length            : {len(b)} (expect 178)")
    ok &= len(b) == 178
    print(f"  wDescriptorVersion: 0x{le16(b, 4):04X} (expect 0x0100)")
    ok &= le16(b, 4) == 0x0100
    print(f"  wIndex            : {le16(b, 6)} (expect 7)")
    ok &= le16(b, 6) == 7
    print(f"  wTotalLength      : {le16(b, 8)} (expect 178)")
    ok &= le16(b, 8) == 178
    cid = bytes(b[26:34])
    print(f"  CompatibleID      : {cid!r} (expect b'WINUSB\\x00\\x00')")
    ok &= cid == b"WINUSB\x00\x00"
    print("  MS OS 2.0  ==> " + ("PASS" if ok else "FAIL"))
    return ok


def main():
    if len(sys.argv) > 2:
        vid = int(sys.argv[1], 0)
        pid = int(sys.argv[2], 0)
    else:
        detected = autodetect_vidpid()
        if detected:
            vid, pid = detected
            print(f"auto-detected bootloader identity: {vid:04X}:{pid:04X}")
        else:
            vid, pid = 0x16C9, 0x15DF
            print(f"no 'DFM Bootloader' device found; using default {vid:04X}:{pid:04X}")
    dev = WcidProbe(vid=vid, pid=pid)
    if not dev.open():
        print(f"open {vid:04X}:{pid:04X} failed: {dev.describe_last_error()}")
        print("Hint: the WinUSB driver must be installed on the &MI_03 node first.")
        print("      (Device Manager -> right-click 'DFM Bootloader' with")
        print("       hardware ID ending in &MI_03 -> Update driver ->")
        print("       browse -> let me pick -> WinUSB device)")
        return 1

    all_ok = True

    print("\n[1] MS OS 1.0 Compatible ID  (C0 07 00 00 04 00 28 00)")
    r = dev.control_transfer(0xC0, 0x07, 0x0000, 0x0004, 40)
    print("  raw: " + hexdump(r))
    all_ok &= check_compat(r) if r else False

    print("\n[2] MS OS 1.0 Extended Properties, wValue=3 (C0 07 03 00 05 00 8E 00)")
    r = dev.control_transfer(0xC0, 0x07, 0x0003, 0x0005, 142)
    print("  raw: " + hexdump(r))
    all_ok &= check_extprops(r) if r else False

    print("\n[3] MS OS 1.0 Extended Properties, wValue=0 (C0 07 00 00 05 00 8E 00)")
    r = dev.control_transfer(0xC0, 0x07, 0x0000, 0x0005, 142)
    print("  raw: " + hexdump(r))
    print("  (empty/stall is expected if firmware requires wValue=3)")

    print("\n[4] MS OS 2.0 Descriptor Set (C0 07 00 00 07 00 B2 00)")
    r = dev.control_transfer(0xC0, 0x07, 0x0000, 0x0007, 178)
    print("  raw: " + hexdump(r))
    all_ok &= check_msos20(r) if r else False

    dev.close()
    print("\nRESULT: " + ("ALL PASS" if all_ok else "SOME CHECKS FAILED"))
    return 0 if all_ok else 2


def autodetect_vidpid():
    """Find the connected 'DFM Bootloader' node's VID/PID via Get-PnpDevice."""
    try:
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-PnpDevice | Where-Object { $_.FriendlyName -eq 'DFM Bootloader' } "
                "| Select-Object -First 1 -ExpandProperty InstanceId",
            ],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return None
    m = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", out.stdout or "")
    if not m:
        return None
    return int(m.group(1), 16), int(m.group(2), 16)


if __name__ == "__main__":
    sys.exit(main())
