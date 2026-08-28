# STM32F103VET6 BootLoader PC Tool

PC-side firmware update tool for the STM32F103VET6 bootloader.

## Architecture

```text
main.py
  |- usb_transport.py
  |- onlineUpdate.py
  |- flash_programmer.py
  `- hex_parser.py
```

## Entry Point

Run:

```bash
python main.py
```

## Supported Transports

| Transport | Backend | Driver | Status |
|-----------|---------|--------|--------|
| WinUSB | raw WinUSB | External INF package | Recommended |
| Serial (CDC) | pyserial | Windows built-in | Supported |
| HID | hidapi | Windows built-in | In development |

## Device Identity

| Property | Bootloader | Application |
|----------|------------|-------------|
| VID | 0x16C0 | 0x16C0 |
| PID | 0x05DF | 0x05DF |
| Product String | DFM Bootloader | DFM Programmer |
| Flash Address | 0x08000000 | 0x0800C000 |

## WinUSB Driver

The WinUSB driver package is no longer stored in this PC tool directory.

Use the unified driver package here:

`E:\Codex\Project\Keil\Reference_Local\winUSBDriver`

## Requirements

```bash
pip install -r requirements.txt
```

Dependencies:

- `hidapi>=0.14.0`
- `pyserial>=3.5`

## Build

```bash
pyinstaller DFM_STM32_Bootloader.spec
```

Output:

`dist/DFM_STM32_Bootloader.exe`
