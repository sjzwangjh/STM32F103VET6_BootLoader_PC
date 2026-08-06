"""
STK500v2 protocol frame builder / parser.

Wire format:
  STX(0x1B) | SEQ | SIZE_HI | SIZE_LO | TOKEN(0x0E) | BODY... | XOR
"""
from dataclasses import dataclass
from typing import Optional, Tuple

STK_STX   = 0x1B
STK_TOKEN = 0x0E

CMD_SIGN_ON        = 0x01
CMD_LOAD_ADDRESS   = 0x06
CMD_FIRMWARE_UPGRADE = 0x07
CMD_ENTER_PROGMODE = 0x10
CMD_LEAVE_PROGMODE = 0x11
CMD_CHIP_ERASE     = 0x12
CMD_PROGRAM_FLASH  = 0x13
CMD_READ_FLASH     = 0x14
CMD_READ_SIGNATURE = 0x1B
CMD_READ_VERSION   = 0x1C
CMD_START_APP      = 0x1D

STATUS_OK      = 0x00
STATUS_FAILED  = 0x01
STATUS_UNKNOWN = 0x03

FIRMWARE_UPGRADE_MAGIC = b"\xA5\x5A"


@dataclass
class StkResponse:
    seq: int
    status: int
    data: bytes


def build_frame(seq: int, cmd: int, payload: bytes = b"") -> bytes:
    """Build a complete STK500v2 request frame."""
    body_len = 1 + len(payload)            # cmd byte + data
    frame = bytearray()
    frame.append(STK_STX)
    frame.append(seq & 0xFF)
    frame.append((body_len >> 8) & 0xFF)
    frame.append(body_len & 0xFF)
    frame.append(STK_TOKEN)
    frame.append(cmd)
    frame.extend(payload)
    # Append XOR checksum
    xor = 0
    for b in frame:
        xor ^= b
    frame.append(xor)
    return bytes(frame)


def build_load_address(seq: int, addr: int) -> bytes:
    """CMD_LOAD_ADDRESS frame."""
    payload = bytes([(addr >> 24) & 0xFF, (addr >> 16) & 0xFF,
                     (addr >> 8) & 0xFF, addr & 0xFF])
    return build_frame(seq, CMD_LOAD_ADDRESS, payload)


def build_program_flash(seq: int, data: bytes) -> bytes:
    """CMD_PROGRAM_FLASH frame.  Payload = len_hi len_lo data..."""
    n = len(data)
    payload = bytes([(n >> 8) & 0xFF, n & 0xFF]) + data
    return build_frame(seq, CMD_PROGRAM_FLASH, payload)


def build_read_flash(seq: int, length: int) -> bytes:
    """CMD_READ_FLASH frame."""
    payload = bytes([(length >> 8) & 0xFF, length & 0xFF])
    return build_frame(seq, CMD_READ_FLASH, payload)


def build_simple(seq: int, cmd: int) -> bytes:
    """Frame with no payload."""
    return build_frame(seq, cmd)


def build_firmware_upgrade(seq: int) -> bytes:
    """Frame that asks the running application to reboot into the bootloader."""
    return build_frame(seq, CMD_FIRMWARE_UPGRADE, FIRMWARE_UPGRADE_MAGIC)


def parse_response(raw: bytes) -> Optional[StkResponse]:
    """
    Parse a STK500v2 response frame.
    Returns StkResponse or None on parse error.
    """
    if len(raw) < 6:
        return None
    if raw[0] != STK_STX or raw[4] != STK_TOKEN:
        return None
    seq       = raw[1]
    body_len  = (raw[2] << 8) | raw[3]
    if 5 + body_len + 1 > len(raw):
        return None
    status    = raw[5]
    data_end  = 5 + body_len
    data      = raw[6:data_end]
    checksum  = raw[data_end]
    # Verify XOR
    xor = 0
    for b in raw[:data_end + 1]:
        xor ^= b
    if xor != 0:
        return None
    return StkResponse(seq=seq, status=status, data=data)


def find_response(stream: bytes, start: int = 0) -> Tuple[Optional[StkResponse], int]:
    """
    Scan a byte stream for a complete STK500 response.
    Returns (response, bytes_consumed).
    """
    idx = stream.find(STK_STX, start)
    if idx < 0:
        return None, len(stream)
    # Need at least 6 bytes after STX
    if idx + 6 > len(stream):
        return None, idx
    body_len = (stream[idx + 2] << 8) | stream[idx + 3]
    frame_end = idx + 5 + body_len + 1   # header(5) + body + xor(1)
    if frame_end > len(stream):
        return None, idx                   # incomplete
    resp = parse_response(stream[idx:frame_end])
    return resp, frame_end
