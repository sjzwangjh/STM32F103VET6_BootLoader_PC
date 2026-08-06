"""
High-level flash programmer using STK500v2 protocol.
"""
import time
import struct
from typing import Optional, Callable

from onlineUpdate import (
    StkResponse, build_simple, build_load_address, build_firmware_upgrade,
    build_program_flash, build_read_flash, find_response,
    CMD_SIGN_ON, CMD_LOAD_ADDRESS, CMD_FIRMWARE_UPGRADE, CMD_ENTER_PROGMODE, CMD_LEAVE_PROGMODE,
    CMD_CHIP_ERASE, CMD_READ_VERSION, CMD_START_APP, STATUS_OK,
)
from hex_parser import HexBlock
from usb_transport import UsbTransport, SerialTransport


class ProgrammerError(Exception):
    pass


class FlashProgrammer:
    PAGE_SIZE = 256

    def __init__(self, transport: UsbTransport, progress_cb: Callable = None):
        self._t = transport
        self._seq = 0
        self._progress = progress_cb  # callback(n, total)

    # 鈹€鈹€ helpers 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFF
        return self._seq

    def _send_cmd(self, cmd: int, payload: bytes = b"", timeout_ms: int = 3000) -> StkResponse:
        seq = self._next_seq()
        if cmd == CMD_ENTER_PROGMODE:
            frame = build_simple(seq, cmd)
        elif cmd == CMD_LOAD_ADDRESS:
            frame = payload  # already built
        elif cmd == CMD_CHIP_ERASE:
            frame = build_simple(seq, cmd)
        elif cmd == CMD_LEAVE_PROGMODE:
            frame = build_simple(seq, cmd)
        else:
            frame = build_simple(seq, cmd)  # fallback

        # For program/read, caller passes payload differently
        if isinstance(payload, bytes) and len(payload) > 0:
            if cmd not in (CMD_LOAD_ADDRESS,):
                frame = payload  # pre-built frame

        sent = self._t.send(frame)
        if sent <= 0:
            raise ProgrammerError(self._format_send_error(cmd))
        rxbuf = b""
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            chunk = self._t.recv(timeout_ms)
            if chunk:
                rxbuf += chunk
            resp, _ = find_response(rxbuf)
            if resp is not None:
                return resp
            if not chunk:
                time.sleep(0.005)
        raise ProgrammerError(self._format_timeout(cmd))

    def _raw_send(self, raw_bytes: bytes, timeout_ms: int = 3000) -> StkResponse:
        """Send a pre-built frame and read response."""
        cmd = raw_bytes[5] if len(raw_bytes) > 5 else 0
        sent = self._t.send(raw_bytes)
        if sent <= 0:
            raise ProgrammerError(self._format_send_error(cmd))
        rxbuf = b""
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            chunk = self._t.recv(timeout_ms)
            if chunk:
                rxbuf += chunk
            resp, _ = find_response(rxbuf)
            if resp is not None:
                return resp
            if not chunk:
                time.sleep(0.005)
        raise ProgrammerError(self._format_timeout(cmd))

    def _format_timeout(self, cmd: int) -> str:
        msg = f"Timeout waiting for response (cmd=0x{cmd:02X})"
        describe = getattr(self._t, "describe_last_error", None)
        if callable(describe):
            detail = describe()
            if detail:
                msg += f"; last transport error: {detail}"
        return msg

    def _format_send_error(self, cmd: int) -> str:
        msg = f"Failed to send cmd=0x{cmd:02X}"
        describe = getattr(self._t, "describe_last_error", None)
        if callable(describe):
            detail = describe()
            if detail:
                msg += f"; last transport error: {detail}"
        return msg

    # 鈹€鈹€ high-level API 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def connect(self) -> bool:
        """Sign-on handshake."""
        try:
            resp = self._raw_send(build_simple(self._next_seq(), CMD_SIGN_ON))
            return resp.status == STATUS_OK
        except ProgrammerError:
            return False

    def read_version(self) -> str:
        """Handshake read of bootloader version string."""
        resp = self._raw_send(build_simple(self._next_seq(), CMD_READ_VERSION))
        if resp.status != STATUS_OK:
            raise ProgrammerError(f"READ_VERSION failed (status={resp.status:#04X})")
        try:
            version = resp.data.decode("ascii").strip("\x00").strip()
        except UnicodeDecodeError as exc:
            raise ProgrammerError("Bootloader version is not valid ASCII") from exc
        if not version:
            raise ProgrammerError("Bootloader version is empty")
        return version

    def request_firmware_upgrade(self, timeout_ms: int = 3000) -> bool:
        """Ask the running application to reboot into the bootloader."""
        resp = self._raw_send(
            build_firmware_upgrade(self._next_seq()), timeout_ms=timeout_ms)
        # The running application echoes the command byte before the status
        # (response body = [cmd, status]); the bootloader replies body = [status].
        if resp.status == STATUS_OK:
            return True
        if resp.status == CMD_FIRMWARE_UPGRADE and len(resp.data) >= 1 and resp.data[0] == STATUS_OK:
            return True
        return False

    def enter_progmode(self) -> None:
        resp = self._send_cmd(CMD_ENTER_PROGMODE)
        if resp.status != STATUS_OK:
            raise ProgrammerError(f"ENTER_PROGMODE failed (status={resp.status:#04X})")

    def leave_progmode(self) -> None:
        self._send_cmd(CMD_LEAVE_PROGMODE)

    def chip_erase(self) -> None:
        # Application area spans 232 flash pages (0x0800C000-0x0807FFFF).
        # Full erase can take several seconds on STM32F103, so use a longer timeout.
        resp = self._send_cmd(CMD_CHIP_ERASE, timeout_ms=20000)
        if resp.status != STATUS_OK:
            raise ProgrammerError(f"CHIP_ERASE failed (status={resp.status:#04X})")

    def start_application(self) -> None:
        try:
            resp = self._send_cmd(CMD_START_APP, timeout_ms=1500)
        except Exception as exc:
            if isinstance(self._t, SerialTransport):
                return
            raise ProgrammerError(f"START_APP transport error: {exc}") from exc
        if resp.status != STATUS_OK:
            raise ProgrammerError(f"START_APP failed (status={resp.status:#04X})")

    def program_block(self, address: int, data: bytes) -> None:
        """Program one contiguous block."""
        # Set address
        frame = build_load_address(self._next_seq(), address)
        resp = self._raw_send(frame)
        if resp.status != STATUS_OK:
            raise ProgrammerError(f"LOAD_ADDRESS failed at 0x{address:08X}")

        # Program in pages
        offset = 0
        while offset < len(data):
            chunk = data[offset:offset + self.PAGE_SIZE]
            frame = build_program_flash(self._next_seq(), chunk)
            resp = self._raw_send(frame)
            if resp.status != STATUS_OK:
                raise ProgrammerError(
                    f"PROGRAM_FLASH failed at 0x{address + offset:08X}")
            offset += len(chunk)
            if self._progress:
                self._progress(offset, len(data))

    def verify_block(self, address: int, data: bytes) -> bool:
        """Read back and compare."""
        frame = build_load_address(self._next_seq(), address)
        resp = self._raw_send(frame)
        if resp.status != STATUS_OK:
            return False

        offset = 0
        while offset < len(data):
            read_len = min(256, len(data) - offset)
            frame = build_read_flash(self._next_seq(), read_len)
            resp = self._raw_send(frame)
            if resp.status != STATUS_OK:
                return False
            if resp.data != data[offset:offset + read_len]:
                return False
            offset += read_len
        return True

    def program(self, blocks: list, verify: bool = True, phase_cb: Callable = None) -> None:
        """Full programming sequence: erase, program, verify, leave, start."""
        if phase_cb:
            phase_cb("enter")
        self.enter_progmode()
        try:
            total = sum(len(b.data) for b in blocks)
            done = 0
            if phase_cb:
                phase_cb("program")
            for blk in blocks:
                self.program_block(blk.address, blk.data)
                done += len(blk.data)
                if self._progress:
                    self._progress(done, total)

            if verify:
                if phase_cb:
                    phase_cb("verify")
                for blk in blocks:
                    if not self.verify_block(blk.address, blk.data):
                        raise ProgrammerError(
                            f"Verify failed at 0x{blk.address:08X}")
        finally:
            if phase_cb:
                phase_cb("leave")
            self.leave_progmode()

        if phase_cb:
            phase_cb("start")
        self.start_application()

