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
from handler_protocol import (
    HandlerClient, HandlerConfig, LEVEL_FIELDS, DELAY_FIELDS, STATISTICS_FIELDS,
)
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
        root.minsize(900, 0)

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
        self._pages = ttk.Notebook(self.root)
        self._pages.pack(fill="both", expand=True, padx=10, pady=10)

        self._bootloader_page = ttk.Frame(self._pages, padding=10)
        self._handler_page = ttk.Frame(self._pages, padding=10)
        self._statistic_page = ttk.Frame(self._pages, padding=10)
        self._pages.add(self._bootloader_page, text="BootLoader")
        self._pages.add(self._handler_page, text="Handler")
        self._pages.add(self._statistic_page, text="Statistic")
        self._build_bootloader_page()
        self._build_handler_page()
        self._build_statistic_page()

    def _build_bootloader_page(self):
        f = self._bootloader_page
        f.columnconfigure(1, weight=1)

        # Row 0: File
        ttk.Label(f, text="Firmware:").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.file_path, width=45).grid(row=0, column=1, padx=4, sticky="ew")
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
        self._btn_reset = ttk.Button(bf, text="Reset", command=self._reset_device)
        self._btn_reset.pack(side="left", padx=4)
        self._btn_test = ttk.Button(bf, text="Connect Test", command=self._test_connect)
        self._btn_test.pack(side="left", padx=4)
        self._btn_prog = ttk.Button(bf, text="Program", command=self._start_program)
        self._btn_prog.pack(side="left", padx=4)
        ttk.Button(bf, text="Quit", command=self.root.destroy).pack(side="left", padx=4)

    def _build_handler_page(self):
        fields = (
            ("sotLevel", "SOT"),
            ("eotLevel", "EOT (reserved)"),
            ("busyLevel", "BUSY"),
            ("passLevel", "OK"),
            ("ngLevel", "NG"),
        )
        f = self._handler_page
        f.columnconfigure(3, weight=1, minsize=240)
        self.handler_inputs = {}
        # Initial display values; device values are loaded by the Read button.
        for row, (field, signal) in enumerate(fields):
            ttk.Label(f, text=f"{signal} ({field}):").grid(
                row=row, column=0, sticky="w", padx=(0, 20), pady=6)
            control = ttk.Combobox(
                f, values=("0 - Active low", "1 - Active high"),
                state="readonly", width=24)
            control.current(1)
            control.grid(row=row, column=1, sticky="w", pady=6)
            self.handler_inputs[field] = control

        delay_validation = (f.register(self._valid_handler_delay), "%P")
        for row, (field, default) in enumerate((
                ("delayMsBinToEot", 10),
                ("delayMsMinTestTime", 3000)), start=len(fields)):
            ttk.Label(f, text=f"{field}:").grid(
                row=row, column=0, sticky="w", padx=(0, 20), pady=6)
            control = ttk.Spinbox(
                f, from_=0, to=60000, increment=1, width=24,
                validate="key", validatecommand=delay_validation)
            control.set(str(default))
            control.grid(row=row, column=1, sticky="w", pady=6)
            ttk.Label(f, text="ms").grid(
                row=row, column=2, sticky="w", padx=(8, 0), pady=6)
            self.handler_inputs[field] = control

        buttons = ttk.Frame(f)
        buttons.grid(row=0, column=3, rowspan=7, padx=(32, 12))
        self._btn_handler_store = ttk.Button(
            buttons, text="存储", width=14, command=lambda: self._start_handler_action(True))
        self._btn_handler_store.pack(pady=10)
        self._btn_handler_read = ttk.Button(
            buttons, text="读取", width=14, command=lambda: self._start_handler_action(False))
        self._btn_handler_read.pack(pady=10)
        self._handler_status = ttk.Label(
            f, text="就绪", foreground="gray", wraplength=820)
        self._handler_status.grid(row=7, column=0, columnspan=4, sticky="w", pady=(10, 0))

    def _build_statistic_page(self):
        labels = (
            ("realTotal", "realTotal（实际测试总数）"),
            ("realPassed", "realPassed（实际通过数）"),
            ("realFaild", "realFaild（实际失败数）"),
            ("logicTotal", "logicTotal（逻辑测试总数）"),
            ("logicPassed", "logicPassed（逻辑通过数）"),
            ("logicFaild", "logicFaild（逻辑失败数）"),
        )
        f = self._statistic_page
        f.columnconfigure(1, weight=1)
        self.statistic_values = {}
        for row, (field, label) in enumerate(labels):
            ttk.Label(f, text=f"{label}:").grid(
                row=row, column=0, sticky="w", padx=(0, 20), pady=6)
            value = tk.StringVar(value="--")
            ttk.Entry(f, textvariable=value, width=22, state="readonly").grid(
                row=row, column=1, sticky="w", pady=6)
            self.statistic_values[field] = value

        buttons = ttk.Frame(f)
        buttons.grid(row=0, column=2, rowspan=len(labels), padx=(32, 12))
        self._btn_statistic_read = ttk.Button(
            buttons, text="读取", width=14,
            command=lambda: self._start_statistic_action(False))
        self._btn_statistic_read.pack(pady=10)
        self._btn_statistic_reset = ttk.Button(
            buttons, text="重置", width=14,
            command=lambda: self._start_statistic_action(True))
        self._btn_statistic_reset.pack(pady=10)
        self._statistic_status = ttk.Label(
            f, text="就绪", foreground="gray", wraplength=820)
        self._statistic_status.grid(
            row=len(labels), column=0, columnspan=3, sticky="w", pady=(10, 0))

    @staticmethod
    def _valid_handler_delay(value):
        # Allow an empty field while editing; constrain entered values to uint16 range.
        return value == "" or (
            value.isascii() and value.isdecimal()
            and len(value) <= 5 and int(value) <= 60000)

    # ── Actions ─────────────────────────────────────────

    def _start_handler_action(self, store):
        if self._running:
            return
        config = None
        if store:
            try:
                values = [self.handler_inputs[field].current() for field in LEVEL_FIELDS]
                for field in DELAY_FIELDS:
                    value = self.handler_inputs[field].get()
                    if not value or not self._valid_handler_delay(value):
                        raise ValueError(f"{field} 必须为 0–60000 ms 的整数")
                    values.append(int(value))
                config = HandlerConfig(*values)
                config.encode()
            except ValueError as exc:
                self._handler_status.configure(text=str(exc), foreground="red")
                return
        key = self.transport.get()
        self._handler_status.configure(
            text=f"正在通过 {key} {'存储' if store else '读取'}配置…", foreground="gray")
        self._start_device_task(self._do_handler_action, store, config, key)

    def _do_handler_action(self, store, config, key):
        transport = None
        try:
            if device_mode() == "bootloader":
                raise ProgrammerError("设备处于 BootLoader 模式，请先运行 Programmer 应用固件")
            transport = self._get_app_transport(key)
            if not transport.open():
                raise ProgrammerError("无法打开 Programmer 设备，请检查连接及传输方式")
            client = HandlerClient(transport)
            if store:
                client.store_config(config)
                result = None
                message = "存储成功（0xA1 应答成功）"
            else:
                result = client.read_config()
                message = "读取成功（0xA0），已更新全部 7 个参数"
            self.root.after(0, lambda: self._finish_handler_action(result, message))
        except Exception as exc:
            self.root.after(0, lambda error=str(exc): self._handler_status.configure(
                text=f"{'存储' if store else '读取'}失败：{error}", foreground="red"))
        finally:
            if transport is not None:
                try:
                    transport.close()
                except Exception:
                    pass

    def _finish_handler_action(self, config, message):
        if config is not None:
            for field in LEVEL_FIELDS:
                self.handler_inputs[field].current(getattr(config, field))
            for field in DELAY_FIELDS:
                self.handler_inputs[field].set(str(getattr(config, field)))
        self._handler_status.configure(text=message, foreground="green")

    def _start_statistic_action(self, reset):
        if self._running:
            return
        key = self.transport.get()
        action = "重置" if reset else "读取"
        self._statistic_status.configure(
            text=f"正在通过 {key} {action}统计参数…", foreground="gray")
        self._start_device_task(self._do_statistic_action, reset, key)

    def _do_statistic_action(self, reset, key):
        transport = None
        action = "重置" if reset else "读取"
        try:
            if device_mode() == "bootloader":
                raise ProgrammerError("设备处于 BootLoader 模式，请先运行 Programmer 应用固件")
            transport = self._get_app_transport(key)
            if not transport.open():
                raise ProgrammerError("无法打开 Programmer 设备，请检查连接及传输方式")
            client = HandlerClient(transport)
            if reset:
                client.reset_statistics()
                message = "重置成功（0xA3）：仅逻辑统计计数已清零"
            else:
                message = "读取成功（0xA2），已更新全部 6 个统计参数"
            result = client.read_statistics()
            self.root.after(0, lambda: self._finish_statistic_action(result, message))
        except Exception as exc:
            self.root.after(0, lambda error=str(exc): self._statistic_status.configure(
                text=f"{action}失败：{error}", foreground="red"))
        finally:
            if transport is not None:
                try:
                    transport.close()
                except Exception:
                    pass

    def _finish_statistic_action(self, statistics, message):
        for field in STATISTICS_FIELDS:
            self.statistic_values[field].set(str(getattr(statistics, field)))
        self._statistic_status.configure(text=message, foreground="green")

    def _start_device_task(self, action, *args):
        if self._running:
            return
        self._set_ui_state(True)

        def run():
            try:
                action(*args)
            finally:
                self.root.after(0, lambda: self._set_ui_state(False))

        threading.Thread(target=run, daemon=True).start()

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
        for button in (self._btn_prog, self._btn_reset, self._btn_test,
                       self._btn_handler_store, self._btn_handler_read,
                       self._btn_statistic_read, self._btn_statistic_reset):
            button["state"] = "disabled" if running else "normal"
        for field, control in self.handler_inputs.items():
            control["state"] = ("disabled" if running else
                                "readonly" if field in LEVEL_FIELDS else "normal")

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

    def _get_app_transport(self, key=None):
        """Build a transport that targets the RUNNING application firmware,
        using the currently selected interface type."""
        key = key or self.transport.get()
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
        self._start_device_task(self._do_program, fp)

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
        self._start_device_task(_run)

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
        self._start_device_task(_run)

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
