# DFM Bootloader WinUSB 驱动手动安装说明

当前设备身份：**VID = 0x16C0，PID = 0x05DF**（Bootloader 与 App 统一，AVR-Doper 兼容），WinUSB 接口为复合设备里的
**MI_03** 节点（设备管理器里名字显示为 “DFM Bootloader”、硬件 ID 以 `&MI_03` 结尾）。

> 说明：当前固件使用 bcdUSB=0x0200，Windows 不会自动完成 WinUSB 免驱协商，
> 首次使用需要手动安装一次 WinUSB 驱动。安装一次后，Windows 会记住，后续插拔不再需要重复安装。

## 第一步：清理旧设备状态（首次使用或换过 VID/PID 后）

1. 设备管理器 → 菜单“查看”→“显示隐藏的设备”。
2. 展开“通用串行总线控制器”和“其它设备”，卸载所有名字带 **DFM** 或 VID 为 16C9 的项
   （包括带叹号/黄色问号的）。
3. 管理员 CMD 执行（删除 USB 描述符缓存）：

   ```
   reg delete "HKLM\SYSTEM\CurrentControlSet\Control\UsbFlags\16C005DF0200" /f
   ```

4. 拔掉 USB 线，重新插入。

## 第二步：手动安装 WinUSB 驱动（二选一）

### 方法 A：Windows 自带 WinUSB 驱动（推荐，无需 INF、无签名问题）

1. 设备管理器里找到 “DFM Bootloader”（硬件 ID 以 `&MI_03` 结尾的节点，在“其它设备”下带叹号）。
2. 右键 → “更新驱动程序” → “浏览我的电脑以查找驱动程序”。
3. 点“让我从计算机上的可用驱动程序列表中选取”。
4. 在设备类型列表里选择“**USB 设备**”（USB Device）→ 下一步。
5. 厂商列表里选择“**Microsoft**”，型号列表里选择“**WinUSB 设备**” → 下一步。
6. 出现签名警告时点“仍然安装”。安装完成后节点应显示为“DFM Bootloader”、无叹号。

### 方法 B：使用 INF 安装（[winusb_dfm.inf](winusb_dfm.inf)）

> 该 INF 为未签名驱动，Windows x64 默认会拦截（“第三方 INF 不包含数字签名信息”）。
> 可用“高级启动 → 关闭驱动程序强制签名”（仅本次开机有效）或管理员 `pnputil` 安装。

```
pnputil /add-driver "winusb_dfm.inf" /install
```

或者图形界面：右键 MI_03 节点 → 更新驱动程序 → 浏览我的电脑 → 让我从列表中选取 →
从磁盘安装 → 选择 winusb_dfm.inf。该 INF 会额外写入 `DeviceInterfaceGUIDs`，
方便上位机按 GUID 定位设备。

## 第三步：验证

安装完成后，设备管理器里 MI_03 节点：

- 无叹号；
- “驱动程序”页显示驱动提供商为 Microsoft、驱动为 WinUSB（winusb.sys）。

上位机连接：打开 DFM 上位机，串口应能识别到 Bootloader（0x16C9:0x15DF）的 COM 口，
WinUSB 更新通道应可正常下载。

## 常见问题

- **找不到 “DFM Bootloader” 节点**：确认设备已插入、并先执行第一步的清理。
- **安装后感叹号还在**：卸载该节点，重新执行第一步第 3 条后重插，再走第二步。
- **上位机报 “open … failed: find_device”**：MI_03 未装 WinUSB 驱动，或
  `DeviceInterfaceGUIDs` 缺失——用方法 B 重装一次。
