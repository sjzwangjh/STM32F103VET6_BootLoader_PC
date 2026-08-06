# -*- coding: utf-8 -*-
"""
STM32F103VE Bootloader — PC Upload Tool (tkinter GUI)

Select a .bin/.hex file, choose transport (HID / Serial / WinUSB),
and program the application area of the device.
"""
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from hex_parser import parse_hex, parse_bin
from flash_programmer import FlashProgrammer, ProgrammerError
from usb_transport import (
    HidTransport, SerialTransport, WinUsbTransport,
    APP_VID, APP_PID, BOOT_VID, BOOT_PID, wait_for_device,
    APP_PRODUCT, BOOT_PRODUCT, device_mode,
    set_cdc_latency_timer,
)

TRANSPORTS = {
    "HID":     ("HID (USB HID)",       HidTransport),
    "Serial":  ("Serial (CDC COM)",    SerialTransport),
    "WinUSB":  ("WinUSB (Bulk)",       WinUsbTransport),
}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("DFM STM32 Bootloader")
        root.resizable(False, False)

        self.file_path  = tk.StringVar()
        self.transport  = tk.StringVar(value="WinUSB")
        self.version_text = tk.StringVar(value="Device Version:")
        self.download_time_text = tk.StringVar(value="Download time: --")
        self.status_text = tk.StringVar(value="Ready")
        self._running   = False

        self._build_ui()
        self._refresh_ports()

    # ── UI ──────────────────────────────────────────────

    def _build_ui(self):
        f = ttk.Frame(self.root, padding=10)
        f.pack(fill="both", expand=True)

        # Row 0: File
        ttk.Label(f, text="Firmware:").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.file_path, width=45).grid(row=0, column=1, padx=4)
        ttk.Button(f, text="Browse...", command=self._browse).grid(row=0, column=2)

        # Row 1: Transport
        ttk.Label(f, text="Transport:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        tf = ttk.Frame(f)
        tf.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        for key, (label, _) in TRANSPORTS.items():
            ttk.Radiobutton(tf, text=label, variable=self.transport,
                            value=key, command=self._on_transport_change).pack(side="left", padx=4)

        # Row 2: Progress
        self._pbar = ttk.Progressbar(f, length=400, mode="determinate")
        self._pbar.grid(row=2, column=0, columnspan=3, pady=(10, 4), sticky="ew")

        # Row 3: Version + download time
        ttk.Label(f, textvariable=self.version_text, foreground="navy").grid(
            row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(f, textvariable=self.download_time_text, foreground="green").grid(
            row=3, column=2, sticky="e")

        # Row 4: Status
        self._status_label = ttk.Label(f, textvariable=self.status_text, foreground="gray")
        self._status_label.grid(
            row=4, column=0, columnspan=3, sticky="w")

        # Row 5: Buttons
        bf = ttk.Frame(f)
        bf.grid(row=5, column=0, columnspan=3, pady=(10, 0))
        ttk.Button(bf, text="Reset", command=self._reset_device).pack(side="left", padx=4)
        ttk.Button(bf, text="Connect Test", command=self._test_connect).pack(side="left", padx=4)
        self._btn_prog = ttk.Button(bf, text="Program", command=self._start_program)
        self._btn_prog.pack(side="left", padx=4)
        ttk.Button(bf, text="Quit", command=self.root.destroy).pack(side="left", padx=4)

    # ── Actions ─────────────────────────────────────────

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Select firmware file",
            filetypes=[("Firmware", "*.bin *.hex"), ("All", "*.*")])
        if path:
            self.file_path.set(path)

    def _on_transport_change(self):
        if self.transport.get() == "Serial":
            self._refresh_ports()

    def _refresh_ports(self, announce: bool = True):
        mode = device_mode()
        self._current_mode = mode
        if mode == "bootloader":
            ports = SerialTransport.list_ports(vid=BOOT_VID, pid=BOOT_PID)
            self._detected_port = ports[0] if ports else ""
        elif mode == "app":
            ports = SerialTransport.list_ports(vid=APP_VID, pid=APP_PID)
            self._detected_port = ports[0] if ports else ""
        else:
            self._detected_port = ""
        if announce:
            if mode == "bootloader":
                self._log(f"Bootloader mode: serial {self._detected_port or 'none'}")
            elif mode == "app":
                self._log(f"Application mode: serial {self._detected_port or 'none'}")
            else:
                self._log("No DFM device detected")

    def _set_ui_state(self, running: bool):
        self._running = running
        self._btn_prog["state"] = "disabled" if running else "normal"

    def _set_version(self, version: str):
        self.root.after(0, lambda v=version: self.version_text.set(f"Device Version: {v}"))

    def _clear_version(self):
        self.root.after(0, lambda: self.version_text.set("Device Version:"))

    def _get_transport(self):
        key = self.transport.get()
        cls  = TRANSPORTS[key][1]
        if key == "Serial":
            if device_mode() != "bootloader":
                raise ProgrammerError(
                    "Bootloader not detected. Click Reset first to enter bootloader mode.")
            ports = SerialTransport.list_ports(vid=BOOT_VID, pid=BOOT_PID)
            if not ports:
                raise ProgrammerError(
        "No bootloader serial port found (0x16C0/0x05DF). "
                    "Click Reset first to enter bootloader mode.")
            self._apply_latency_timer()
            return cls(port=ports[0])
        if key == "HID":
            return cls(product=BOOT_PRODUCT)
        return cls()

    def _get_app_transport(self):
        """Build a transport that targets the RUNNING application firmware,
        using the currently selected interface type."""
        key = self.transport.get()
        if key == "Serial":
            ports = SerialTransport.list_ports(vid=APP_VID, pid=APP_PID)
            if not ports:
                raise ProgrammerError(
                    "No application serial port found (0x16C0/0x05DF).")
            self._apply_latency_timer()
            return SerialTransport(port=ports[0])
        if key == "HID":
            return HidTransport(vid=APP_VID, pid=APP_PID, product=APP_PRODUCT)
        return WinUsbTransport(vid=APP_VID, pid=APP_PID)

    def _apply_latency_timer(self):
        """Try to lower the Windows CDC LatencyTimer (default 16 ms per read)
        so Serial transport round-trips are not ~16 ms slower than WinUSB."""
        for vid, pid in ((BOOT_VID, BOOT_PID), (APP_VID, APP_PID)):
            if set_cdc_latency_timer(vid, pid):
                self._log(
                    f"CDC LatencyTimer set to 1ms ({vid:04X}:{pid:04X}); "
                    "replug USB to take effect")
                return
        self._log(
            "Tip: run as admin to set CDC LatencyTimer=1ms, "
            "otherwise CDC reads add ~16ms per response")

    # ── Programming thread ─────────────────────────────

    def _start_program(self):
        fp = self.file_path.get().strip()
        if not fp or not os.path.exists(fp):
            messagebox.showerror("Error", "Select a valid firmware file.")
            return
        self._set_ui_state(True)
        threading.Thread(target=self._do_program, args=(fp,), daemon=True).start()

    def _do_program(self, filepath: str):
        t = None
        success = False
        start_time = time.time()
        self.root.after(0, lambda: self.download_time_text.set("Download time: ..."))
        try:
            self._log("Parsing file...")
            self._clear_version()
            ext = os.path.splitext(filepath)[1].lower()
            if ext == ".hex":
                blocks = parse_hex(filepath)
            else:
                blocks = parse_bin(filepath)

            if not blocks:
                raise ProgrammerError("No data found in file.")
            total_kb = sum(len(b.data) for b in blocks) / 1024
            self._log(f"Parsed {len(blocks)} block(s), {total_kb:.1f} KB")

            # Program the application directly through the bootloader.
            # Use the <Reset> button first if the device is still running the
            # application firmware (same VID/PID, different HID product string)
            # and needs to reboot.
            self._log("Opening device...")
            t = self._get_transport()
            if not t.open():
                raise ProgrammerError("Cannot open device. Check connection and driver.")

            prog = FlashProgrammer(t, progress_cb=self._on_progress)

            self._log("Handshake: reading bootloader version...")
            version = prog.read_version()
            self._set_version(version)

            self._log("Connecting...")
            if not prog.connect():
                raise ProgrammerError("Sign-on failed. Is bootloader running?")

            self._log("Programming...")
            prog.program(blocks, verify=True, phase_cb=self._on_phase)

            self._log("Done — programming successful!")
            success = True
            elapsed = time.time() - start_time
            self.root.after(
                0, lambda e=elapsed: self.download_time_text.set(f"Download time: {e:.1f} s"))
            self.root.after(0, lambda: self._pbar.configure(maximum=1000, value=1000))
        except ProgrammerError as e:
            elapsed = time.time() - start_time
            self.root.after(
                0, lambda e=elapsed: self.download_time_text.set(f"Download time: {e:.1f} s (failed)"))
            self._log_error(f"Error: {e}")
        except Exception as e:
            elapsed = time.time() - start_time
            self.root.after(
                0, lambda e=elapsed: self.download_time_text.set(f"Download time: {e:.1f} s (failed)"))
            self._log_error(f"Unexpected error: {e}")
        finally:
            if t is not None:
                try:
                    t.close()
                except Exception:
                    pass
            if not success:
                self._pbar["value"] = 0
            self.root.after(0, lambda: self._set_ui_state(False))
            self.root.after(0, lambda: self._refresh_ports(announce=False))

    def _test_connect(self):
        def _run():
            t = None
            try:
                self._log("Test: Reading bootloader version...")
                self._clear_version()
                t = self._get_transport()
                if not t.open():
                    self._log(
        "Test: Cannot open device (bootloader PID 0x16C0/0x05DF). "
                        "If you just clicked Reset, wait for re-enumeration and retry.")
                    return
                prog = FlashProgrammer(t)
                version = prog.read_version()
                self._log(f"Test: Version read OK, {version}")
                self._set_version(version)
            except Exception as e:
                self._log_error(f"Test error: {e}")
            finally:
                if t is not None:
                    try:
                        t.close()
                    except Exception:
                        pass
        threading.Thread(target=_run, daemon=True).start()

    def _reset_device(self):
        """Send STK_CMD_FIRMWARE_UPGRADE to the application through the
        currently selected interface, then wait for the bootloader (same
        VID/PID, HID product string differs) to re-enumerate."""
        def _run():
            t = None
            try:
                if device_mode() == "bootloader":
                    self._log("Reset: device is already in bootloader mode.")
                    self._refresh_ports(announce=False)
                    return
                key = self.transport.get()
                self._log(
                    f"Reset: opening application via {TRANSPORTS[key][0]}...")
                t = self._get_app_transport()
                if not t.open():
                    self._log(
                        "Reset: application not found; it may already be in bootloader mode.")
                    return
                prog = FlashProgrammer(t)
                if prog.request_firmware_upgrade():
                    self._log(
                        "Reset: upgrade command accepted; waiting for bootloader to re-enumerate...")
                else:
                    self._log("Reset: device rejected the upgrade command.")
                    return
            except Exception as e:
                self._log_error(f"Reset error: {e}")
                return
            finally:
                if t is not None:
                    try:
                        t.close()
                    except Exception:
                        pass
            if wait_for_device(BOOT_VID, BOOT_PID, product=BOOT_PRODUCT, timeout_ms=10000):
                self._refresh_ports(announce=False)
                self._log(
        "Reset: bootloader detected (0x16C0/0x05DF), "
                    f"serial {self._detected_port or 'none'}. You can run Connect Test.")
            else:
                self._log(
                    "Reset: bootloader not detected within 10s; check USB re-enumeration.")
        threading.Thread(target=_run, daemon=True).start()

    def _on_progress(self, n: int, total: int):
        value = int(900 * n / total) if total > 0 else 0
        self.root.after(0, lambda v=value: self._pbar.configure(maximum=1000, value=v))

    def _on_phase(self, phase: str):
        if phase == "enter":
            self._log("Entering programming mode...")
        elif phase == "program":
            self._log("Programming...")
        elif phase == "verify":
            self._log("Verifying...")
        elif phase == "leave":
            self._log("Finalizing update...")
        elif phase == "start":
            self._log("Starting application...")

    def _log(self, msg: str):
        self.root.after(
            0, lambda m=msg: (self.status_text.set(m),
                              self._status_label.configure(foreground="gray")))

    def _log_error(self, msg: str):
        self.root.after(
            0, lambda m=msg: (self.status_text.set(m),
                              self._status_label.configure(foreground="red")))


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
