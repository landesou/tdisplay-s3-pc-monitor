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

### M5 **屏幕点亮**（2026-11-10，实机，用户确认画面正确）

这是整个项目最关键的一段，也是最踩坑的一段。完整链路：

**1. 烧录先卡在进不了下载模式**
- 现象：`esptool` 报 `Write timeout`，连不上。
- 根因：出厂固件（GIF 播放 demo）**不读 USB CDC 的接收通道**，so 主机写入的同步字节被 NAK；
  pyserial 单独写 8 字节同样超时，证明跟 esptool 无关。
- 解法：按住 **BOOT** 键再拔插 USB（GPIO0 低 → ROM 下载模式），写入正常。
- 附：烧完芯片会**停在下载模式**（ESP32-S3 原生 USB 的复位只能进 ROM，GPIO0 被 USB-JTAG 复位路径拉低）。
  **`esptool --after watchdog_reset flash_id` 能让它自己跳回 App**——这条已固化进 `tools/idf_build.cmd`，
  现在「烧录 + 自动重启」一步完成，不用再拔插。（`esptool run` 不行，只会停在 bootloader。）

**2. 固件跑起来了，但屏幕不更新**
- 串口能抓到自己的启动日志（`lcd init done`、`lvgl task started`、`ui init done`），外设全过。
- 但屏幕上一直是**上一版固件留在 GRAM 里的旧像素图**，刷测试图/切参数都没反应。
- 关键推理：背光是亮的（能看到旧图）→ 供电没问题；旧 GRAM 内容原样保留 → 我们的写根本没进面板。

**3. 用「裸总线自检」把问题二分**
在 `main.c` 里加了开机自检（`BOOT_DISPLAY_SELFTEST`）：**绕过 LVGL 和 panel 驱动**，直接
发 `CASET/RASET/RAMWR + tx_color` 刷纯色。这里踩到两个细节：
- 第一版「一次设窗口、多段续写」只有**一小条**变色 —— ST7789 在 CS 每次上升沿后不一定续写窗口；
  改成**每块都重设窗口 + 重发 RAMWR**（与大驱动 `draw_bitmap` 一致）才正确。
- 自检必须显式发 `SLPOUT(0x11)` + `DISPON(0x29)`，否则面板在 sleep-in 状态，写进去也不显示。

**4. 真正的元凶：`LCD RD`（GPIO9）浮空**
- 现象：自检刷红/绿/四象限时，屏幕都是「**一半白 + 一半花屏**」。
- 根因：**RD（读使能）没有驱动成高电平**。ST7789 的 RD 低有效，悬空/被拉低时面板会朝 8 位数据总线
  反向驱动，与我们的写操作抢总线 → 数据被打花，未覆盖区域保持上电随机值（发白）。
- 证据：LilyGO 官方 Arduino 配置 `lib/TFT_eSPI/User_Setups/Setup206_LilyGo_T_Display_S3.h`
  里 `TFT_RD 9` 是当普通输出脚驱动的；而 ESP-IDF 的 `esp_lcd_i80_bus_config_t` **根本没有 rd_gpio_num 字段**，
  谁也没帮我们管这根线。
- 修法：`lcd_init()` 之前把 GPIO9 `gpio_config` 成输出并拉高（`main.c` 里 `RD hold HIGH`）。
- 同时把 PCLK 从 20 MHz 降到官方值 **16 MHz**（官方注释：过高会导致 mosaic）。

**5. 结果**
- 修完 RD 后，屏幕立刻出字：**CPU / GPU / MEM / NET 四张卡片 + 顶栏**，用户确认「画面完全对了」。
- 最终采用的 ST7789 参数 = V0：**MV + MX + invert + RGB + gap(0,35)**（170 宽在 240 列里居中 35）。
- 上位机 `pcmond` 同时接上：USB 推帧 1 Hz，HTTP 侧 `seq` 连续递增（60→63），界面数字实时刷新。

**这条"RD 浮空"值得单独立卡**：它不会报任何错，`esp_lcd` 的初始化全部返回 `ESP_OK`，
日志也一切正常，只有画面是花的——典型的"软件全对、硬件抢总线"。

### M6 实机联调通过（2026-11-10，用户确认）

在屏幕点亮之后，把整条链路跑通并做了对照验证：

| 验证 | 方法 | 结果 |
|---|---|---|
| 静态显示 | 上电后看屏 | 320×170 横屏，顶栏 + CPU/GPU/MEM/NET 四卡 + 曲线，用户确认「画面完全对了」 |
| 数据实时性 | 用 10 个忙循环把 CPU 压满 60 秒 | PC 端 `pcmond --once` 报 **CPU 97.5%**，同时用户确认「屏幕 CPU 也冲上去了（90% 以上）」 |
| 数值正确性对照 | 同一时刻取 PC 端 ground truth | MEM 74.7%（12026/16108 MiB）与屏上一致；GPU 24% / 70 °C / 2865 MiB 与 `nvidia-smi` 一致 |
| NET 为 0 的疑问 | 查 ground truth | 机器空闲时 `net.down=0.0 / up=0.0` 是真的 0，不是显示 bug |
| USB 传输 | pcmond 日志 + 设备帧号 | 1 Hz 推帧，`seq` 连续递增（60→63），无断流 |

**一条被证伪的怀疑**：一开始以为"数值不对"，其实是**对比时刻不同**造成的错觉——
CPU/GPU 每秒波动很大，拿某一秒的屏上数字去比另一秒的 PC 采样必然对不上。
用"人为加压 → 看屏是否同步冲到 90%+"这种**定性对照**才靠谱。这条经验对以后调试监控类项目很有用。

**当前项目真实状态**：固件 + 上位机 + USB 链路 + 屏幕显示全部在真机上验证通过；WiFi 双模代码已实现但**未实机验证**
（需要用户提供 WiFi 凭据）；托盘已实现（等 pc-companion 做真机复测）；GitHub 发布等用户提供账号。

### M7 新鲜度状态机的真机验证（2026-11-10）

logicprobe 建模时立的三条不变式，现在有了实机证据：

| 不变式 | 验证方法 | 结果 |
|---|---|---|
| 3 秒无新帧必须转 STALE，且不得把旧数据当实时 | 停掉上位机 5 秒再启动 | 用户确认「看到了：变灰/黄点，然后恢复彩色」 |
| STALE 收到任意一帧回到 ONLINE | 同上（重启 pcmond） | 1–2 秒内恢复彩色 |
| ONLINE_USB 状态下 age ≤ 2 | 与上条同一过程 | 无中间态闪烁（恢复即彩色，不是先黄后绿） |

这条比"模型验证"更硬的证据意义在于：**状态机不是纸上谈兵**，PC 端断流这一点在真机上确实被 3 秒内捕捉到了，
而且没有出现"卡在 STALE 不恢复"或"旧数据冒充在线"这两种最典型的监控类 bug。

### M8 WiFi 双模实机验证通过（2026-11-10）

| 步骤 | 结果 |
|---|---|
| 串口配置：`wifi LAN_HotPoint <redacted>` / `url http://10.21.132.85:8787/api/stats` / `token <24字符>` | 全部 `ESP_OK`，`cfg` 回显正确（token 只显前 4 位） |
| 设备连 AP | `wifi:connected with LAN_HotPoint, channel 11, rssi -50`，DHCP 拿到 **10.21.132.204** |
| 设备轮询 HTTP | `netstat` 抓到设备 IP 每秒向 `10.21.132.85:8787` 建连（ESTABLISHED/SYN_RECEIVED） |
| **端到端** | 把上位机切成**只用 HTTP**（`pcmond --no-usb`，USB 完全不出帧）后，用户确认**屏幕数字仍在实时变化** |

这条验证很关键：它证明「USB CDC」和「WiFi HTTP」两条链路不是"代码写了但只有一条能用"，
而是**两条都独立跑通过**。同时它反过来验证了状态机的仲裁语义（USB 无帧时自动落到 ONLINE_WIFI）。

> 顺带发现一个不影响功能的小问题：设备连上 AP 后日志里会出现一次
> `w:sta is connected, disconnect before connecting to new ap`——`link_http` 在 `got_ip` 之后又调了一次
> `esp_wifi_connect()`，属于重复连接调用，功能无影响但可以清理（记在待优化项）。

### M9 开源发布（2026-11-10）

- 仓库：**https://github.com/landesou/tdisplay-s3-pc-monitor**（Public，MIT）。
- **网络限制与绕法**（这台机器上很关键，换机器要注意）：
  - `https://github.com` 和 `https://api.github.com` 都**不通**（curl 直接 000 / 连接拒绝）→ 所以
    **gh CLI 装上也用不了**（它全靠 api.github.com）；
  - 但 **SSH over 443 通**：`ssh -T -p 443 git@ssh.github.com` → `Hi landesou! You've successfully
    authenticated`；remote 用 `ssh://git@ssh.github.com:443/landesou/tdisplay-s3-pc-monitor.git`，
    推送时加 `GIT_SSH_COMMAND="ssh -p 443"`；
  - GitHub 仓库本身必须由用户在网页端手动建（API 不通，命令行建不了仓）。
- 提交作者已统一重写为 `landesou <landesou@users.noreply.github.com>`
  （发布前用 `git rebase --root --exec 'git commit --amend --no-edit --reset-author'` 批量改），
  默认分支 `main`。
- 文档同步：README 状态改为「USB + WiFi 双链路均已实机验证」，已验证清单补 WiFi / 托盘 / 新鲜度三条，
  未验证清单精化为 CPU 温度 / 帧率 / 长时间稳定性 / NVS 与深睡 / WiFi 断线重连 / 其它板；
  博客替换为真实仓库地址。

### 项目最终状态（2026-11-10 收尾）

| 目标项 | 状态 |
|---|---|
| 320×170 横屏桌面监控仪 | ✅ 实机点亮、UI 正常（用户确认） |
| ESP-IDF 5.5.3 + LVGL 9 固件 | ✅ i80 并口 ST7789 驱动、两页 UI、USB CDC + WiFi HTTP 双链路 |
| 新鲜度状态机 | ✅ logicprobe 0 error + 真机 STALE 恢复验证 |
| Windows 上位机 pcmond | ✅ 采集/双传输/开机自启/托盘，单测 90 passed |
| 开源到 GitHub | ✅ https://github.com/landesou/tdisplay-s3-pc-monitor |
| README + 记录文章 | ✅ README 已发布；博客草稿 4700+ 字在 `docs/blog-draft.md` |
| 全过程记录 | ✅ 本文件（M0–M9） |

**已知未做**：实机照片（README 已标注待补）、CPU 温度（硬件限制）、帧率与长时间稳定性实测、
WiFi 断线重连/漫游、3D 打印外壳。

### M10 发布后的安全事故与补救（2026-11-10，记录教训）

**问题**：第一次推送后自查发现，仓库历史和文件里带进了两处敏感信息——**WiFi 密码**（写进了 journal 的
WiFi 验证表格）和**真实的 Bearer Token**（写死在 `companion/tests/test_tray.py` 的测试常量里）。
仓库当时是 Public。

**补救步骤**（已全部完成，可复用）：
1. 工作区先脱敏：journal 里密码换成 `<redacted>`，测试里的 token 换成 `DUMMY-TOKEN-FOR-TESTS`；
2. `git filter-branch --tree-filter` 重写**全部提交**，逐文件 sed 替换这两串；
3. 删掉 filter-branch 的备份引用 `refs/original/*`，`git reflog expire --expire=now --all` +
   `git gc --prune=now --aggressive`，让旧对象真正不可达；
4. `git push --force` 覆盖远端历史；
5. 复验：`git grep` 扫 `git rev-list --all`（含 origin/main）→ **命中 0**，本地/远端 HEAD 一致。

**教训**：
- 「把调试过程原样写进 journal」是个好习惯，但**凭据要在写进去的那一刻就脱敏**，不能等发布前再查；
- 测试夹具里的 token 即使是"本地随便一个值"，也**绝不要复制真实的**；
- 发布前跑一遍 `git grep -I -E "<密码>|<token>" $(git rev-list --all)` 应该是必做动作；
- 已经推送过的历史即使 force-push 覆盖，GitHub 侧仍可能短期保留不可达对象 → 这种时候最稳的是
  **直接轮换凭据**（换 token / 换 WiFi 密码），而不是只靠改历史。

> 本文档后续所有命令、日志、配置示例里，密码与 token 均已脱敏为占位符。

### M11 实机照片入库 + CSDN 版文章（2026-11-10）

- 用户提供实机照片（1280×960）：亚克力外壳、屏幕实时显示 GPU 29% / 74 °C / 41 W、
  MEM 72%（11636/16108 MB）、NET DN 0.1 UP 0.8 MB/s、CPU 30% / 2200 MHz、右下角 60 点曲线，
  顶栏状态灯是绿色 + `WIFI 0s`（说明当时走的是 WiFi 链路、帧延迟 0 秒）。
- 入库两张：`docs/images/device.jpg`（原图，README 与博客用相对路径引用）
  和 `docs/images/device-cover.jpg`（裁切成 1210×660 的封面，给 CSDN 首图/仓库 Social preview 用）。
- 一个被证伪的担心：放大量过顶栏文字，确认不是被截断，就是 `WIFI 0s`（WiFi 在线 + age=0s），
  标定排版没问题 —— **看图要先放大再下结论**，差点为一个不存在的 bug 去改代码重烧。
- 新增 `docs/csdn-article.md`（CSDN 发布版，约 4450 中文字，含 `@[toc]` 目录、GitHub 链接 3 处）。
- **图片外链踩坑**：`raw.githubusercontent.com` 在本机/国内**不可达**（curl 返回 000），
  直接写 raw 地址会让 CSDN 读者看到裂图；实测 **`cdn.jsdelivr.net/gh/<user>/<repo>@main/<path>` 可达（200）**，
  于是文章里改用 jsDelivr CDN。备选：`gh-proxy.com/<raw url>` 也 200。
  如果哪天 jsDelivr 也挂，最后的稳妥做法是把图片直接传进 CSDN 编辑器让它转存自己的 CDN。

### M12 新功能：DSH 页（token 消耗 + 进程监控）立项（2026-11-10）

用户提出：按一下板载按键进入「DSH 进程监控 + token 消耗」页。先做可行性调研，**两条数据源都实测过**：

**1）token 用量来自 DSH 会话日志**（`~/.dsh/sessions/<项目>/<session-xxx>/session.v4.jsonl.zstd`）

| 实测项 | 结果 |
|---|---|
| 每条 `assistant/message` | 带 `data.usage = {inputTokens, outputTokens, cacheReadTokens, cacheWriteTokens, totalTokens}` |
| 时间戳 / 模型名 | 顶层 `time`（毫秒 epoch）/ `data.message.source.model`（实测 `deepseek-flash`） |
| 规模 | 152 个会话文件 / 131 MB；**24h 内有改动的只有 7 个（9.3 MB）** |
| 解析耗时 | 单个解压后 8.1 MB 的会话 **0.03 秒** |
| 其它可用标记 | `agentId` / `teamMember` / `subagent`（以后可以按主代理/队友分开统计） |

→ 按 `(path, mtime, size)` 缓存 + 只重解析变动文件，1 Hz 刷新毫无压力（每帧最多 0.1 s，且可 5 s 节流）。

**2）进程监控**：psutil 枚举 name 含 `DeepSeek Harness` 的进程，当前实测 **10 个 / 合计 RSS 906 MB**
（Electron renderer/GPU/utility + agent worker），再探 10899 端口是否 LISTENING。

**3）费用估算用官方峰谷价**（2026-11 查 `api-docs.deepseek.com/zh-cn/quick_start/pricing/`）：

| `deepseek-flash`（元/百万 tokens） | 空闲时段 | 高峰时段 |
|---|---|---|
| 输入（缓存命中） | 0.02 | 0.04 |
| 输入（缓存未命中） | 1 | 2 |
| 输出 | 4 | 8 |

高峰 = 北京时间周一至周五 9:00–12:00、14:00–18:00（法定节假日除外），其余时段空闲；**空闲价是高峰的一半**。
所以费用**逐条按该条 `time` 判峰谷再取价**，比"均价 × 总量"准得多——这是这个功能最容易做糙的地方。

**契约**：帧里加**可选** `dsh` 对象（`docs/design/architecture.md` §2.5），固件 `stats_t.dsh`
（`dsh_stats_t`，含 `valid` 标志）。缺字段时设备显示 `--`，老帧/老固件互相兼容。

**分工**：pc-companion → `pcmond/dsh.py` 采集器 + 帧字段 + 测试（task-6）；fw-link → `link.c` 解析 +
离线断言（task-8）；fw-display → UI 第 3 页 + 三页循环（task-7）；Lead → 契约、`stats.h`、集成、烧录联调、文档。

**诚实边界（要写进 README）**：只统计本机 DSH 日志（不含网页版/其它客户端）；日志落盘才有数（延迟 1–2 s）；
费用是估算非账单；峰谷判断不查法定节假日表（简化）。

### M13 DSH 页集成验证通过（2026-11-11 上午验收）

**三个同学交付 + Lead 集成**：

| 模块 | 交付 | 验收结果 |
|---|---|---|
| pcmond 采集（pc-companion） | `dsh.py`(264) + `dsh_parse.py`(195) + `test_dsh.py`(337) | pytest **112 passed / 1 skipped**（原 90 无退化） |
| 固件解析（fw-link） | `link.c` 加 dsh 段 + int64 `j_u64()` | 离线测试 61 → **83 checks 全绿**，状态机一行未动 |
| 固件 UI（fw-display） | `ui_pages.c` DSH 页（15 个对象）+ 三页循环 | 编译 0 error / 0 warning |
| Lead | 契约 §2.5、`stats.h`、cfg 页号、集成烧录 | 实机第 3 页确认可用 |

**实机结果**（用户确认）：「第 3 页出来了，有 TOKENS / 费用 / REQ / 进程，数字在变」。
采集数字举例（2026-11-10 晚）：today 353M tokens / `~CNY 12.97` / REQ 1125 / 9 个 DSH 进程 / RSS 1055 MB /
缓存命中 99%；week 619M / ¥33.53 / 2381 REQ。帧大小 **621–752 B**（带 dsh，1 Hz 无压力）。

**跨零点行为**（次日 09:22 实测）：`dsh.today` 全部归零、`week` 仍计 619M——分桶按本地时区日期，符合预期。
**`proc.cpu_pct` 是多核累加**（实测 165% = 1.65 核），和 `top` 口径一致，README 已注明"可能 >100%"。

**这一轮踩到 / 决定的事**：

1. **`≈` 和 `¥` 在 Montserrat 里没有字形**（查过字体 unicode 表）→ 费用显示 `~CNY 12.97`。
   为两个字符加中文字体（几百 KB flash + 转换工具链）不值得。
2. **源码里不能出现 GBK 编不出的字符**（`•` U+2022、`¥` U+00A5）：编译器诊断一回显，
   `idf.py` 在 Windows 上按 GBK 写日志会抛 `UnicodeEncodeError`，**整个构建挂掉但不是编译错误**
   （fw-display 第一次 build 就卡在这，浪费一次全量编译）。已写扫描脚本确认固件源码里 0 个这类字符。
3. **契约帧长上限从 600 B → 1 KB**：带 dsh 实测 621–752 B；两端缓冲都是 1024 B，超过会整行丢弃/截断，
   所以设备侧对超长行要留 warning。
4. **页号被 cfg 夹在 0..1**（`s_page = (page > 1) ? 1 : page`）→ 第 3 页重启后丢了，改成上限 2。
5. **psutil 性能坑**：`process_iter(["name","memory_info"])` 在 381 个进程上要 **1.66 s**，
   改成先只枚举 name、命中 DSH 进程再取 memory_info → **0.006 s**；缓存命中时整个 refresh 0.05 s。
6. **费用逐条判峰谷**：不是"均价 × 总量"。晚上多为空闲半价，所以 3.5 亿 token 才 12.97 元；
   代价是不能查法定节假日表（简化，README 写明）。
7. **无数据时不发 `dsh` 字段**（而不是 `valid=false`）：设备端 `valid` 只看"帧里有没有"，
   老帧/老固件互相兼容（离线测试专门断言两种帧）。

**未验证**：与 DSH 界面/官方账单逐项对账（只和独立解压脚本对过）；节假日峰谷；长时间（数天）缓存文件增长。

### M14 峰谷时段徽标（v1.2，2026-11-11 实机确认）

需求：DSH 页要能看出「现在是不是高峰时段」（高峰贵一倍）。

**设计（比"红↑/绿↓"多做两层编码）**：

| 层 | 内容 | 为什么 |
|---|---|---|
| 形状 | 上箭头 / 下箭头（**位图**） | 形状本身携带信息，不依赖颜色（红绿色盲、黑白截图也能分辨） |
| 文字 | `PEAK x2` / `OFF x1` | 色弱用户 & 无颜色场景仍可读；倍数直接说明"贵几倍" |
| 倒计时 | `11h02m` | 真正可行动的信息：还要等多久切换到便宜时段 |

位置：DSH 页右卡费用行正下方，总高 16 px；`rate` 段缺失时三个对象一起隐藏（不占位、不显示 0）。

**关键技术点：字体里没有箭头字形。**
查了 vendored `lv_font_montserrat_14.c` 的 unicode 表，只有 HOME / DOWNLOAD / TINT / SHUFFLE 等少数图标，
`LV_SYMBOL_UP`(0xF077) / `LV_SYMBOL_DOWN`(0xF078) **都不在**。三条路：
① 给字体加字形（要 lv_font_conv + 占 flash）；② 用 ASCII `^`/`v`（丑且不像箭头）；
③ **生成 12×12 A8 位图 + LVGL image recolor**（两个箭头共 288 B，深浅可调）。
选 ③：`firmware/main/ui/ui_icons.h` 是脚本用 PIL 8× 超采样画三角形再 LANCZOS 缩到 12×12
（自带抗锯齿），生成两个 `lv_image_dsc_t`；UI 用 `image_recolor` 上色（红=峰、绿=谷）。

**契约新增 `dsh.rate`（§2.5）**：`{"peak": bool, "mult": 1|2, "until_min": int}`。
**峰谷规则只在 PC 侧实现一份**（`dsh_parse.is_peak()` / `next_rate_boundary()`），设备只显示——
上次标定模式"两个状态源"的教训，这次一开始就把规则收敛到单点。

边界规则（PC 侧，单测锁死）：工作日 9–12、14–18 高峰；高峰中 → 12:00 / 18:00；
工作日空闲 → 9:00 / 14:00 / 次日 9:00；**周五 18:00 后与整个周末 → 下周一 9:00**；
`until_min = ceil(剩余秒/60)`；不查法定节假日表（简化）。

**验证**：
- pytest **119 passed / 1 skipped**（+7 条 rate 用例，含周五/周末边界、向上取整、30+ 采样点
  "边界时刻 is_peak 必翻转"的一致性测试，防规则漂移）；
- 固件离线测试 **83 → 100 checks**（rate 四字段、有 dsh 无 rate 时 `rate_valid=false` 但 `dsh.valid` 仍 true、
  越界夹取 mult:0→1/5→2、until_min:-5→0/70000→65535）；
- 实机（2026-11-11 21:58，周三空闲）：**绿↓ + `OFF x1` + `11h02m`**，用户确认位置不挤、显示正常。

**这轮踩的坑**：烧录前强杀 pcmond（`Stop-Process -Force`）时它正持有串口句柄，
**ESP32-S3 的 USB 直接挂死、设备从系统里消失**（`VID_303A` 没了），只能拔插恢复。
以后流程固定为：**先让 pcmond 优雅退出（托盘"退出"），等 1 秒再烧**；实在挂了就拔插。

**帧长**：带 dsh+rate 实测 **672–797 B**（不同序列化口径），仍在 1 KB 契约 / 1024 B 缓冲内。

### 团队分工（2026-11-10 开工）

| 成员 | 负责 | 写作用域 | 任务 |
|---|---|---|---|
| lead（我） | 架构/契约/journal/骨架/集成/验收 | `docs/`、`firmware/main/main.c`+头文件+CMake 等 | — |
| pc-companion | Windows 上位机 pcmond | `companion/` | task-1 |
| fw-display | i80+ST7789 驱动、LVGL 移植、两页 UI | `firmware/main/display/`、`firmware/main/ui/` | task-2 |
| writer | README + 博客草稿 | `docs/README.draft.md`、`docs/blog-draft.md` | task-3 |
| fw-link | USB/WiFi 双链路 + 状态机 + NVS 配置 | `firmware/main/link/`、`firmware/main/config/` | task-4 |


