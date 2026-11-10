# 项目日志 / Journal

> 规则：本文件是唯一的过程真相源。README、博客、交付说明里的命令、报错、参数、版本号
> 必须能在这里追溯到来源；找不到出处的写「未验证」，不许脑补。
> 记录粒度：选型决策、价格渠道、当时的疑问与验证过程，都要留。

---

## 2026-11-10 立项：把 T-Display-S3 做成 PC 性能监控仪

### 需求对话（用户原话要点）

- 板子：`T-Display-S3 H569`（LilyGO，ESP32-S3）。
- 目标：改造成「电脑性能监控仪」。
- 工作方式：先问问题明确目标 → 十分有把握后开智能体团队「边做边记录」。
- 参考：GitHub 上有开源项目，但那是微雪电子的板子。
- 用户给出的参考仓库：<https://github.com/wangjian124/cc-usage-display>

### 第一轮问答结论（用户选择）

| 问题 | 用户选择 |
|---|---|
| 通信 | 先 USB，跑通后再加 WiFi 双模 |
| 数据来源 / 监控内容 | 「你先看那个开源的项目」→ 由 Lead 读参考仓库后提出方案 |
| 屏幕摆法 | 横屏 320×170，立在桌面 |
| 目标电脑 | 这台笔记本（联想） |
| 开发框架 | ESP-IDF 5.5.3（本机已有） |
| 交付范围 | 选项 2+3：工程化（自启/托盘/配置/README）**并且**要能开源到 GitHub + 记录文章 |

### 参考仓库调研（2026-11-10，`git clone` 到 `D:\AI_vibe\_ref\cc-usage-display`）

- 它是给 **微雪 ESP32-C6-Touch-AMOLED-2.16**（480×480 AMOLED、QSPI CO5300、CST9220 触摸、
  AXP2101 PMU、QMI8658 IMU、PCF85063A RTC）写的**带主页启动器**，Arduino/PlatformIO + LVGL。
- 架构值得搬：PC 端 Python「Companion」（纯标准库）读本地数据源 → 局域网 HTTP + Bearer Token
  → 设备轮询渲染。设备侧 15 秒拉一次，60 秒无操作熄屏，串口调试命令一整套。
- **硬件层完全不能搬**：屏是 QSPI AMOLED + 触摸 + PMU，本板是 **8-bit 8080 并口 ST7789、
  无触摸、无 PMU**。所以「抄架构、重写驱动」。
- 目录/文件规模（实测 `wc -l`）：`src/main.cpp` 782 行、`src/apps.cpp` 1268 行、`src/ui.cpp` 631 行、
  `companion/cc_usage_display/{config,main,metrics,server}.py` = 110/102/321/193 行。

### 硬件事实核对（有出处）

引脚（来源：CircuitPython 板级定义 / PlatformIO board def，经 TinkerNews 汇总页转述，
`https://www.tinkernews.com/boards/lilygo-t-display-s3`）：

| 功能 | GPIO | 备注 |
|---|---|---|
| LCD D0–D7 | 39,40,41,42,45,46,47,48 | 8 位并口数据线 |
| WR (PCLK) | 8 | |
| RD | 9 | 读使能，初始化里用不到 |
| DC | 7 | |
| CS | 6 | |
| RST | 5 | |
| 背光 BCKL | 38 | |
| **LCD_POWER_ON** | **15** | 必须拉高，否则屏不亮（关键坑） |
| BOOT 键 | 0 | 也是下载模式 strapping |
| KEY 键 | 14 | |
| USB D-/D+ | 19/20 | 原生 USB（USB-Serial/JTAG），无外置串口芯片 |
| 电池 ADC | 4 | |

- 芯片：ESP32-S3，16MB Flash，8MB PSRAM（零售页与板级定义口径一致）。
- **`H569` 这个丝印/版本号未能在公开资料里核实**（web 检索无结果）。风险控制：按标准
  T-Display-S3（1.9" 170×320 ST7789 IPS）做，点亮阶段用测试图案实测 offset/mirror/invert/颜色序。

### 选型决策

1. **框架：ESP-IDF 5.5.3（用户指定，本机已装）**。关键依据：官方自带 i80 并口 + ST7789 的
   LVGL 示例，正好是本板需要的总线类型。
   - 已验证存在：`components/esp_lcd/include/esp_lcd_io_i80.h`
   - 已验证存在：`examples/peripherals/lcd/i80_controller/`（内置 LVGL 9.2.2 端口代码、
     ST7789 面板初始化、`esp_lcd_i80_alloc_draw_buffer` 双缓冲、flush 回调里做 RGB565 字节交换）
   - 该示例 `main/idf_component.yml` 只依赖 `lvgl/lvgl: "9.2.2"`（组件管理器拉取）。
2. **不启用 PSRAM（v1）**：170×320 双缓冲 2×34KB 放内部 RAM 足够，省掉 PSRAM 模式
   （octal/quad）配错导致开机失败的整类风险。
3. **LVGL 9.2.2 + 官方 i80 示例的移植路径**，不引 esp_lvgl_port（示例自带 ~40 行移植层，更可控）。
4. **PC 端自写 Python 上位机**（不用 AIDA64 / HWiNFO）：AIDA64 是付费软件，HWiNFO 免费版
   共享内存有时长限制；自写采集器还能同时走 USB 和 HTTP 两种传输，一套 JSON。

### 本机环境事实（实测）

- `PlatformIO Core 6.2.0` 已装（`~/.platformio`），但 `packages/` 下**没有** arduino-esp32 框架
  和 xtensa 工具链 → 走 Arduino 方案需额外下载；已选 ESP-IDF 故无关。
- ESP-IDF：`D:\ESP32-S3\Espressif\frameworks\esp-idf-v5.5.3`，
  python env `idf5.5_py3.11_env`（注意：`export.ps1` 硬找 py3.12 会失败，
  用 `tools/idf_tools.py export --format key-value` 绕过，见历史踩坑记录）。
- Python：`C:\Users\Lenovo\miniconda3\python.exe` 3.12.13；**psutil 未安装**（`import psutil` 报错）。
- 组件仓库 `https://components.espressif.com` 可达（HTTP 200）；PlatformIO registry 也 200。
- GPU：**NVIDIA GeForce RTX 5060 Laptop GPU**（`nvidia-smi` 实测可用：
  `16 %, 58 C, 1864 MiB / 8151 MiB, 29.15 W`）+ Intel UHD 集显 → **独显指标不需要管理员权限**。
- CPU：Intel Core i7-14650HX，16 核 24 线程。机型代码 `83NN`。
- 有 ToDesk / GameViewer / ExtensoDesk 三个虚拟显示适配器（枚举显卡时要过滤，否则会误报显卡名）。
- **写日志时板子未插在电脑上**：`Get-PnpDevice` 里没有 `VID_303A`（Espressif），
  `HKLM\HARDWARE\DEVICEMAP\SERIALCOMM` 只有 BthModem(COM3/4/8/9/26/27) → 实机烧录/点亮验证
  要等用户插板。

### 待验证 / 未知

- [ ] 板子丝印 `H569` 对应的具体版本（是否就是标准 1.9" 版）；点亮阶段实测。
- [ ] ST7789 在这块板上的 offset / mirror / invert / RGB-BGR 组合（微雪屏与本板不同，
      参考工程的参数不能照抄）。计划：烧「测试图案」四步试参数。
- [ ] USB-Serial/JTAG 在数据量 1Hz、约 300–600 B/帧 时的丢帧情况（需要实机压测）。
- [ ] 笔记本 CPU 温度：Intel 14 代 HX 需 MSR/内核驱动，v1 **不承诺**；GPU 温度由 `nvidia-smi` 提供。
- [ ] ESP-IDF 首次构建 + LVGL 组件下载耗时（正在测，见下条「进行中」）。

### 状态机验证（logicprobe，2026-11-10）

用 `logicprobe_verify` 对设备侧链路/新鲜度状态机（5→6 状态、22 条迁移）跑了结构性 + 对抗性检查。
模型存 `docs/design/link-state-machine.model.json`。三轮才对：

| 轮次 | verdict | 发现的真问题 |
|---|---|---|
| v0 | fail（6 error） | ①`stale-marked`（STALE 时 age==3）不变式在初始状态就违规——schema 规定 init 状态**无条件**参与 `var-in-range`，`min=3` 必挂；②运行态是**封闭 SCC 且无终态**（S3 liveness）；③`maxTicks=2` 被 A14 判「驻留超期」 |
| v1 | fail（4 error） | 补了 `SLEEP` 终态解决 SCC、删了 stale-marked、把 maxTicks 提到 3 → A14 仍报 `ticks=4` |
| v2 | **pass_with_findings**（0 error / 21 warning） | 想通了：A14 的驻留计数不会因为 `usb_frame` 重置 `age` 而清零，而「数据一直在流就一直在 ONLINE」**本来就是正确行为**，驻留时长无界 → `maxTicks` 表达不了「断流 3 秒内转 STALE」，删掉它，改用 `age` 守卫 + `var-in-range` 不变式覆盖 |

最终检查全过：S1 可达性、S2 无死锁、S3 活跃性、S4 确定性、S6 守卫完备、S7 三条不变式、
S8 变量域、A2/A4/A5/A6/A7/A8/A9/A10/A11/A12/A13。
剩余 21 条警告全是设计使然（10 条「该状态不可能发生的事件被忽略」、1 条「帧与 tick 顺序会影响结果」）。
**结论：这个状态机可以照着写代码了**——「绝不在旧数据上显示在线」「3 秒断流转 STALE」「USB 优先」
三条硬要求都有可执行模型背书。

### 环境踩坑（重要，后面每个人都要知道）

1. **`idf_tools.py export --format key-value` 输出的 `PATH` 是 Windows 分号形式，直接 `export` 进 bash
   会把 coreutils 全干掉**（`head: command not found` / `rm: command not found`）——第一版探测脚本
   就是这么废的。要用的话必须先把 `;` 转 `:`、`D:\` 转 `/d/`。
2. **在 bash 里 `export IDF_PYTHON_ENV_PATH=D:\...` 传给原生 python，值会被 MSYS 弄丢/改写**，
   于是 `idf.py` 报「python.exe doesn't exist! Please run the install script」——**假报错**，
   python 明明存在。正确做法：改用 `cmd.exe` 批处理跑 IDF 构建。
3. 即使用 cmd，`idf.py` 的导出 `PATH` 也**不带 System32**，`xcopy`/`dir` 之类会找不到；
   必须在导出后再 `set "PATH=%PATH%;%PATH_ORIG%"` 把原 PATH 拼回去。
4. **组件管理器连不上**：`https://components-file.espressif.com/...lvgl/lvgl.json` 超时
   （而 `components.espressif.com` 的 API 是通的）。官方 `i80_controller` 示例因此 configure 失败。
   → 决定不依赖组件管理器，改为把 LVGL 固定版本放进工程（见下一条）。

### M0 固件骨架（2026-11-10，已完成）

- 建了能编译通过的 ESP-IDF 5.5.3 工程：`firmware/{CMakeLists.txt, sdkconfig.defaults, partitions.csv, main/}`，
  模块按 `display/ ui/ link/ config/` 分层，接口头由 Lead 定（`lcd.h`/`lvgl_port.h`/`ui.h`/`link.h`/`cfg.h`），
  `.c` 先放占位实现保证随时可编译，再由各 teammate 填真实现。
- `bash tools/build.sh` 结果：**`tdisplay_pcmon.bin` 0x2fac0（约 195 KB），3 MB 分区剩余 94%**，
  目标确认是 `xtensa-esp32s3`（不是默认 esp32，之前 set-target 没生效过一次，见坑 4）。
- 分区表改成 16 MB 布局：nvs 24K / phy 4K / factory 3 MB / storage 1 MB（默认 1MB app 槽放不下 LVGL+WiFi）。

### 依赖落地：LVGL 不走组件管理器（2026-11-10）

- 现象：`idf.py` 的组件管理器报 `Cannot establish a connection to the component registry`
  （URL `https://components-file.espressif.com/.../lvgl.json`），但**同一个 URL 用 python 直接抓是 200**，
  IDF 自带 venv python 抓也是 200 → 组件的 HTTP 客户端问题，不是网络不通。
- 决定：**把 LVGL 9.2.2 固定版本 vendor 进工程**（`firmware/components/lvgl`，gitignore 掉，用
  `tools/fetch_deps.cmd` 一键拉取）。好处：完全离线可复现，不受 registry 抖动影响；代价：工程多 19 MB 源码。
- 裁剪踩坑：LVGL 的 `env_support/cmake/esp.cmake` 会把 `examples/`、`demos/` **无条件**加进 include 路径，
  删掉这两个目录会直接 CMake 报错 `Include directory ... is not a directory` → 两个目录必须存在（可以为空）。

### 又一个环境坑：.cmd 里不能写 UTF-8 中文（2026-11-10）

- 症状：`tools/idf_build.cmd` 一运行就报 `'f_build.cmd' 不是内部或外部命令`，而且把注释里的
  `tools\idf_build.cmd [build|flash|...]` 当成命令行执行（`|` 被当管道）。
- 根因：**cmd.exe 按 cp936 解析 .cmd 文件，UTF-8 中文注释变成乱码字节，把 `rem` 行内容当成命令**。
- 解决：**所有 `.cmd`/`.bat` 文件一律纯 ASCII**（注释也用英文）。bash 脚本（`tools/build.sh`）不受影响，中文随便写。
- 另：从 bash 直接 `cmd //c xxx.cmd` 有 MSYS 参数改写问题，统一走 `bash tools/build.sh` 这层包装
  （内部 `MSYS_NO_PATHCONV=1 cmd.exe /c <windows路径>`）。

### M1 上位机 pcmond 完成（2026-11-10，Lead 已复核）

teammate `pc-companion` 交付 `companion/`（`pcmond/` 12 个模块共 1208 行 + 9 个测试文件），Lead 独立复跑验收：

| 验收项 | 命令 | 结果 |
|---|---|---|
| 单测 | `python -m pytest companion -q` | **65 passed in 2.71s** |
| 单帧 | `cd companion && .venv/Scripts/python.exe -m pcmond --once` | 409 B 合法帧，含真实 GPU（RTX 5060 / 19% / 59°C / 2203 MiB / 29.7 W） |
| HTTP 鉴权 | `curl -H "Authorization: Bearer <token>" http://127.0.0.1:8787/api/stats` | 200；无 token / 错 token 均 **401**；`/health` 200；`/xyz` 404 |
| 不重复采集 | 连续两次请求比 `seq` | `7, 7` —— 证明 `/api/stats` 只回最新快照，不触发采集 |
| 帧大小 | `wc -c` | 409 B（契约上限 600 B，OK；24 线程时 `cores` 数组占大头） |

版本与环境（实测）：pcmond 0.1.0、Python 3.12.13（miniconda）、psutil 7.2.2、pyserial 3.5、
pytest 9.2.0、Windows 11 10.0.26100。pip 默认 index 已是清华 tuna 镜像（框架侧不用手动切源）。

**修正一条先前记录**：主机名是 `LAPTOP-4J1FBMVH`（14 字符），`83NN` 只是联想机型代码，不是 hostname。
本机局域网地址 `10.21.132.85`（另有 Tailscale `100.80.66.82`），pcmond 启动日志会打印全部地址。

**Windows 坑（真实踩到并修好，值得记）**：`socketserver` 的 `allow_reuse_address = True` 在 Linux 上
是「允许 TIME_WAIT 后立即重绑」，但在 **Windows 上 SO_REUSEADDR 的语义相反**——它允许第二个进程
**再绑同一个已在监听的端口**，于是两个 pcmond 实例会静默抢 8787，谁也不报错。修法：Windows 上改用
`SO_EXCLUSIVEADDRUSE`，非 Windows 才 reuse；现在第二个实例报 `WinError 10048` 并只关闭 HTTP 链路，
采集与 USB 继续跑（进程不退出）。

**`psutil.cpu_percent(interval=None)` 首调返回 0.0** 的经典坑也用 warmup 解决了（`--once` 也会先
warmup 再睡 0.25 s 采样），单测锁定了调用顺序。

**未验证（板子不在）**：USB CDC 真机联调（丢帧率、断线重连）、防火墙放行后的设备侧 HTTP 轮询。

### 并行构建隔离 + 仓库初始化（2026-11-10）

- 多人同时改固件时，共用一个 `build/` 会互相踩：实测给 `idf_build.cmd` 加了 `BUILD_DIR` 支持
  （`-B <绝对路径>` + `-D SDKCONFIG=<绝对路径>/sdkconfig` + `-D IDF_TARGET=esp32s3`）。
- 坑：第一次写成相对路径，`idf.py` 按**调用者的 cwd** 解析，build 目录跑到仓库根去了，kconfgen 直接失败
  → 必须传绝对路径（`%PROJ%\%BUILD_DIR%`）。改完实测 `BUILD_DIR=build-lead bash tools/build.sh` 通过，
  产物在 `firmware/build-lead/`，flash 参数 `--flash_size 16MB --flash_freq 80m` 正确。
- 现在固件同学各用 `build-display` / `build-link`，互不干扰；都已在 `.gitignore` 里。
- `git init` + 首个提交 `80d090b`（55 个文件），作为 M0+M1 的可回滚基线。

### M2 对外文档草稿（2026-11-10，Lead 已抽查）

writer 交付两篇（`docs/README.draft.md` 319 行、`docs/blog-draft.md` 222 行 / 约 4200 字），Lead 抽查方式：
- 规模与「未验证」标注数：README 7 处、博客 4 处；
- 反编造扫描：`grep` 「fps / 实测 / 点亮 / 烧录成功」只命中 1 处且是真实数据（`nvidia-smi` 实测 GPU 值）；
- 命令可执行性：README 里 `tools\fetch_deps.cmd`、`bash tools/build.sh`、`bash tools/build.sh flash COM5`、
  `setup.ps1`/`run.ps1` 与 `tools/`、`companion/` 的实际文件一一对上。
- 结论：没有发现编造内容，状态警示放在最前面，符合「未点亮必须说明」的要求。

**许可决定：MIT**（与 ESP-IDF 官方示例 CC0、LVGL MIT、参考项目 MIT 兼容）。已建 `LICENSE`（版权署名写
"T-Display-S3 PC Monitor contributors"，发布前可改成具体 GitHub ID）。

**工具可移植性**：`tools/idf_build.cmd` 里原来硬编码本机 ESP-IDF 路径，已改成可用环境变量覆盖
（`ESP_IDF_DIR` / `ESP_IDF_TOOLS_DIR` / `ESP_IDF_PYENV`），默认值仍是本机安装位置，克隆到别的机器不用改脚本。

### M2.5 固件链路层完成（2026-11-10，Lead 已复核离线测试）

fw-link 交付 `link.c`(369) / `link_usb.c`(316) / `link_http.c`(240) / `cfg.c`(178)，编译 `tdisplay_pcmon.bin`
约 1.18 MB（3 MB 分区剩 61%）。**它自己搭了一个主机离线测试**：把固件里**真实的 `link.c`** 用 MinGW gcc
配 FreeRTOS/esp_log/esp_err shim + IDF 自带 cJSON 编到 Windows 上跑。Lead 复跑 `bash run.sh` →
**61 checks / 0 failure**（含状态机全迁移、`ONLINE_* age<=2` 不变式、上位机真实 424 B 帧解析、
40 核截断到 32、越界夹取、坏 JSON 拒收）。

这条经验对这个项目很有价值：**没有板子也能验证固件行为**——只要被验证的模块不直接碰寄存器，
用 shim 把 IDF 头文件替换掉就能在 PC 上跑。已批准搬进仓库 `firmware/test/host_link/`。

**技术事实（fw-link 实测/查证，已接受）**：

1. **USB 收数据必须装驱动，不能读 console 的 stdin**：USB-Serial/JTAG 的 RX FIFO 只有 **64 B**，
   而一帧 JSON 是 ~424 B 的突发写入，console 的 no-driver VFS 轮询路径会丢字节。
   正确姿势：`usb_serial_jtag_driver_install()`（rx 4096）→ `usb_serial_jtag_vfs_register()`
   （重复注册返回 INVALID_STATE，忽略）→ `usb_serial_jtag_vfs_use_driver()`，
   让 esp_log 的 TX 与 JSON 行共用同一把锁，避免日志和数据行在 FIFO 里交错。
2. **上位机真实帧 424 B**（契约上限 600 B）；`host="LAPTOP-4J1FBMVH"` 正好 15 字符卡满；
   `gpu.name` 34 字符被设备端截到 19（`STATS_GPU_NAME_LEN=20`）。
3. **cfg 下发帧的真实字段名是 `wifi_ssid`/`wifi_pass`/`api_url`/`api_token`**（architecture §4 里写的
   `ssid`/`pass`/`url`/`token` 是简写）→ 以 `companion/pcmond/usb_link.py` 的实现为准，**architecture.md 已按此修正**。
4. **USB 在线时 WiFi 帧是"显式忽略"**（模型里那条迁移没有 updates），不是"只刷新"：
   这样状态灯不会在数据其实来自 WiFi 时长亮 USB。HTTP 轮询在 `LINK_ONLINE_USB` 时整个跳过。
5. 主机测试用的 gcc：`D:\ESP32-S3\x86_64-8.1.0-release-posix-seh-rt_v6-rev0\mingw64\bin\gcc.exe`
   （不在 PATH 上）；MinGW 8.1 没有 `strlcpy`，shim 里用宏映射。

**未实机验证**：USB 实际收帧与 hello/cfg 往返、WiFi 连接与 HTTP 轮询、NVS 持久化、串口命令、深睡。

### M2.6 离线测试入仓 + 标定模式接线（2026-11-10）

- fw-link 把主机测试搬进 `firmware/test/host_link/`（`run.sh` + `test_link.c` + `shim/` + `frame.json` + `README.md`）。
  Lead 复验：仓库根跑 `GCC=<mingw gcc> bash firmware/test/host_link/run.sh` → **61 checks, 0 failure**；
  不设 `GCC` 且 PATH 里没有 gcc 时会打安装提示并 `exit 2`（实测过）。产物目录已加进 `.gitignore`。
  → 项目现在有一条「没有板子也能验证固件行为」的路径，这对后面改状态机/解析器非常重要。
- Lead 在 `main.c` 里接了**屏幕标定模式**：长按 BOOT 2 秒进入/退出，进入后短按 BOOT 切下一组候选参数
  （调用 `lcd_test_variant_next()`），标定模式下主循环跳过 UI 刷新，避免 LVGL 抢画测试图案；
  退出时置 `s_force_repaint` 强制重绘回 UI。
- 新增 `docs/bringup.md`：上板点亮/黑屏排查表/4 组参数标定判断标准/上位机联调/WiFi 联调/结果回填要求。
  这份是「板子到手当天照着做」的操作手册，**里面屏幕相关步骤本身尚未执行过**。

### M3 显示层完成 + 一次真实的集成 bug（2026-11-10）

fw-display 交付：`lcd.c`(459 行) / `lvgl_port.c`(203) / `ui.c`(485) / `ui_pages.c`(163)，
`bash tools/build.sh` → **0 error 0 warning，bin `0x12ddf0` ≈ 1.18 MB，3 MB 分区剩 61%**
（比骨架的 195 KB 大，是因为 LVGL 全量链接进来了）。

**它抓到我写的一个真 bug（值得记下来，因为这类"跨模块状态没同步"最容易漏）**：
- 现象（静态推演，未上板）：BOOT 长按退出标定模式后，屏幕一直停在测试图，UI 回不来。
- 根因：测试图是**绕过 LVGL 直接写面板**的，所以 `ui_update()` 里加了
  `if (lcd_test_pattern_active()) return;` 防止 UI 把测试图盖掉；而我 main.c 的退出分支只置了
  `s_force_repaint = true`，没清 lcd 的测试模式标志 → `ui_update()` 早退，`s_force_repaint` 被消费掉，屏就卡在测试图。
- 修法（两处一起）：
  1. `ui.h` 新增 `ui_exit_test_mode()`（清标志 + `lv_obj_invalidate(lv_screen_active())` + 置 `s_force_full_update`），
     因为测试图不是 LVGL 画的，不整屏 invalidate 就只有后续变化的 label 重绘、其余还留着测试图；
  2. main.c 退出分支改成在 `lvgl_lock()` 里调 `ui_exit_test_mode()`。
- **第二层问题**：修完发现「标定模式」有两个状态源（main 的 `s_lcd_test_mode` + lcd.c 的 `s_test_active`），
  从串口 `lcd` 进标定后 BOOT 开关会错乱（以为要"进入"其实已在里面）。Lead 进一步改成**单一状态源**：
  main.c 直接 `lcd_test_pattern_active()` 判状态，删掉自己的布尔量；BOOT 长按 = 反向切换，BOOT 短按 = 标定中就切参数、否则切亮度。
  `app_task` 里跳过 UI 刷新的判断也改用同一个函数。

**其它技术事实（fw-display 实测/查证）**：
- **旋转不用 `esp_lcd_panel_mirror/swap_xy`，改为一次写 MADCTL(0x36)**：BGR 位没有 driver setter，
  且重建 panel 会让 LVGL 缓存的 handle 悬空。gap/invert 仍走 driver API。
- **draw buffer 两块 `esp_lcd_i80_alloc_draw_buffer(320*40*2)` = 51.2 KB 内部 RAM**（`INTERNAL|DMA` 拿对齐），
  不开 PSRAM；LVGL 堆 64 KB 是静态 bss。启动后内部堆 ≈153 KB，扣掉 buffer 剩 **≈102 KB 给 WiFi/HTTP/USB**。
  判断：纯 HTTP 不开 TLS 时 lwIP+WiFi STA 峰值约 60–70 KB，够用；**若实机报 heap 不足，把 `LCD_DRAW_BUF_LINES` 从 40 降到 24 即可**（一处改）。
- **i80 驱动内部没有跨任务互斥**（查了 `esp_lcd_panel_io_i80.c`，只有自旋锁保护 bus 成员）→ 所以 main.c 里
  所有 `lcd_test_pattern*` 调用都包在 `lvgl_lock()` 里；fw-display 在测试图函数内部另有"置标志 → 等 100 ms"兜底。
- **Montserrat 字体没有 ↓↑ 字形**（查了 vendored 字体的 unicode_list，0x2193/0x2191 不在；0xB0「°」在）→
  NET 卡用 ASCII `DN x.x  UP x.x MB/s`。
- 布局修正：原设计顶栏 20 + 右列 48/40/76 = 184 > 170 放不下 → 右列压成 **42/34/66**（MEM 百分比仍用 Montserrat 20）；
  P1 的 24 根柱 12px+2px = 334 > 320 → 改 **11px 柱宽**（总 310）。
- 曲线按 `pc_time` 的秒桶推进（上位机 1–2 Hz，按帧推会变成 2 点/秒，60 点窗口缩成 30 s）；STALE 时不推点。
- 顶栏移到页面容器**外**，两页共享，保证 P1 也能看到 STALE 状态。

**仍未实机验证**：屏幕能否点亮、4 组 ST7789 参数哪组对、i80 时序/DC 电平、颜色位序、按键手感、实际帧率。

### M4 发布准备：README 合并入根目录（2026-11-10）

- `docs/README.draft.md` 合并成仓库根 **`README.md`**（391 行），合并时做了三件事：
  1. 删掉草稿头的说明注释；
  2. **修正相对链接**——草稿是按 `docs/` 目录写的，搬到根目录后 `journal.md` / `design/*.md` /
     `blog-draft.md` 这些链接全断，统一加 `docs/` 前缀；用脚本逐个校验 8 个相对链接全部命中（0 个 MISS）；
  3. 删掉合并后的草稿文件，避免仓库里两份 README 打架（历史留在 git 里）。
- 精确字节数（`0x12ddf0` / `1,236,4xx`）全部改成「约 1.18 MB / 当前 3 MB 分区剩 61%」——
  固件每次改代码 bin 都会变几十~几百字节，写死 byte 数一定会过期，这是个容易犯的文档病。
- 待用户提供后替换：`README.md` 与 `docs/blog-draft.md` 里的 GitHub 仓库地址（现在还是
  `https://github.com/<your-id>/tdisplay-s3-pc-monitor` 占位）。

### 全仓验收（2026-11-10，Lead 自己跑的最后一轮）

| 项 | 命令 | 结果 |
|---|---|---|
| 固件编译 | `bash tools/build.sh` | 0 error / 0 warning，`tdisplay_pcmon.bin` 约 1.18 MB，3 MB 分区剩 61% |
| 固件离线行为测试 | `GCC=<mingw> bash firmware/test/host_link/run.sh` | **61 checks, 0 failure** |
| 上位机单测 | `python -m pytest companion -q` | **65 passed** |
| 上位机接口 | `curl` + token | 200 / 401 / 404 / seq 不重复采集，全部通过 |

**这个项目现在的真实状态**：代码链路是完整闭环，但**屏幕一次都没亮过**（用户还没把板子插上电脑）。
所有跟画面有关的参数（ST7789 offset/mirror/invert/RGB、i80 时序、20 MHz 稳不稳）都是"照官方示例 +
静态推演"，必须上板标定。`docs/bringup.md` 就是为那一刻准备的手册。

### 团队分工（2026-11-10 开工）

| 成员 | 负责 | 写作用域 | 任务 |
|---|---|---|---|
| lead（我） | 架构/契约/journal/骨架/集成/验收 | `docs/`、`firmware/main/main.c`+头文件+CMake 等 | — |
| pc-companion | Windows 上位机 pcmond | `companion/` | task-1 |
| fw-display | i80+ST7789 驱动、LVGL 移植、两页 UI | `firmware/main/display/`、`firmware/main/ui/` | task-2 |
| writer | README + 博客草稿 | `docs/README.draft.md`、`docs/blog-draft.md` | task-3 |
| fw-link | USB/WiFi 双链路 + 状态机 + NVS 配置 | `firmware/main/link/`、`firmware/main/config/` | task-4 |


