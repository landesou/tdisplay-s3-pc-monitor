<!--
  草稿说明（发布前删掉这段注释）
  - 本文件是 GitHub 首页 README 的草稿，链接按「仓库根目录」书写；若直接在 docs/ 下预览，
    请给相对链接临时加 ../ 前缀。
  - 事实来源：docs/journal.md、docs/design/architecture.md、docs/bringup.md、firmware/main/board.h、
    firmware/test/host_link/README.md、companion/README.md、tools/*。
  - 写这份草稿时屏幕尚未点亮，任何「显示效果/帧率/手感」类描述都没有写，只在「已验证 / 未验证」里如实列出。
-->

# T-Display-S3 PC Monitor

**把一块 1.9 寸的 ESP32-S3 开发板，做成放在桌角的电脑性能仪表盘。**

> **English TL;DR** — A 1.9″ 320×170 desktop dashboard for your PC, built on
> LilyGO **T-Display-S3** (ESP32-S3, 8-bit 8080 parallel ST7789, 16 MB flash).
> A Python host daemon (`pcmond`) samples CPU / RAM / GPU / network / disk every
> second and pushes one compact JSON line over **USB CDC** (HTTP + Wi-Fi as
> fallback). Firmware: ESP-IDF 5.5.3 + LVGL 9.2.2. Host: Python 3.10+. **License: MIT.**
> **Status:** firmware builds clean (0 error / 0 warning) and the link layer passes
> 61 host-side checks; the state machine is model-verified (0 errors); host passes
> 65 unit tests — **the panel has not been lit yet**, see
> [Verified / Unverified](#已验证--未验证).

> ⚠️ **项目状态（2026-11-10）**：屏幕还没点亮——写这份文档时板子没插在电脑上
> （系统里查不到 `VID_303A` 设备）。固件两层已写完并**编译通过（0 error / 0 warning）**，
> 链路层另有 **61 条主机离线测试**通过；状态机通过模型验证（0 error）；上位机 65 个单测通过
> 并读出真实 GPU 数据。**以上都不是实机验证**，请以文末两张清单为准。

---

## 它能显示什么

横屏 **320×170**，两页 UI，底部一排状态信息（链路来源、时间、开机时长、页码）。
下面是根据接口契约画的目标界面草图——**不是实机截图，屏还没亮**：

```text
T-Display-S3        MY-PC                    ● USB   14:32
──────────────────────────────────────────────────────────
CPU   23%   ▁▂▄▅▃▂▁▃▅▇▅▃▂▁                      2400 MHz
MEM   61%   ████████████░░░░░░░░    9340 / 15288 MiB
GPU   16%   RTX 5060   58 °C   1864/8151 MiB   29.2 W
NET   ↓ 1.2 ↑ 0.3 MiB/s       DISK  R 0.4  W 2.1 MiB/s
──────────────────────────────────────────────────────────
PAGE 1/2                                       uptime 3d 04h

第 2 页：24 线程占用 + 60 点历史曲线（环形缓冲）

  C0   12%  ███░░░░░░░░░░░░░░░░░   ▁▂▃▅▄▂▁▂▃▅▆▄▂
  C1   34%  ████████░░░░░░░░░░░░   ▁▁▂▃▄▆▇▅▃▂▁▁▂▃
  ...
```

状态灯含义：USB 帧新鲜 = 在线；USB 断但 Wi-Fi 有帧 = 走 Wi-Fi；**3 秒没有新帧 = `STALE`**，
界面标灰（原则：绝不在旧数据上显示「在线」）。

> 未验证：以上布局、配色、字体、切页交互都还没有上过屏。屏点亮后这一节会换成实机照片。

---

## 硬件

| 项目 | 规格 | 备注 |
|---|---|---|
| 主控 | ESP32-S3（Xtensa LX7 双核） | 本项目用 ESP-IDF 5.5.3 |
| 屏幕 | 1.9″ ST7789V IPS，物理 170×320 | 逻辑横屏 320×170 |
| 屏幕接口 | **8-bit Intel 8080 并口**（不是 SPI） | D0–D7 = GPIO39/40/41/42/45/46/47/48 |
| 控制线 | WR=PCLK GPIO8、DC GPIO7、CS GPIO6、RST GPIO5、RD GPIO9 | 只写模式，RD 不用 |
| 屏电源使能 | **GPIO15（LCD_POWER_ON，高有效）** | 不拉高 → 背光开了也是黑屏 |
| 背光 | GPIO38，LEDC PWM 调光 | |
| 按键 | BOOT = GPIO0（也是下载模式 strapping 脚）、KEY = GPIO14 | |
| Flash / PSRAM | 16 MB Flash；8 MB PSRAM（**v1 不启用**） | 双缓冲放内部 RAM 足够，避开 PSRAM 模式配错的风险 |
| USB | 原生 USB-Serial/JTAG（D-/D+ = GPIO19/20） | 无外置串口芯片，一根线搞定供电 + 数据 + 日志 |
| 像素时钟 | 20 MHz | `BOARD_LCD_PIXEL_CLOCK_HZ` |
| 电池 ADC | GPIO4 | v1 仅预留，不做电量显示 |

引脚定义集中在 [`firmware/main/board.h`](firmware/main/board.h)。

按键行为、屏参数（swap_xy / mirror / invert / RGB-BGR / gap）**尚未在实机上确认**：
固件预置了 4 组候选参数，点亮当天靠测试图案肉眼判断哪组对，方法见
[`docs/bringup.md`](docs/bringup.md)。

---

## 架构

```text
PC（Windows，上位机 pcmond）
  采集器：psutil（CPU / 内存 / 磁盘 / 网络）+ nvidia-smi 子进程（GPU）
     │  每秒一帧 StatsSnapshot → 紧凑 JSON（一行、UTF-8、≤ 600 B）
     ├──► USB CDC 推送 1–2 Hz（主链路）
     └──► HTTP :8787  GET /api/stats（Bearer Token，备链路）
            │
            │ USB-C 线（供电 + 数据）        Wi-Fi（局域网 HTTP 轮询）
            ▼
T-Display-S3（ESP-IDF 5.5.3 + LVGL 9.2.2）
  link_usb（CDC 行读取） / link_http（esp_http_client）→ 同一个 JSON 解析
     │  仲裁：USB 有帧 → USB 优先；否则 Wi-Fi；都没有 → NO_LINK / STALE
     ▼
  LVGL 9：横屏 320×170，2 页 UI，60 点历史环形缓冲
```

两条链路**报文字节相同**，设备侧只在 `src` 字段里标注 `usb` / `wifi`。
契约细节见 [`docs/design/architecture.md`](design/architecture.md)。

**为什么不用 AIDA64 / HWiNFO？** AIDA64 是付费软件；HWiNFO 免费版共享内存有时长限制；
而且现成软件不能同时走 USB 和 HTTP 两种传输。自写采集器一套 JSON、两条链路、零管理员权限。

---

## 固件实现要点（编译级，未实机验证）

display/ui + link/config 两层都写完了，下面是上板前值得先知道的实现选择：

- **上电顺序**：`LCD_POWER_ON`(GPIO15) 拉高 → 背光 LEDC 先设 `duty=0` →
  i80 总线（8-bit，D0–D7 = 39/40/41/42/45/46/47/48，WR=8，20 MHz，`dma_burst=64`）→
  ST7789 panel（RST=5）→ MADCTL + gap + invert → 最后开背光。
- **旋转**：不走 `esp_lcd_panel_mirror` / `swap_xy`，改成初始化后**一次写 MADCTL(0x36)**——
  BGR 位没有对应的 driver setter，而且重建 panel 会让 LVGL 缓存的 handle 悬空。
  gap / invert 仍走 driver API。
- **显存预算**：两块 `esp_lcd_i80_alloc_draw_buffer(320*40*2)` = **51.2 KB 内部 RAM**，不开 PSRAM；
  LVGL 堆 64 KB 是静态 bss。启动后内部堆 ≈153 KB，扣掉 draw buffer 余 ≈102 KB 给 Wi-Fi / HTTP / USB；
  不够时把 `LCD_DRAW_BUF_LINES` 从 40 降到 24（一处改）。
- **USB 收数据必须装驱动**：USB-Serial/JTAG 的 RX FIFO 只有 **64 B**，而一帧 JSON 是 ~424 B 的突发写入，
  读 console 的 stdin 会丢字节。正确姿势是 `usb_serial_jtag_driver_install()` →
  `usb_serial_jtag_vfs_register()` → `usb_serial_jtag_vfs_use_driver()`，
  让 esp_log 的 TX 和 JSON 行共用同一把锁，日志才不会把数据行切碎。
- **仲裁**：USB 在线时 Wi-Fi 帧**显式忽略**（seq / age 都不动），HTTP 轮询整轮跳过；
  这样状态灯不会在数据其实来自 Wi-Fi 时长亮 USB。
- **标定模式**：长按 BOOT 2 秒进/出，标定中短按 BOOT 切下一组候选；串口 `lcd` / `lcd 2` 等效。
  4 组候选：V0（MV+MX+invert+RGB+gap(0,35)，MV/MX 为镜像方向）/ V1（换镜像轴）/ V2（关 invert+BGR）/
  V3（无镜像，gap(0,0)）。**哪组对还没实机确认**，判读标准见 [`docs/bringup.md`](docs/bringup.md)。
- **UI 布局**：P0 = 顶栏（主机名 / 时钟 / 状态点）+ CPU / GPU / MEM / NET 卡片 + 60 点曲线；
  P1 = 24 根线程柱（11 px 柱宽）+ 两行明细（DISK R/W、VRAM、温度、开机时长、FPS）。
  顶栏放在页面容器外、两页共享，所以 P1 也能看到 STALE。
- **串口命令**：`cfg` / `wifi <ssid> <pass>` / `url <url>` / `token <tok>` / `lcd [0-3]` /
  `reboot` / `help`。

---

## 快速开始

> 环境前提：ESP-IDF **5.5.3**、Python **3.10+**、Windows（上位机目前只验证了 Windows）。
> **IDF 路径**：`tools/idf_build.cmd` 默认走本机的 ESP-IDF 安装位置，克隆到别的机器时
> **不用改脚本**，用环境变量覆盖即可（默认值仍是本机安装位置，例如）：
>
> ```bat
> set ESP_IDF_DIR=D:\path\to\esp-idf-v5.5.3
> set ESP_IDF_TOOLS_DIR=D:\path\to\espressif
> set ESP_IDF_PYENV=%ESP_IDF_TOOLS_DIR%\python_env\idf5.5_py3.11_env
> ```

### 1) 拉固件依赖（LVGL 9.2.2，不走组件管理器）

```bat
tools\fetch_deps.cmd
```

把 LVGL `v9.2.2` 浅克隆、裁剪后放进 `firmware/components/lvgl/`（该目录 gitignore 掉，
不进仓库，所以每个 clone 都要跑一次）。这个工程**有意不用 ESP-IDF 组件管理器**：
本机上 `idf.py` 的组件管理器连不上 `components-file.espressif.com`，而固定版本源码可离线复现。

### 2) 编译

```bash
bash tools/build.sh
```

第一次会自动 `set-target esp32s3`。其它动作：

```bash
bash tools/build.sh menuconfig
bash tools/build.sh flash COM5          # 烧录（端口换成你的）
BUILD_DIR=build-foo bash tools/build.sh # 独立 build 目录，多人并行时用
```

产物：`firmware/build/tdisplay_pcmon.bin` ≈ **1.18 MB**（3 MB app 槽剩 61%）。

> 为什么要 `bash tools/build.sh` 这一层：Windows 下直接 `cmd //c idf_build.cmd` 会被 MSYS
> 改写参数，而 msys 的 bash 里直接 export IDF 环境变量会导致 `idf.py` 假报
> 「python.exe doesn't exist」。细节见 [docs/journal.md](journal.md)。

### 3) 烧录

```bash
bash tools/build.sh flash COM5
```

> 未验证：**烧录这一步还没有在实机上跑过**（写文档时板子未连接电脑）。板子插上后
> 设备管理器应出现「USB 串行设备（COMx）」，`VID` 为 `303A`。

### 4) 启动上位机

```powershell
cd companion
powershell -ExecutionPolicy Bypass -File .\setup.ps1   # 建 .venv，装 psutil / pyserial

# 先验证采集：打印一帧 JSON 后退出（不连串口、不开端口）
.\.venv\Scripts\python.exe -m pcmond --once

# 前台调试 / 后台无窗口运行
.\.venv\Scripts\python.exe -m pcmond --verbose
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

登录自启（不需要管理员、不写注册表，只在当前用户启动文件夹放一个快捷方式）：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_startup.ps1
powershell -ExecutionPolicy Bypass -File .\uninstall_startup.ps1
```

设备首次联网的推荐方式：设备通过 USB 上报 `{"t":"hello","need_cfg":1}`，上位机回一帧
`{"t":"cfg",...}`（Wi-Fi 凭据 + `api_url` + `api_token`），设备写 NVS 后重启。也可用串口命令行
`wifi <ssid> <pass>` / `url <url>` / `token <tok>` / `cfg` / `reboot` 手动配。
更多参数与排障见 [`companion/README.md`](companion/README.md)。

> **板子到手后**：按 [`docs/bringup.md`](docs/bringup.md) 走一遍——认设备 → 构建烧录 →
> 第一次点亮/黑屏排查表 → 4 组参数标定 → 上位机与 Wi-Fi 联调 → 结果回填 journal。
> 这份清单是「板子到手当天照着做」的操作手册，屏幕相关步骤本身尚未执行过。

---

## 监控指标

| 指标 | 契约字段 | 数据来源 | 需要管理员权限 |
|---|---|---|---|
| CPU 占用率、平均频率 | `cpu.avg` / `cpu.mhz` | psutil | 否 |
| 每线程占用率 | `cpu.cores`（设备最多渲染 32 个） | psutil | 否 |
| 内存占用 / 已用 / 总量 | `mem.pct` / `mem.used` / `mem.total` | psutil | 否 |
| GPU 占用、温度、显存、功耗、名称 | `gpu.*` | `nvidia-smi` 子进程（3 s 超时） | 否 |
| 网络上下行速率 | `net.down` / `net.up` | psutil 计数器差分 | 否 |
| 磁盘读写速率 | `disk.read` / `disk.write` | psutil 计数器差分 | 否 |
| 时间、开机时长 | `t` / `up` | PC 本地时间 / 开机时长 | 否 |
| CPU 温度（字段预留） | `cpu.temp` | v1 笔记本基本读不到 → `null` → 界面显示 `--` | 需要 MSR/内核驱动，v1 不承诺 |

补充：

- **不需要管理员权限、不装驱动、不改系统设置**（上位机侧）。Wi-Fi 链路的入站防火墙放行是唯一
  可能需要管理员操作一次的事（`New-NetFirewallRule ... -LocalPort 8787`，见 companion 排障表）。
- 没有 NVIDIA 独显 / `nvidia-smi` 不可用时，`gpu` 字段整体发 `null`，界面显示 `--`，不会崩。
- 网卡 / 磁盘速率是 1 s 窗口差分；`--once` 单帧的 `net`/`disk` 接近 0 是正常现象。
- 有虚拟显示适配器（ToDesk / GameViewer / ExtensoDesk 之类）时显卡名会误报，采集侧做了过滤。

---

## 目录结构

```text
tdisplay-s3-pc-monitor/
├── firmware/                  # ESP-IDF 5.5.3 工程
│   ├── main/
│   │   ├── board.h            # 板级引脚（唯一引脚真相源）
│   │   ├── main.c
│   │   ├── display/           # i80 并口 + ST7789 + LVGL 移植
│   │   ├── ui/                # 两页 UI + 历史缓冲
│   │   ├── link/              # USB CDC / HTTP 双链路 + 仲裁状态机
│   │   ├── config/            # NVS 配置
│   │   └── stats.h            # 帧结构（契约 §2 的 C 侧）
│   ├── components/lvgl/       # fetch_deps.cmd 生成，gitignore
│   ├── test/host_link/        # 链路层主机离线测试（真实 link.c 编到 PC 上跑）
│   ├── partitions.csv         # 16 MB 分区：nvs 24K / phy 4K / factory 3 MB / storage 1 MB
│   └── sdkconfig.defaults
├── companion/                 # Windows 上位机 pcmond（Python，依赖只有 psutil + pyserial）
│   ├── pcmond/                # 采集 / 快照 / 队列 / USB / HTTP / 配置 / GPU / 差分
│   ├── tests/                 # pytest
│   ├── setup.ps1 / run.ps1 / install_startup.ps1 / uninstall_startup.ps1
│   └── README.md
├── tools/                     # 构建包装（build.sh → idf_build.cmd）、依赖拉取
└── docs/
    ├── journal.md             # 开发日志（过程事实源）
    ├── bringup.md             # 上板点亮/标定/联调清单（板子到手后照着做）
    ├── design/architecture.md # 架构与接口契约
    ├── design/link-state-machine.model.json  # 状态机模型（logicprobe 验证用）
    ├── README.draft.md        # 本文件
    └── blog-draft.md          # 技术长文草稿
```

---

## 踩过的坑（挑最值钱的）

1. **`LCD_POWER_ON`（GPIO15）不拉高，屏永远不亮。** 背光 PWM 调得再对也是黑屏——屏电源使能在
   GPIO15 上，初始化流程里必须先拉高。这条来自板级资料核对，属于「预埋坑」，点亮阶段第一件事就是验它。
2. **ESP-IDF 组件管理器连不上，LVGL 改为 vendor。** `idf.py` 报
   `Cannot establish a connection to the component registry`，但同一个 URL 用 python 直接抓是 200、
   IDF 自带 venv python 也是 200 → 是组件 HTTP 客户端的问题，不是网络问题。
   解法：`tools/fetch_deps.cmd` 固定 `v9.2.2` 源码，离线可复现。
   裁剪时还有个小坑：LVGL 的 `env_support/cmake/esp.cmake` **无条件**把 `examples/`、`demos/`
   加进 include 路径，删目录会 CMake 报 `Include directory ... is not a directory` → 保留（可为空）。
3. **MSYS 把 IDF 的 Windows 环境变量弄坏，`idf.py` 假报错。** 在 bash 里 export
   `idf_tools.py export --format key-value` 输出的 `PATH`（分号形式）会干掉 coreutils；
   export `IDF_PYTHON_ENV_PATH=D:\...` 又会被 MSYS 改写，于是 `idf.py` 报
   「python.exe doesn't exist! Please run the install script」——**假报错，python 明明存在**。
   解法：所有 IDF 构建走 `cmd.exe` 批处理，并在导出后 `set "PATH=%PATH%;%PATH_ORIG%"` 把 System32 拼回来。
4. **`.cmd` 文件里不能写 UTF-8 中文。** cmd.exe 按 **cp936** 解析批处理，UTF-8 中文注释变成乱码字节，
   把 `rem` 行当成命令执行（`|` 被当管道），报 `'f_build.cmd' 不是内部或外部命令`。
   解法：**所有 `.cmd` / `.bat` 一律纯 ASCII**（注释也用英文），bash 脚本不受影响。
5. **Windows 上 `SO_REUSEADDR` 语义和 Linux 相反。** `socketserver.allow_reuse_address = True`
   在 Linux 是「TIME_WAIT 后快速重绑」，在 Windows 却**允许第二个进程再绑同一个正在监听的端口**，
   于是两个 pcmond 会静默抢 8787，谁也不报错。解法：Windows 上改用 `SO_EXCLUSIVEADDRUSE`，
   第二个实例报 `WinError 10048` 并且只关 HTTP 链路，采集与 USB 继续跑。

完整记录（含每次报错原文、验证命令、结论）在 [`docs/journal.md`](journal.md)。

---

## 没有板子也能验证固件：host_link 离线测试

链路层的新鲜度状态机和「帧 JSON → `stats_t`」解析都是纯 C 逻辑，不碰 GPIO / LCD / Wi-Fi，
所以可以**把固件里真实的 `link.c` 直接编到 PC 上跑**。`firmware/test/host_link/` 用 MinGW +
FreeRTOS / esp_log / esp_err 的 shim + ESP-IDF 自带 cJSON 做这件事——编的是
`firmware/main/link/link.c` 本体，不是复制品：

```bash
# Windows：MinGW 不在 PATH 时显式指定（也可以用 scoop/MSYS2 装一个）
GCC=/path/to/mingw64/bin/gcc.exe bash firmware/test/host_link/run.sh
# Linux / macOS：有 gcc 或 clang 就行
bash firmware/test/host_link/run.sh
```

结果：**61 checks, 0 failure**，覆盖状态机全部迁移（INIT 丢帧、USB 抢占、3 个 tick 转 STALE、
USB 在线时 Wi-Fi 帧被忽略、SLEEP 终态）、`ONLINE_* 时 age ≤ 2` 不变式、`frame.json` 里 424 B 的
真实上位机帧解析、40 核截断到 32、占用率越界夹取、坏 JSON 拒收。找不到编译器时脚本只打印提示并
`exit 2`，不猜路径、不静默跳过。

> 它不代表实机验证：跑的是 x86 代码，没碰 USB / Wi-Fi / NVS / 串口 / 深睡；
> `xSemaphore` 在 shim 里是空实现，真实 RTOS 下的竞态和栈溢出测不到。模型验证回答「设计对不对」，
> 这个测试回答「代码是不是真的那么走」。

---

## 已验证 / 未验证

诚实起见，这里按 journal 记的验收结果逐项列出。**写这份文档时屏还没亮。**

### ✅ 已验证（均为非实机）

- **① 固件编译**：`bash tools/build.sh` → **0 error / 0 warning**，
  `tdisplay_pcmon.bin` = `0x12ddf0`（≈ 1.18 MB / 1,236,464 B），目标 `xtensa-esp32s3`；
  16 MB 分区布局下 3 MB app 槽剩余 61%（比骨架时大，是 LVGL 全量链接进来了）。
- **② 链路层离线测试**：`firmware/test/host_link/run.sh` → **61 checks, 0 failure**
  （状态机全迁移 + `ONLINE_* age ≤ 2` 不变式 + 真实 424 B 帧解析 + 边界与坏 JSON）。
- **③ 上位机实测**：
  - 65 个单测：`python -m pytest companion -q` → `65 passed in 2.71s`。
  - 单帧数据：`python -m pcmond --once` 输出 424 B 合法帧（契约上限 600 B；`host` 正好 15 字符
    卡满、`gpu.name` 34 字符被截到 19），GPU 字段是 `nvidia-smi` 实测值
    （RTX 5060 / 19% / 59 °C / 2203 MiB / 29.7 W）。
  - HTTP：`GET /api/stats` 带 Token → 200；无 Token / 错 Token → 401；`/health` → 200；
    `/xyz` → 404；连发两次请求 `seq` 都是 7 → `/api/stats` 只回最新快照、不重复采集。
  - 采集/传输解耦：采集线程固定周期产帧，队列满丢最旧帧，采集永不阻塞；USB 拔掉指数退避重连。

- **附：其它非实机验证**：链路状态机 `logicprobe_verify` 三轮迭代后
  `verdict = pass_with_findings`，**0 error / 21 warning**，模型 hash `88fee4edf864`，
  模型在 `docs/design/link-state-machine.model.json`；`BUILD_DIR=build-lead bash tools/build.sh`
  通过、flash 参数 `--flash_size 16MB --flash_freq 80m` 正确；
  环境：ESP-IDF 5.5.3、Python 3.12.13、psutil 7.2.2、pyserial 3.5、pytest 9.2.0、Windows 11 10.0.26100。

### ⚠️ 未验证

- **屏幕能否点亮、4 组 ST7789 参数哪组对、i80 时序（PCLK / DC 电平）、颜色位序、观感、按键手感、
  实际帧率**——全都没有（板子未插在电脑上，系统里无 `VID_303A` 设备）。
  判读方法见 [`docs/bringup.md`](docs/bringup.md)，点亮后要把结果回填 journal。
- **烧录流程**：`bash tools/build.sh flash COM5` 尚未在实机执行过。
- **USB 真机收帧与 `hello` → `cfg` 往返**：1–2 Hz、424 B/帧的实际收帧、丢帧率、断线重连、
  cfg 写 NVS 都未联调，目前只有单测 + 主机离线测试 + 假串口验证。
- **Wi-Fi 连接与 HTTP 轮询**：设备能否连上、防火墙放行后能否稳定轮询，未在局域网设备上验证；
  按计划 v1 先把 USB 链路跑通，Wi-Fi 双模随后（HTTP 链路已在架构里定义）。
- **NVS 持久化、串口命令、深睡**：代码已写，未上板验证。
- **CPU 温度**：Intel 14 代 HX 需要 MSR/内核驱动，v1 不承诺（`cpu.temp` 本机为 `null`）。
- **板子丝印 `H569` 对应的具体版本**：公开资料没能核实，按标准 T-Display-S3 处理。
- **非本机环境**：上位机只在本机（conda Python 3.12.13）验证过；其它 Python 3.10+ 环境、
  其它 ESP-IDF 安装路径未验证。

---

## 许可与致谢

- **许可证**：本仓库采用 **MIT**（见仓库根 [`LICENSE`](LICENSE)，版权署名
  `T-Display-S3 PC Monitor contributors`）。本项目用到的第三方许可均兼容：ESP-IDF 官方 i80 示例是
  **CC0**，LVGL 9.2.2 是 **MIT**，参考项目 cc-usage-display 是 **MIT**。
  `tools/fetch_deps.cmd` 会把 LVGL 的 `LICENCE.txt` 一并拷到 `firmware/components/lvgl/`，
  其许可随源码保留。
- **致谢**：
  - [ESP-IDF](https://github.com/espressif/esp-idf) 官方 `examples/peripherals/lcd/i80_controller/`
    ——本项目的 i80 并口总线、ST7789 初始化、双 draw buffer、LVGL 9 移植层，路子都从这里来。
  - [LVGL](https://github.com/lvgl/lvgl) v9.2.2（本项目 vendor 到 `firmware/components/lvgl/`）。
  - [cc-usage-display](https://github.com/wangjian124/cc-usage-display)——参考项目。
    **只借鉴架构**（PC 端 Python Companion + 设备侧轮询渲染），硬件层完全不同
    （它是微雪 ESP32-C6-Touch-AMOLED-2.16：480×480 AMOLED、QSPI CO5300、触摸、PMU，
    本板是无触摸无 PMU 的 8-bit 8080 并口 ST7789）。

## 文档

- 架构与接口契约：[docs/design/architecture.md](design/architecture.md)
- 上板点亮、黑屏排查、4 组参数标定与联调清单：[docs/bringup.md](docs/bringup.md)
- 状态机模型：[docs/design/link-state-machine.model.json](design/link-state-machine.model.json)
- 开发日志（过程事实源）：[docs/journal.md](journal.md)
- 技术长文草稿：[docs/blog-draft.md](blog-draft.md)
- 上位机说明：[companion/README.md](companion/README.md)
