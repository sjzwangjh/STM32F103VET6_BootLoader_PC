# STM32F103VET6 BootLoader PC Tool

PC-side firmware upload tool for STM32F103VET6 bootloader, supporting three USB transport methods via STK500v2 protocol.

## Architecture

```
main.py (tkinter GUI)
  ├── usb_transport.py     ─ HID / CDC Serial / WinUSB transport backends
  ├── onlineUpdate.py      ─ STK500v2 protocol (frame builder/parser)
  ├── flash_programmer.py  ─ Flash programming logic
  └── hex_parser.py        ─ HEX / BIN file parser
```

## Supported Transports

| Transport | Backend | Driver | Status |
|-----------|---------|--------|--------|
| **WinUSB** | raw WinUSB (via `winusb_dfm.inf`) | Custom INF | ✅ Recommended |
| **Serial (CDC)** | `pyserial` | Windows built-in | ✅ |
| **HID** | `hidapi` | Windows built-in | 🚧 In development |

> The device identifies as VID=0x16C0 PID=0x05DF. Bootloader product string: `DFM Bootloader`, Application: `DFM Programmer`.

## Requirements

```bash
pip install -r requirements.txt
```

Dependencies:
- `hidapi>=0.14.0`
- `pyserial>=3.5`

## Usage

### GUI Mode

```bash
python main.py
```

1. Select firmware file (.hex / .bin)
2. Choose transport (WinUSB / Serial / HID)
3. Click **Program**

### WinUSB Driver Installation

For WinUSB transport, install the device driver once:

1. Right-click `winusb_dfm.inf` → **Install**
2. Or use Device Manager → "DFM Programmer" / "DFM Bootloader" → Update Driver → Browse to this directory

## Device Identity

| Property | Bootloader | Application |
|----------|-----------|-------------|
| VID | 0x16C0 | 0x16C0 |
| PID | 0x05DF | 0x05DF |
| Product String | DFM Bootloader | DFM Programmer |
| Flash Address | 0x08000000 | 0x0800C000 |

## Build (Standalone EXE)

```bash
pyinstaller DFM_STM32_Bootloader.spec
```

Output: `dist/DFM_STM32_Bootloader.exe`

## Protocol

Uses STK500v2 protocol (AVR-Doper compatible) over bulk/interrupt endpoints:

- **WinUSB**: EP4 Bulk IN/OUT (64-byte packets)
- **CDC Serial**: COM port (virtual UART)
- **HID**: EP1 Interrupt IN/OUT (32-byte max packet, Report IDs 1-2)
