# -*- coding: utf-8 -*-
"""
USB transport layer: HID, CDC Serial, and WinUSB backends.

Each transport implements: open(), send(data) -> int, recv(timeout_ms) -> bytes, close()
"""
import time
import queue
import threading
import ctypes
import winreg
from ctypes import wintypes
from abc import ABC, abstractmethod
from typing import Optional, List

# Device identities: the running application (programmer firmware) and the
# bootloader share the same VID/PID (0x16C0:0x05DF, AVR-Doper compatible).
# The tool distinguishes the two by HID product string:
#   bootloader = "DFM Bootloader", application = "DFM Programmer".
APP_VID = 0x16C0
APP_PID = 0x05DF
BOOT_VID = 0x16C0
BOOT_PID = 0x05DF
APP_PRODUCT = "DFM Programmer"
BOOT_PRODUCT = "DFM Bootloader"


def device_mode(vid: int = 0x16C0, pid: int = 0x05DF):
    """Return 'bootloader', 'app', or None based on the HID product string."""
    try:
        import hid
        for d in hid.enumerate(vid, pid):
            ps = d.get("product_string") or ""
            if BOOT_PRODUCT in ps:
                return "bootloader"
            if APP_PRODUCT in ps:
                return "app"
    except ImportError:
        pass
    return None


def set_cdc_latency_timer(vid: int, pid: int, value_ms: int = 1) -> bool:
    """
    Best-effort: lower the Windows usbser.sys LatencyTimer (default 16 ms) for
    the CDC device with the given VID:PID.

    The 16 ms default adds ~16 ms to EVERY response read on a CDC COM port,
    which roughly doubles the round-trip time of this request/response protocol
    compared to WinUSB. Writing HKLM needs admin rights; returns True when at
    least one device instance was updated.
    """
    try:
        import winreg
    except ImportError:
        return False
    base = f"SYSTEM\\CurrentControlSet\\Enum\\USB\\VID_{vid:04X}&PID_{pid:04X}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as dev:
            inst_count = winreg.QueryInfoKey(dev)[0]
    except OSError:
        return False

    updated = False
    for i in range(inst_count):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as dev:
                inst = winreg.EnumKey(dev, i)
        except OSError:
            continue
        params = base + "\\" + inst + "\\Device Parameters"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, params, 0,
                                winreg.KEY_READ | winreg.KEY_WRITE) as pk:
                winreg.SetValueEx(pk, "LatencyTimer", 0, winreg.REG_DWORD, value_ms)
                updated = True
        except PermissionError:
            return updated
        except OSError:
            continue
    return updated

# ────────────────────────────────────────────────────────
#  Abstract base
# ────────────────────────────────────────────────────────

class UsbTransport(ABC):
    @abstractmethod
    def open(self) -> bool: ...
    @abstractmethod
    def send(self, data: bytes) -> int: ...
    @abstractmethod
    def recv(self, timeout_ms: int = 2000) -> bytes: ...
    @abstractmethod
    def close(self) -> None: ...

# ────────────────────────────────────────────────────────
#  HID transport over EP1 interrupt IN/OUT (requires: pip install hidapi)
# ────────────────────────────────────────────────────────

class HidTransport(UsbTransport):
    VID = 0x16C0
    PID = 0x05DF
    # Report ID -> (full report size incl. ID byte, STK payload bytes).
    # Report 1 = 15 bytes total (13 payload), Report 2 = 31 bytes total
    # (29 payload); both travel over EP1 interrupt IN/OUT (32-byte MPS).
    REPORTS = {
        1: (15, 13),
        2: (31, 29),
    }
    # Buffer large enough for the longest report; hid_read returns one
    # whole report (including the report ID byte) per call.
    READ_BUF_SIZE = 64

    def __init__(self, vid: int = None, pid: int = None, product: str = None):
        self.vid = vid or self.VID
        self.pid = pid or self.PID
        self.product = product
        self._dev = None

    def open(self) -> bool:
        try:
            import hid
            for d in hid.enumerate(self.vid, self.pid):
                if d["usage_page"] != 0xFF00:
                    continue
                if self.product and self.product not in (d.get("product_string") or ""):
                    continue
                self._dev = hid.device()
                self._dev.open_path(d["path"])
                return True
        except (ImportError, OSError):
            pass
        return False

    @staticmethod
    def _pick_report(payload_len: int):
        """Pick the smallest report that fits payload_len (clamped to 29)."""
        for report_id, (report_size, max_payload) in HidTransport.REPORTS.items():
            if payload_len <= max_payload:
                return report_id, report_size, max_payload
        report_size, max_payload = HidTransport.REPORTS[2]
        return 2, report_size, max_payload

    def send(self, data: bytes) -> int:
        if self._dev is None:
            return 0
        sent = 0
        offset = 0
        while offset < len(data):
            remaining = len(data) - offset
            report_id, report_size, max_payload = self._pick_report(remaining)
            chunk = data[offset:offset + max_payload]
            report = bytes([report_id, len(chunk)]) + chunk
            report += b"\x00" * (report_size - len(report))
            self._dev.write(report)
            sent += len(chunk)
            offset += len(chunk)
        return sent

    def recv(self, timeout_ms: int = 2000) -> bytes:
        if self._dev is None:
            return b""
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            remaining_ms = int((deadline - time.time()) * 1000)
            if remaining_ms <= 0:
                return b""
            try:
                report = bytes(self._dev.read(self.READ_BUF_SIZE, remaining_ms))
            except OSError:
                return b""
            if len(report) >= 2 and report[0] in self.REPORTS:
                report_size, max_payload = self.REPORTS[report[0]]
                payload_len = min(report[1], max_payload, max(0, len(report) - 2))
                if payload_len:
                    return report[2:2 + payload_len]
        return b""

    def close(self) -> None:
        if self._dev:
            self._dev.close()
            self._dev = None

    @staticmethod
    def list_devices(vid: int = None, pid: int = None) -> List[str]:
        try:
            import hid
            v = vid or HidTransport.VID
            p = pid or HidTransport.PID
            return [d["path"].decode() if isinstance(d["path"], bytes) else d["path"]
                    for d in hid.enumerate(v, p)]
        except ImportError:
            return []

# ────────────────────────────────────────────────────────
#  Serial (CDC) transport  (requires: pip install pyserial)
# ────────────────────────────────────────────────────────

class SerialTransport(UsbTransport):
    def __init__(self, port: str = "", baudrate: int = 115200):
        self.port = port
        self.baudrate = baudrate
        self._ser = None
        self._rx_queue = None
        self._stop_reader = False
        self._reader = None

    def open(self) -> bool:
        try:
            import serial
            # Keep a blocking read pending at all times via a background thread:
            # usbser.sys otherwise batches received data for up to LatencyTimer
            # (default 16 ms) before the app sees it, which slows every response
            # read by ~16 ms and roughly doubles CDC round-trip time.
            self._ser = serial.Serial(self.port, self.baudrate, timeout=0.1, write_timeout=0.5)
            self._rx_queue = queue.Queue()
            self._stop_reader = False
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
            return self._ser.is_open
        except (ImportError, OSError):
            return False

    def _read_loop(self):
        while not self._stop_reader:
            try:
                # read(1) returns as soon as the first byte arrives (the
                # timeout only bounds the wait). Reading a large count at once
                # would block the whole timeout, because Windows completes a
                # read only when the full request is satisfied or the timeout
                # expires. After the first byte, grab everything buffered.
                first = self._ser.read(1)
                if not first:
                    continue
                chunk = bytearray(first)
                waiting = getattr(self._ser, "in_waiting", 0)
                if waiting:
                    chunk += self._ser.read(waiting)
                self._rx_queue.put(bytes(chunk))
            except Exception:
                break

    def send(self, data: bytes) -> int:
        if self._ser is None:
            return 0
        return self._ser.write(data)

    def recv(self, timeout_ms: int = 2000) -> bytes:
        if self._rx_queue is None:
            return b""
        buf = bytearray()
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            try:
                chunk = self._rx_queue.get(timeout=0.005)
                buf.extend(chunk)
            except queue.Empty:
                if buf:
                    break
        return bytes(buf)

    def close(self) -> None:
        self._stop_reader = True
        if self._reader is not None:
            self._reader.join(timeout=0.5)
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    @staticmethod
    def list_ports(vid: int = None, pid: int = None) -> List[str]:
        try:
            import serial.tools.list_ports
            result = []
            for p in serial.tools.list_ports.comports():
                if vid is not None and p.vid != vid:
                    continue
                if pid is not None and p.pid != pid:
                    continue
                result.append(p.device)
            return result
        except ImportError:
            return []

# ────────────────────────────────────────────────────────
#  WinUSB transport  (uses ctypes — no extra deps)
# ────────────────────────────────────────────────────────

class WinUsbTransport(UsbTransport):
    VID = 0x16C0
    PID = 0x05DF
    OUT_PIPE = 0x04
    IN_PIPE = 0x84
    PIPE_TRANSFER_TIMEOUT = 0x03

    # Fallback GUIDs to search when the installed driver does not expose
    # DeviceInterfaceGUIDs in the registry.
    _GUIDS = [
        "{9B0D1CA8-2D68-4BA2-9E72-401055510001}",
        "{9F08D6F6-9CC2-4F2F-8BFB-69E0743D31B9}",
        # GUID registered by the in-box "WinUSB device" picker (winusb.inf).
        "{dee824ef-729b-4a0e-9c14-b7117d33a817}",
    ]

    def __init__(self, vid: int = None, pid: int = None):
        self.vid = vid or self.VID
        self.pid = pid or self.PID
        self._hdev = None
        self._whandle = None
        self.last_error = 0
        self.last_error_where = ""

    def open(self) -> bool:
        path = self._find_device()
        if not path:
            self._set_last_error("find_device", 0)
            return False
        kr = ctypes.windll.kernel32
        kr.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kr.CreateFileW.restype = wintypes.HANDLE
        kr.CloseHandle.argtypes = [wintypes.HANDLE]
        kr.CloseHandle.restype = wintypes.BOOL
        h = kr.CreateFileW(path, 0xC0000000, 3, None, 3, 0x40000000, None)
        if h == wintypes.HANDLE(-1).value:
            self._set_last_error("CreateFileW", kr.GetLastError())
            return False
        wu = self._get_winusb()
        wh = wintypes.HANDLE()
        if not wu.WinUsb_Initialize(h, ctypes.byref(wh)):
            self._set_last_error("WinUsb_Initialize", kr.GetLastError())
            kr.CloseHandle(h)
            return False
        self._hdev = h
        self._whandle = wh
        self._set_last_error("", 0)
        self._set_pipe_timeout(self.IN_PIPE, 2000)
        self._set_pipe_timeout(self.OUT_PIPE, 2000)
        return True

    def send(self, data: bytes) -> int:
        if self._whandle is None:
            self._set_last_error("WinUsb_WritePipe", 0)
            return 0
        wu = self._get_winusb()
        kr = ctypes.windll.kernel32
        sent = wintypes.DWORD()
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        ok = wu.WinUsb_WritePipe(self._whandle, self.OUT_PIPE,
                                 buf, len(data),
                                 ctypes.byref(sent), None)
        if not ok:
            self._set_last_error("WinUsb_WritePipe", kr.GetLastError())
            return 0
        self._set_last_error("", 0)
        return sent.value if ok else 0

    def recv(self, timeout_ms: int = 2000) -> bytes:
        if self._whandle is None:
            self._set_last_error("WinUsb_ReadPipe", 0)
            return b""
        self._set_pipe_timeout(self.IN_PIPE, timeout_ms)
        wu = self._get_winusb()
        kr = ctypes.windll.kernel32
        buf = (ctypes.c_ubyte * 1024)()
        got = wintypes.DWORD()
        ok = wu.WinUsb_ReadPipe(self._whandle, self.IN_PIPE,
                                buf, len(buf),
                                ctypes.byref(got), None)
        if ok:
            self._set_last_error("", 0)
            return bytes(buf[:got.value])
        err = kr.GetLastError()
        if err in (121, 1460):
            # Read timed out (semaphore timeout / pending): abort the pipe so
            # the next read starts from a clean state and any in-flight packet
            # can be re-read by the device-side retry logic.
            try:
                wu.WinUsb_AbortPipe(self._whandle, self.IN_PIPE)
            except Exception:
                pass
            self._set_last_error("WinUsb_ReadPipe", err)
            return b""
        self._set_last_error("WinUsb_ReadPipe", err)
        return b""

    def close(self) -> None:
        if self._whandle:
            self._get_winusb().WinUsb_Free(self._whandle)
            self._whandle = None
        if self._hdev:
            ctypes.windll.kernel32.CloseHandle(self._hdev)
            self._hdev = None

    def _find_device(self) -> Optional[str]:
        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD),
                ("Reserved", ctypes.c_void_p),
            ]

        setupapi = ctypes.windll.setupapi
        ole32 = ctypes.windll.ole32
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
        detail_cb_size = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
        path_offset = ctypes.sizeof(wintypes.DWORD)
        flags = 0x02 | 0x10  # DIGCF_PRESENT | DIGCF_DEVICEINTERFACE

        setupapi.SetupDiGetClassDevsW.argtypes = [
            ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD
        ]
        setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
        setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID),
            wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)
        ]
        setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
        setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
        setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
        setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
        ole32.CLSIDFromString.argtypes = [wintypes.LPCOLESTR, ctypes.POINTER(GUID)]
        ole32.CLSIDFromString.restype = wintypes.LONG

        guid_texts = self._read_interface_guids()
        # Scan the DeviceClasses hive for the interface GUID actually
        # registered for this VID/PID/MI_03. This works even when
        # DeviceInterfaceGUIDs is missing or does not match the registration.
        for g in self._scan_device_classes():
            if g not in guid_texts:
                guid_texts.append(g)
        for fallback_guid in self._GUIDS:
            if fallback_guid not in guid_texts:
                guid_texts.append(fallback_guid)

        for guid_text in guid_texts:
            guid = GUID()
            if ole32.CLSIDFromString(guid_text, ctypes.byref(guid)) != 0:
                continue
            h = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, flags)
            if h == INVALID_HANDLE_VALUE:
                continue
            idx = 0
            while True:
                d = SP_DEVICE_INTERFACE_DATA()
                d.cbSize = ctypes.sizeof(d)
                if not setupapi.SetupDiEnumDeviceInterfaces(
                    h, None, ctypes.byref(guid), idx, ctypes.byref(d)
                ):
                    break
                idx += 1
                req = wintypes.DWORD()
                setupapi.SetupDiGetDeviceInterfaceDetailW(
                    h, ctypes.byref(d), None, 0, ctypes.byref(req), None
                )
                if req.value == 0:
                    continue
                buf = (ctypes.c_byte * req.value)()
                ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = detail_cb_size
                if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    h, ctypes.byref(d), buf, req, None, None
                ):
                    continue
                path = ctypes.wstring_at(ctypes.addressof(buf) + path_offset)
                lo = path.lower()
                if (
                    f"vid_{self.vid:04x}" in lo
                    and f"pid_{self.pid:04x}" in lo
                    and "mi_03" in lo
                ):
                    setupapi.SetupDiDestroyDeviceInfoList(h)
                    return path
            setupapi.SetupDiDestroyDeviceInfoList(h)
        return None

    def _scan_device_classes(self) -> List[str]:
        """Return interface GUIDs registered for this device by scanning
        HKLM\\SYSTEM\\CurrentControlSet\\Control\\DeviceClasses."""
        needle = f"vid_{self.vid:04x}&pid_{self.pid:04x}&mi_03"
        guids = []
        base = r"SYSTEM\CurrentControlSet\Control\DeviceClasses"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as top:
                count = winreg.QueryInfoKey(top)[0]
                for i in range(count):
                    guid = winreg.EnumKey(top, i)
                    try:
                        with winreg.OpenKey(top, guid) as gk:
                            inst_count = winreg.QueryInfoKey(gk)[0]
                            for j in range(inst_count):
                                inst = winreg.EnumKey(gk, j)
                                if needle in inst.lower():
                                    guids.append(guid)
                                    break
                    except OSError:
                        continue
        except OSError:
            pass
        return guids

    def _read_interface_guids(self) -> List[str]:
        path = (
            rf"SYSTEM\CurrentControlSet\Enum\USB\VID_{self.vid:04X}&PID_{self.pid:04X}&MI_03"
        )
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as parent:
                subkey_count = winreg.QueryInfoKey(parent)[0]
                for idx in range(subkey_count):
                    instance_id = winreg.EnumKey(parent, idx)
                    params_path = path + "\\" + instance_id + "\\Device Parameters"
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, params_path) as params:
                            value, _ = winreg.QueryValueEx(params, "DeviceInterfaceGUIDs")
                    except OSError:
                        continue
                    if isinstance(value, str):
                        return [value]
                    if isinstance(value, list):
                        return list(value)
        except OSError:
            pass
        return []

    def _set_pipe_timeout(self, pipe_id: int, timeout_ms: int) -> None:
        if self._whandle is None:
            return
        wu = self._get_winusb()
        value = wintypes.ULONG(timeout_ms)
        wu.WinUsb_SetPipePolicy(
            self._whandle,
            pipe_id,
            self.PIPE_TRANSFER_TIMEOUT,
            ctypes.sizeof(value),
            ctypes.byref(value),
        )

    def _set_last_error(self, where: str, code: int) -> None:
        self.last_error_where = where
        self.last_error = code

    def describe_last_error(self) -> str:
        if not self.last_error_where:
            return ""
        if self.last_error == 0:
            return self.last_error_where
        return f"{self.last_error_where} failed (Win32={self.last_error})"

    @staticmethod
    def _get_winusb():
        wu = ctypes.windll.winusb
        wu.WinUsb_Initialize.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE)]
        wu.WinUsb_Initialize.restype = wintypes.BOOL
        wu.WinUsb_Free.argtypes = [wintypes.HANDLE]
        wu.WinUsb_Free.restype = wintypes.BOOL
        wu.WinUsb_WritePipe.argtypes = [
            wintypes.HANDLE, ctypes.c_ubyte, ctypes.POINTER(ctypes.c_ubyte),
            wintypes.ULONG, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        wu.WinUsb_WritePipe.restype = wintypes.BOOL
        wu.WinUsb_ReadPipe.argtypes = [
            wintypes.HANDLE, ctypes.c_ubyte, ctypes.POINTER(ctypes.c_ubyte),
            wintypes.ULONG, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        wu.WinUsb_ReadPipe.restype = wintypes.BOOL
        wu.WinUsb_AbortPipe.argtypes = [wintypes.HANDLE, ctypes.c_ubyte]
        wu.WinUsb_AbortPipe.restype = wintypes.BOOL
        wu.WinUsb_SetPipePolicy.argtypes = [
            wintypes.HANDLE, ctypes.c_ubyte, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p
        ]
        wu.WinUsb_SetPipePolicy.restype = wintypes.BOOL
        return wu


def wait_for_device(vid: int, pid: int, product: str = None,
                    timeout_ms: int = 15000, poll_ms: int = 300) -> bool:
    """
    Poll until a device with the given VID:PID (and optional HID product
    string) can be opened.

    Used to wait for the bootloader to re-enumerate after the application has
    been asked to reboot for a firmware upgrade. With a unified VID/PID the
    product string is required to tell the bootloader apart from the app.
    """
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        hid_dev = HidTransport(vid=vid, pid=pid, product=product)
        if hid_dev.open():
            hid_dev.close()
            return True
        if product is not None:
            time.sleep(poll_ms / 1000.0)
            continue
        win_dev = WinUsbTransport(vid=vid, pid=pid)
        if win_dev.open():
            win_dev.close()
            return True
        if SerialTransport.list_ports(vid=vid, pid=pid):
            return True
        time.sleep(poll_ms / 1000.0)
    return False
