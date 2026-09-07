"""Application-firmware Handler configuration commands (STK500v2)."""
import struct
import time
from dataclasses import dataclass, astuple

from onlineUpdate import build_frame, parse_response, STK_STX, STK_TOKEN

CMD_GET_HANDLER_CONFIG = 0xA0
CMD_SET_HANDLER_CONFIG = 0xA1
CMD_GET_HANDLER_STATISTICS = 0xA2
CMD_RESET_HANDLER_STATISTICS = 0xA3
LEVEL_FIELDS = ("sotLevel", "eotLevel", "busyLevel", "passLevel", "ngLevel")
DELAY_FIELDS = ("delayMsBinToEot", "delayMsMinTestTime")
STATISTICS_FIELDS = (
    "realTotal", "realPassed", "realFaild",
    "logicTotal", "logicPassed", "logicFaild",
)


class HandlerProtocolError(Exception):
    pass


@dataclass(frozen=True)
class HandlerConfig:
    sotLevel: int
    eotLevel: int
    busyLevel: int
    passLevel: int
    ngLevel: int
    delayMsBinToEot: int
    delayMsMinTestTime: int

    def encode(self):
        for field in LEVEL_FIELDS:
            if type(getattr(self, field)) is not int or getattr(self, field) not in (0, 1):
                raise ValueError(f"{field} 必须为 0 或 1")
        for field in DELAY_FIELDS:
            value = getattr(self, field)
            if type(value) is not int or not 0 <= value <= 60000:
                raise ValueError(f"{field} 必须为 0–60000 ms 的整数")
        return struct.pack("<5B2H", *astuple(self))

    @classmethod
    def decode(cls, data):
        if len(data) != 9:
            raise HandlerProtocolError(f"配置应为 9 字节，实际为 {len(data)} 字节")
        config = cls(*struct.unpack("<5B2H", data))
        config.encode()  # Validate before any UI field is changed.
        return config


@dataclass(frozen=True)
class HandlerStatistics:
    realTotal: int
    realPassed: int
    realFaild: int
    logicTotal: int
    logicPassed: int
    logicFaild: int

    @classmethod
    def decode(cls, data):
        if len(data) != 24:
            raise HandlerProtocolError(f"统计数据应为 24 字节，实际为 {len(data)} 字节")
        return cls(*struct.unpack("<6I", data))


class HandlerClient:
    def __init__(self, transport, timeout_ms=3000):
        self.transport = transport
        self.timeout_ms = timeout_ms
        self.seq = 0

    def read_config(self):
        return HandlerConfig.decode(self._exchange(CMD_GET_HANDLER_CONFIG, b"", 9))

    def store_config(self, config):
        self._exchange(CMD_SET_HANDLER_CONFIG, config.encode(), 0)

    def read_statistics(self):
        return HandlerStatistics.decode(
            self._exchange(CMD_GET_HANDLER_STATISTICS, b"", 24))

    def reset_statistics(self):
        self._exchange(CMD_RESET_HANDLER_STATISTICS, b"", 0)

    def _exchange(self, cmd, payload, data_size):
        self.seq = (self.seq + 1) & 0xFF
        frame = build_frame(self.seq, cmd, payload)
        if self.transport.send(frame) != len(frame):
            raise HandlerProtocolError(f"0x{cmd:02X} 发送失败或发送不完整")
        deadline = time.monotonic() + self.timeout_ms / 1000
        buffer = b""
        while time.monotonic() < deadline:
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            chunk = self.transport.recv(remaining)
            buffer += chunk
            while buffer:
                start = buffer.find(bytes([STK_STX]))
                if start < 0:
                    buffer = b""
                    break
                buffer = buffer[start:]
                if len(buffer) < 5:
                    break
                size = int.from_bytes(buffer[2:4], "big")
                # Handler replies have command + status + at most 24 data bytes.
                if buffer[4] != STK_TOKEN or not 2 <= size <= 26:
                    buffer = buffer[1:]
                    continue
                if len(buffer) < size + 6:
                    break
                response = parse_response(buffer[:size + 6])
                if response is None:
                    buffer = buffer[1:]
                    continue
                buffer = buffer[size + 6:]
                # The shared parser calls the first body byte 'status'; the
                # application firmware puts the echoed command in that byte.
                if response.seq != self.seq or response.status != cmd:
                    continue
                status = response.data[0]
                if status != 0:
                    raise HandlerProtocolError(
                        f"0x{cmd:02X} 被下位机拒绝，状态码 0x{status:02X}")
                data = response.data[1:]
                if len(data) != data_size:
                    raise HandlerProtocolError(f"0x{cmd:02X} 应答参数长度错误")
                return data
            if not chunk:
                time.sleep(0.005)
        raise HandlerProtocolError(f"0x{cmd:02X} 等待有效应答超时（校验和/序号/命令不匹配或无响应）")
