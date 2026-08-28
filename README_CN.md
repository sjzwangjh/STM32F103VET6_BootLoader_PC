# STM32F103VET6 BootLoader PC 工具

用于 STM32F103VET6 Bootloader 的 PC 端固件升级工具。

## 架构

```text
main.py
  |- usb_transport.py
  |- onlineUpdate.py
  |- flash_programmer.py
  `- hex_parser.py
```

## 入口

运行：

```bash
python main.py
```

## 支持的传输方式

| 传输方式 | 后端 | 驱动 | 状态 |
|----------|------|------|------|
| WinUSB | 原始 WinUSB | 外部 INF 驱动包 | 推荐 |
| 串口 (CDC) | pyserial | Windows 内置 | 支持 |
| HID | hidapi | Windows 内置 | 开发中 |

## 设备标识

| 属性 | Bootloader | 应用程序 |
|------|------------|----------|
| VID | 0x16C0 | 0x16C0 |
| PID | 0x05DF | 0x05DF |
| 产品字符串 | DFM Bootloader | DFM Programmer |
| Flash 地址 | 0x08000000 | 0x0800C000 |

## WinUSB 驱动

WinUSB 驱动包已不再存放在当前 PC 工具目录中。

请使用这里的统一驱动包：

`E:\Codex\Project\Keil\Reference_Local\winUSBDriver`

## 环境要求

```bash
pip install -r requirements.txt
```

依赖项：

- `hidapi>=0.14.0`
- `pyserial>=3.5`

## 构建

```bash
pyinstaller DFM_STM32_Bootloader.spec
```

输出文件：

`dist/DFM_STM32_Bootloader.exe`
