"""
Intel HEX file parser.

Parses .hex files produced by Keil MDK, extracts contiguous
programmable blocks suitable for flash programming.
"""
import re
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class HexBlock:
    """A contiguous block of data with a start address."""
    address: int
    data: bytes


def parse_hex(filepath: str) -> List[HexBlock]:
    """Parse an Intel HEX file, return sorted contiguous blocks."""
    records: List[Tuple[int, bytes]] = []
    extended_addr = 0

    with open(filepath, "r", encoding="ascii", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line.startswith(":"):
                continue

            byte_count = int(line[1:3], 16)
            address    = int(line[3:7], 16)
            rec_type   = int(line[7:9], 16)
            data_end   = 9 + byte_count * 2
            data       = bytes.fromhex(line[9:data_end])

            if rec_type == 0x00:       # Data record
                full_addr = extended_addr + address
                records.append((full_addr, data))
            elif rec_type == 0x01:     # End of file
                break
            elif rec_type == 0x02:     # Extended segment address
                extended_addr = int.from_bytes(data, "big") << 4
            elif rec_type == 0x04:     # Extended linear address
                extended_addr = int.from_bytes(data, "big") << 16
            elif rec_type == 0x05:     # Start linear address
                pass

    return _merge_blocks(records)


def parse_bin(filepath: str, base_addr: int = 0x0800C000) -> List[HexBlock]:
    """Parse a raw .bin file into a single block."""
    with open(filepath, "rb") as f:
        data = f.read()
    # Pad to half-word boundary
    if len(data) % 2:
        data += b"\xFF"
    return [HexBlock(address=base_addr, data=data)]


def _merge_blocks(records: List[Tuple[int, bytes]]) -> List[HexBlock]:
    """Merge sorted records into contiguous blocks."""
    if not records:
        return []
    records.sort(key=lambda r: r[0])
    blocks: List[HexBlock] = []
    cur_addr, cur_data = records[0]
    cur_data = bytearray(cur_data)

    for addr, data in records[1:]:
        gap = addr - (cur_addr + len(cur_data))
        if gap == 0:
            cur_data.extend(data)
        elif 0 < gap <= 1024:
            # Small gap: pad with 0xFF
            cur_data.extend(b"\xFF" * gap)
            cur_data.extend(data)
        else:
            blocks.append(HexBlock(address=cur_addr, data=bytes(cur_data)))
            cur_addr = addr
            cur_data = bytearray(data)

    blocks.append(HexBlock(address=cur_addr, data=bytes(cur_data)))
    return blocks
