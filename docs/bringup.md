# 上板点亮与联调清单（Bring-up & Bring-up Troubleshooting）

> 这份清单是**板子到手那天照着做**的操作手册。每一步都写了「预期看到什么」和「不对时怎么查」。
> 所有命令都已在本机验证过语法，但**屏幕相关步骤尚未实机执行**（见 `docs/journal.md` 未验证清单）。

---

## 0. 前置：确认板子被电脑认出来

插上**能传数据的 USB-C 线**（不是纯充电线），然后：

```powershell
# 找 Espressif 设备（VID 303A），以及它占用的串口
powershell -NoProfile -Command "Get-PnpDevice -PresentOnly | Where-Object { \$_.InstanceId -match 'VID_303A' } | Select-Object Status,FriendlyName,InstanceId"
powershell -NoProfile -Command "Get-ItemProperty 'HKLM:\HARDWARE\DEVICEMAP\SERIALCOMM'"
```

预期：出现一个 `USB 串行设备 (COMx)` 或 `USB JTAG/serial debug unit`，记住 COM 号。
**如果一个都没有**：换线（很多 Type-C 线只有电源针）、换口、或按住 BOOT 再插（进下载模式）。

> 本板 ESP32-S3 是原生 USB（USB-Serial/JTAG），没有 CH340/CP210x；Windows 10/11 自带驱动，不需要装。

---

## 1. 构建 + 烧录

```bash
cd /d/AI_vibe/tdisplay-s3-pc-monitor

# 一次性：拉 LVGL 9.2.2（约 19MB，gitignore）
cmd //c tools\\fetch_deps.cmd

# 构建（首次会自动 set-target esp32s3）
bash tools/build.sh

# 烧录（COM 号换成你自己的）
bash tools/build.sh flash COM5

# 看日志
bash tools/build.sh monitor
```

预期构建产物：`firmware/build/tdisplay_pcmon.bin`，约 1.2 MB，3 MB 分区剩 60% 左右。

---

## 2. 第一次点亮：先看日志，再看屏

预期串口日志（顺序大致如下）：

```
I (xxx) tdisplay-pcmon: boot: T-Display-S3 PC monitor
I (xxx) lcd: power on (GPIO15)
I (xxx) lcd: i80 bus ok, panel st7789 ok
I (xxx) lvgl_port: display 320x170, draw bufs 25.6KB x2
I (xxx) link: link module ready (state=INIT)
I (xxx) tdisplay-pcmon: boot done, NO_LINK
```

**判断顺序（黑屏时按这个顺序查，别乱猜）**：

| 现象 | 最可能原因 | 怎么查 |
|---|---|---|
| 串口完全没输出 | 供电/线/没进 USB | 换线；按住 BOOT 插；`bash tools/build.sh flash COMx` 重烧 |
| 有日志但全黑 | **`LCD_POWER_ON`(GPIO15) 没拉高** | 日志里应有 `lcd: power on`；没有就是代码没跑到 |
| 全黑但背光微微发亮 | 面板初始化/复位时序 | 查 RST(GPIO5) 是否真的在 init 前拉低再拉高 |
| 白屏 | 数据线序错 | 检查 D0..D7 = 39/40/41/42/45/46/47/48 |
| 花屏/撕裂 | PCLK 太高 | 先降到 10 MHz 试，稳了再往上加 |
| 图像偏移/缺边 | gap/offset 参数不对 | 进第 3 步标定 |
| 红色显示成蓝色 | RGB/BGR 反了 | 第 3 步换 variant |
| 颜色像底片 | invert 反了 | 第 3 步换 variant |

---

## 3. 标定：一次把 5 个参数定下来

需要定的是 **swap_xy / mirror_x / mirror_y / invert / RGB-BGR / gap** 六项。网上查不到这块屏的可靠值，
所以固件里预置了 **4 组候选**，靠测试图案肉眼判断。

**进入标定模式**：长按 **BOOT** 键 2 秒 → 屏上出现测试图案（边框 + 四角 L 标记 + R/G/B 色块 + 白十字 + 组号）。

**切换参数组**：标定模式下**短按 BOOT** 切下一组（V0→V1→V2→V3→V0）。
也可以用串口命令：`lcd`（下一组）/ `lcd 2`（指定）。

**退出标定**：再长按 BOOT 2 秒 → 回到正常 UI。

### 判断标准（按顺序排除）

1. **边框四边都完整**：缺哪条边 → gap/offset 不对（缺方向的那条说明起点偏了）。
2. **四角 L 标记都在正确角落**：上下颠倒 → mirror_y 错；左右颠倒 → mirror_x 错。
3. **顶部色块从左到右是 红/绿/蓝**：红蓝互换 → RGB/BGR 错；颜色发白/像底片 → invert 错。
4. **白十字在正中间**：偏说明 gap 不对。
5. 文字（组号 Vn）能正常读出 → 说明像素没有重复/错位。

> 4 组候选只是起点。如果 4 组都不完全对，就把「最接近的那组」的参数记下来（journal 里回填），
> 直接改 `firmware/main/display/lcd.c` 里的 variant 表（组合是有限的：swap×2 × mirror×4 × invert×2 × RGB/BGR×2 = 32 种，
> gap 只需在 0/35 之间试）。

**定好之后**：把正确参数写进 `lcd.c` 的默认 variant（改成不需按键的固定配置），并在 `docs/journal.md`
「待验证/未知」里勾掉 ST7789 参数那一项，写清最终值。

---

## 4. 联调上位机（USB 一线通）

```powershell
cd D:\AI_vibe\tdisplay-s3-pc-monitor\companion
powershell -ExecutionPolicy Bypass -File .\setup.ps1     # 建 .venv（只需一次）
powershell -ExecutionPolicy Bypass -File .\run.ps1       # 后台启动（pythonw）
```

然后拔插一次板子（让 pcmond 发现串口）或者等它自动重连（0.5→10s 指数退避）。

**预期**：
- 屏上顶栏状态点变**蓝（USB）**，CPU/内存/GPU/网速开始每秒刷新；
- 拔掉 USB → 3 秒内转 **STALE**（数值变灰，顶栏黄点），插回 → 1 秒内恢复；
- 串口日志有 `[link_usb]` 收帧记录。

**排障**：
- 屏一直 OFFLINE：`couple` 检查 `pcmond` 日志里有没有「USB 链路已禁用 / 未找到串口」；
  用 `python -m serial.tools.list_ports` 看端口在不在；确认没有别的软件（串口助手/Arduino IDE）占着 COM。
- 丢帧/卡顿：串口日志看帧号是否连续（`seq`），不连续说明 USB 读得慢 → 检查是否有大日志在刷。

---

## 5. 联调 WiFi 模式（可选第二阶段）

1. 设备首次上电会通过 USB 发 `{"t":"hello","need_cfg":1}`，pcmond 收到后自动回配置帧并让设备重启
   （字段名见 `docs/design/architecture.md` §4）。
2. 或者手动串口配置：`wifi <ssid> <pass>` → `url http://<PC局域网IP>:8787/api/stats` → `token <tok>` → `reboot`。
3. Windows 防火墙要放行 8787 的入站（专用网络）。用管理员 PowerShell：
   `New-NetFirewallRule -DisplayName "pcmond" -Direction Inbound -LocalPort 8787 -Protocol TCP -Action Allow`
4. **验证优先级**：USB 插着时状态灯必须是蓝（USB），拔掉 USB 后 3 秒内变绿（WiFi）——这是
   logicprobe 验过的 `USB 优先 + 无损回退` 不变式，实机必须复现。

---

## 6. 结果回填（重要）

点亮和联调产生的**所有实测值**都要回写 `docs/journal.md`：
最终 ST7789 参数、实际帧率、USB 丢帧率、WiFi 延迟、有没有踩到新坑。
`docs/README.draft.md` 里所有「未验证」条目按实际情况勾掉——**没实测过的不要勾**。
