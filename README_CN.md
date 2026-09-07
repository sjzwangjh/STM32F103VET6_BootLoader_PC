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

## Handler 配置

设备需运行 Programmer 应用固件。在 BootLoader 页选择传输方式（默认 WinUSB），
然后切换到 Handler 页操作：

- **读取**：发送 `0xA0`（无参数），校验应答后回填全部 7 个参数。
- **存储**：发送 `0xA1` 和 9 字节配置，由下位机应用并保存到 EEPROM；
  只有收到成功应答才显示存储成功。可随后点击读取检查保存值。

配置字节依次为 `sotLevel, eotLevel, busyLevel, passLevel, ngLevel`（各 1 字节，0/1），
再跟 `delayMsBinToEot, delayMsMinTestTime`（各 2 字节，小端序，0–60000 ms）。
发送的是紧凑的 9 字节参数，不包含 C 结构体对齐填充或 EEPROM 记录头/CRC。

STK500v2 应答正文：读取为 `[A0, 00, 配置9字节]`，存储为 `[A1, 00]`；
第二字节非零表示失败。PC 校验帧 XOR、序号、命令回显、状态码及参数长度。
通信在后台执行，期间禁用配置输入及设备操作按钮；读取失败保留原有界面值。

协议测试：`python -m unittest -v test_handler_protocol`（模拟传输，不写真实设备）。

## 构建

```bash
pyinstaller DFM_STM32_Bootloader.spec
```

输出文件：

`dist/DFM_STM32_Bootloader.exe`
