# pcmond — T-Display-S3 PC 上位机

采集本机 CPU / 内存 / GPU / 网络 / 磁盘指标，按统一契约（[`docs/design/architecture.md`](../docs/design/architecture.md) §2）
打成**一行紧凑 JSON**，同时通过 **USB CDC 串口** 和 **HTTP** 推给 T-Display-S3。

- 采集与传输解耦：采集线程固定周期（默认 1.0 s）产出一帧放入 `queue.Queue(maxsize=2)`，
  队列满丢最旧帧，**采集永不阻塞**；USB 与 HTTP 各自读最新快照。
- HTTP 的 `/api/stats` 直接返回最近一帧，**不为每个请求重新采集**。
- 所有异常都降级：串口拔掉自动重连；`nvidia-smi` 不存在/超时（3 s）时 GPU 字段发 `null`；
  HTTP 端口被占只关闭 HTTP，采集和 USB 继续。
- 可选**系统托盘**（pystray + Pillow）：状态圆点 + 右键菜单，没装依赖自动降级、不影响运行。
- 可选 **DSH 数据源**：扫 `~/.dsh/sessions` 统计 token 消耗（今日/近 7 天 + 费用估算）+ DSH 进程监控，
  帧里加契约 §2.5 的 `dsh` 对象；独立线程 5 s 刷新，没有数据时不发该字段。
- 可选 **图形界面**（tkinter，标准库）：状态 + 设置窗口；打包成单文件 `pcmond.exe` 后双击即用，
  目标机器**不用装 Python**（见「打包版」一节）。
- 不需要管理员权限、不装驱动、不改系统设置。

## 目录结构

```text
companion/
├── pcmond/
│   ├── collectors.py   # CPU/内存/磁盘/网络/GPU/uptime 采集（单项失败只降级）
│   ├── snapshot.py     # 拼契约 JSON（紧凑 separators、取整规则）
│   ├── sampler.py      # 采集线程 + queue(maxsize=2, 丢旧帧) + latest()
│   ├── usb_link.py     # 串口发现、1–2 Hz 推帧、指数退避重连、hello→cfg 下发
│   ├── http_link.py    # ThreadingHTTPServer，/api/stats（Bearer）、/health
│   ├── config.py       # ~/.tdisplay-pcmon/config.json（首次生成随机 Token）
│   ├── gpu.py          # nvidia-smi 调用与 CSV 解析
│   ├── rates.py        # 累计计数器 → MiB/s 差分
│   ├── netinfo.py      # 本机局域网地址
│   ├── tray.py         # 可选：系统托盘（pystray/Pillow，状态圆点 + 右键菜单）
│   ├── dsh.py          # DSH 采集：会话日志缓存 + 进程/端口统计 + 5 s 刷新线程
│   ├── dsh_parse.py    # DSH 日志解析：zstd JSONL → 按天 token/费用（纯函数）
│   ├── runtime.py      # 采集+双链路+DSH+托盘 的统一编排（CLI 与 GUI 共用）
│   ├── gui.py          # tkinter 设置/状态窗口（GUI 只是壳，复用 runtime）
│   ├── gui_util.py     # GUI 纯函数（格式化/校验/文案，可单测）
│   ├── autostart.py    # 开机自启（打包版写 HKCU Run；源码版用启动文件夹脚本）
│   └── main.py         # argparse / 日志 / CLI / --gui / frozen 判定
├── packaging/          # PyInstaller 打包（spec、入口、图标生成、build_exe.ps1）
├── assets/pcmond.ico   # 程序化生成的图标（build 时自动生成，1024/256/… 多尺寸）
├── dist/pcmond.exe     # 打包产物（gitignore，构建后出现）
├── tests/              # pytest（差分、Token、JSON 契约、GPU、预热、队列、托盘、DSH、GUI 纯函数）
├── requirements.txt    # psutil / pyserial / zstandard
├── requirements-tray.txt  # 可选托盘依赖（默认不装）
├── setup.ps1           # 创建 .venv 并装依赖（-WithTray 加托盘）
├── run.ps1             # pythonw 无窗口后台启动
├── install_startup.ps1 # 当前用户登录自启（启动文件夹，不需要管理员）
├── uninstall_startup.ps1
└── README.md
```

## 安装

要求：Windows + Python 3.10 以上（本机验证用 `C:\Users\Lenovo\miniconda3\python.exe` 3.12.13）。

```powershell
cd D:\AI_vibe\tdisplay-s3-pc-monitor\companion
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

`setup.ps1` 会用本机 conda Python 建 `.venv` 并安装 `psutil`、`pyserial`、`zstandard`（默认源失败自动换清华源）。
需要系统托盘时加 `-WithTray`（额外装 `pystray`、`Pillow`）：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -WithTray
```

也可以手动装：

```powershell
C:\Users\Lenovo\miniconda3\python.exe -m pip install -r requirements.txt
C:\Users\Lenovo\miniconda3\python.exe -m pip install -r requirements-tray.txt   # 可选托盘
```

## 运行

```powershell
# 0) 图形界面（设置 + 状态；开发态用这个）
.\.venv\Scripts\python.exe -m pcmond --gui

# 1) 验证采集：打印一帧 JSON 后退出（不连串口、不开端口）
.\.venv\Scripts\python.exe -m pcmond --once

# 2) 前台调试（日志打到控制台）
.\.venv\Scripts\python.exe -m pcmond --verbose

# 3) 后台无窗口运行（自启也用这个入口）
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

常用参数（只影响本次进程，不写回配置文件）：

| 参数 | 说明 |
|---|---|
| `--gui` | 启动图形设置/状态窗口（源码模式默认仍是 CLI；打包版无参数时默认 GUI） |
| `--once` | 采集一帧打印到 stdout 后退出 |
| `--verbose` | DEBUG 日志 |
| `--port 8787` | 覆盖 HTTP 端口 |
| `--token xxx` | 覆盖 Bearer Token |
| `--serial COM7` | 指定串口（默认按 VID 0x303A / 描述自动发现） |
| `--no-usb` / `--no-http` | 关闭某条链路 |
| `--no-tray` | 关闭系统托盘（即使装了 pystray） |
| `--no-dsh` | 关闭 DSH 数据源（帧里不带 `dsh` 字段） |
| `--interval 1.0` | 采集周期（秒，≥0.2） |

日志写到 `%USERPROFILE%\.tdisplay-pcmon\pcmond.log`（1 MB × 3 轮转，UTF-8）。

## 打包版（pcmond.exe）

给**不想装 Python 的人**用的桌面版：单文件 exe + tkinter 设置/状态窗口，双击即可。

```powershell
# 构建（需要一台装有 Python + .venv 的机器，只有构建需要）
cd D:\AI_vibe\tdisplay-s3-pc-monitor
powershell -ExecutionPolicy Bypass -File companion\packaging\build_exe.ps1

# 产物
companion\dist\pcmond.exe
```

- `build_exe.ps1` 会：装构建依赖 PyInstaller → 用 PIL **程序化生成** `assets\pcmond.ico`
  （深色底 + 青色屏幕/CPU 柱，多尺寸，不引用外部图片）→ `PyInstaller --onefile --windowed --icon` 打包。
  加 `-SkipInstall` 可跳过 PyInstaller 安装步骤（已装过时更快）。
- **体积约 20–30 MB**（单文件含 Python 运行时 + psutil/pyserial/zstandard/Pillow/pystray/tkinter）。
- 用法：
  - **双击 `pcmond.exe`** → 弹出设置/状态窗口，后台同时跑采集 + USB/HTTP + 托盘；
  - `pcmond.exe --once` / `--no-usb` / `--no-http` / `--no-dsh` / `--verbose` 等命令行用法照旧；
  - 首次运行同样在 `%USERPROFILE%\.tdisplay-pcmon\config.json` 生成配置与 Token；
  - 窗口里可改 Wi-Fi/端口/间隔/Token/API URL/串口/DSH，点「保存并应用」只重启链路、不重启进程；
  - 底部「开机自启」在打包版写 `HKCU\...\Run`（不需要管理员），源码版复用启动文件夹脚本。
- 关窗口 = 优雅停（停托盘/USB/HTTP/采集/DSH），和托盘右键「退出」是同一条路径；**不要用任务管理器强杀**
  （强杀可能把 ESP32 的 USB CDC 搞挂，需要拔插恢复）。
- 打包版与脚本版共用同一份配置/日志/数据目录，可以混用；不想装环境的人只拷 `pcmond.exe` 即可，
  卸载 = 删 exe（配置在 `%USERPROFILE%\.tdisplay-pcmon`，自启项可在窗口里取消）。
- Windows 首次运行可能弹 SmartScreen「已保护你的电脑」（未签名 exe 的正常提示），
  点「更多信息 → 仍要运行」即可；公司电脑策略禁用未签名 exe 时无解，请用脚本版。

## 配置文件

首次运行自动生成 `%USERPROFILE%\.tdisplay-pcmon\config.json`（含随机 24 字符 Token）：

```json
{
  "token": "首次生成，设备端 api_token 必须一致",
  "port": 8787,
  "host": "0.0.0.0",
  "serial": null,
  "usb_enabled": true,
  "http_enabled": true,
  "tray_enabled": true,
  "interval_s": 1.0,
  "usb_hz": 1.0,
  "dsh_enabled": true,
  "dsh_port": 0,
  "dsh_pricing": {
    "deepseek-flash": {"cache_hit": 0.04, "cache_miss": 2.0, "output": 8.0}
  },
  "api_url": null,
  "wifi_ssid": null,
  "wifi_pass": null
}
```

- `serial: null` = 自动发现；也可以写死 `"COM7"`。
- `usb_hz` 契约要求 1–2 Hz，超出会被夹到边界。
- `dsh_enabled` / `dsh_port` / `dsh_pricing`：DSH 数据源开关、GUI 端口、费用单价
  （元/百万 tokens，**高峰期**价格；空闲自动半价）。价格表按模型覆盖，未覆盖的键沿用默认，
  未知模型回退到 `deepseek-flash`；改价格表会自动作废旧解析缓存。
- `dsh_port=0`（默认）= **自动探测** DSH Web GUI 端口：DSH 的 GUI 端口每个会话都可能变
  （实测上午 10899、下午 8679），候选 `8679 / 10899` 逐个探测；非 0 = 只用指定端口，不再探测。
  GUI 的「DSH 端口」默认显示「自动」（留空/`自动`/`0` 同义）；状态区会显示 `端口 自动→8679`
  或 `端口 自动·未探测到`。
- `wifi_ssid` / `wifi_pass` 是可选项：填了之后，设备通过 USB 发 `{"t":"hello","need_cfg":1}` 时，
  pcmond 会回一帧 `{"t":"cfg","wifi_ssid":...,"wifi_pass":...,"api_token":...,"api_url":...}`
  让设备写 NVS 并重启（架构 §4 的推荐配置路径）。`api_url` 自动取本机局域网 IP。
- `api_url`：可选，手动覆盖下发给设备的 URL；`null` 时自动用 `http://<本机局域网IP>:<port>/api/stats`
  （GUI 设置区可改，改后点「保存并应用」生效）。
- **删掉 config.json 会重新生成 Token**，设备 NVS 里的 `api_token` 也要跟着改。

## HTTP 接口

设备端 URL：`http://<本机局域网IP>:8787/api/stats`（启动日志里会打印所有候选地址）。

```powershell
$token = (Get-Content "$env:USERPROFILE\.tdisplay-pcmon\config.json" | ConvertFrom-Json).token
curl.exe -H "Authorization: Bearer $token" http://127.0.0.1:8787/api/stats
```

- `GET /api/stats`：需要 `Authorization: Bearer <token>`；返回最新一帧，与 USB 推送**字节相同**。
  无 Token / 错 Token → `401 {"error":"unauthorized"}`；还没采到第一帧 → `503`。
- `GET /health`：免鉴权，`{"status":"ok","version":"0.1.0","seq":N}`，供设备/人探活。
- 其他路径 → `404`。

## USB 帧与握手

- 一行一帧、`\n` 结尾、UTF-8 无 BOM，字段见契约 §2；PC 端**不发** `src`（由设备填 `usb`/`wifi`）。
- 默认 1 Hz 推最新帧（`usb_hz` 可到 2 Hz），串口 115200。
- 端口发现顺序：`--serial` 指定 > **`VID 0x303A + PID 0x1001`**（S3 内置 USB-Serial/JTAG，本机板子实测）
  > 任意 `VID 0x303A`（如 TinyUSB CDC `303A:0002`）> 描述含
  `USB 串行设备` / `USB JTAG` / `USB-SERIAL` / `ESP32`。先认 PID 是为了同机插多块 Espressif 设备时不误抓。
- 断开后指数退避重连（0.5 → 1 → 2 … 最大 10 s，连上后重置），不影响采集与 HTTP。
- 收到的设备行若是 JSON 且 `t == "hello"`、`need_cfg` 非 0，则按上节回 cfg 帧；其他串口输出只记 DEBUG 日志。

## DSH 页数据来源与口径（可选）

`dsh` 对象字段见契约 §2.5；设备页给费用标 `≈` 表示是**估算、不是账单**。

### token 用量
- 扫 `%USERPROFILE%\.dsh\sessions\**\session*.jsonl.zstd`（zstd 压缩的 JSONL），
  取 `type == "assistant/message"` 记录的 `data.usage`：
  `inputTokens`（未命中输入）、`outputTokens`、`cacheReadTokens`（缓存命中输入）、
  `cacheWriteTokens`（按未命中价计费）、`totalTokens`（= in + out + cacheRead + cacheWrite）。
- `today` = 本机时区当天 00:00 起；`week` = 滚动 7 天（含今天）；
  `req` = assistant/message 条数（interrupted 且没有 usage 的消息也计 1 次、token 记 0）。
- 只统计**本机** DSH 会话日志，不含网页版/其它客户端；日志落盘前不计入（界面延迟约 1–2 s）。

### 刷新与缓存
- 独立 daemon 线程每 **5 s** 扫一次，1 Hz 发帧只读 `latest()` 缓存，**不会每帧解压日志**。
- 解析结果缓存到 `%USERPROFILE%\.tdisplay-pcmon\dsh_cache.json`，按 `(path, mtime, size)` 增量：
  只重解析有改动/新增的文件；改 `dsh_pricing` 会自动作废旧缓存。
- 只扫 mtime 在近 7 天窗口内的文件（更早的文件不可能含窗口内消息）。
- 单个文件损坏/格式变化 → 跳过该文件并记 warning，其余照常；**近 7 天没有任何可用 token 记录时
  帧里不带 `dsh` 字段**（设备显示 `--`），不塞一堆 0。

### 费用估算口径（估算，非账单）
- **逐条**按该条 `time` 判断峰谷 + 该条模型单价累加，不用"均价 × 总量"；
- 高峰 = 北京时间**周一至周五 9:00–12:00、14:00–18:00**（左闭右开），其余时段空闲，空闲价 = 高峰价一半；
- `deepseek-flash` 高峰价：缓存命中 **0.04**、未命中 **2**、输出 **8** 元/百万 tokens；
  `cacheWrite` 按未命中价；未知模型回退 `deepseek-flash` 价；
- **简化：只按星期 + 时段判断，不查中国法定节假日表**；官方调价后改 `config.json` 的 `dsh_pricing`；
- 官方价格页：<https://api-docs.deepseek.com/zh-cn/quick_start/pricing/>。

### 峰谷徽标（rate，v1.2）
`rate` 给设备页画「箭头 + 文字 + 倍数 + 切换倒计时」：
- `peak`：当前是否高峰——**直接来自 `is_peak()`**，不另写一套判断；
- `mult`：当前单价倍数，高峰 2 / 空闲 1；
- `until_min`：距下一次峰↔谷切换的分钟数，**向上取整**（如 11:59:30 → 1），0 表示马上切。

边界规则（北京时间，与费用口径同一套规则；不查法定节假日表）：

| 当前时段 | 下一个切换 |
|---|---|
| 工作日 09:00–12:00（高峰） | 12:00 |
| 工作日 14:00–18:00（高峰） | 18:00 |
| 工作日 00:00–09:00（空闲） | 09:00 |
| 工作日 12:00–14:00（空闲） | 14:00 |
| 工作日 18:00 之后（空闲） | 次日 09:00（周五 → 下周一 09:00） |
| 周六 / 周日（空闲） | 下周一 09:00 |

`rate` 缺失时设备**不显示**峰谷徽标、也不自己推算。跨月/跨年由 `datetime` 处理。

### 进程
- `proc.count` = 进程名含 `DeepSeek Harness` 的进程数（psutil 枚举）；
- `proc.rss_mb` = 这些进程 RSS 合计（MiB 取整）；
- `proc.cpu_pct` = 各进程 `cpu_percent(interval=None)` 之和（psutil 首调恒为 0，5 s 后准确）；
- `proc.port` = 实际探测到的 DSH Web GUI 端口（**0 = 未探测到**；设备可以不显示，仅调试/PC 端自证）；
- `proc.port_up` = 该端口是否在监听。探测是「TCP connect → 一次极简 HTTP 请求确认返回 `HTTP/`」，
  避免把别的程序误判成 DSH（401/403 也算 Web 服务）；结果会缓存，端口变了/服务消失自动重探，
  全部失败时隔 20 s 再扫，不会每 5 s 都扫。

关闭：`--no-dsh` 启动或配置 `dsh_enabled: false`。带完整 `dsh`（含 `rate`）时整帧约 **780–800 B**，
在契约 v1.1 的 **1 KB 上限 / 1024 B 缓冲**内（1 Hz 下带宽无压力）。

## 开机自启

```powershell
powershell -ExecutionPolicy Bypass -File .\install_startup.ps1    # 当前用户登录自启
powershell -ExecutionPolicy Bypass -File .\uninstall_startup.ps1  # 取消（并停掉运行中的 pcmond）
```

在当前用户的「启动」文件夹创建 `pcmond.lnk`，指向 `.venv\Scripts\pythonw.exe -m pcmond`，
工作目录设为 `companion\`。**不需要管理员权限、不写注册表。**
托盘模式也是走这个 pythonw 入口（无控制台窗口，托盘图标就是可见的入口）。

## 托盘（可选）

托盘依赖 `pystray` + `Pillow`，**默认不装**。没装时 pcmond 照常运行（采集/HTTP/USB 都不受影响），
只在日志里记一条 `未安装 pystray/Pillow，系统托盘不可用` 的 warning。

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -WithTray   # 装托盘依赖
.\.venv\Scripts\pythonw.exe -m pcmond                            # run.ps1 / 自启用的就是这种无窗口模式
```

启动后通知区域出现圆点图标，颜色含义：

| 颜色 | 含义 |
|---|---|
| 绿 | 串口已打开，USB 链路在推帧 |
| 黄 | 只有 HTTP 在跑（没发现串口 / 板子没插） |
| 红 | 两个链路都不可用 |

鼠标悬停 tooltip 显示：版本、当前状态、HTTP 地址:端口、USB 端口名、最近一帧 `seq @ 时间`、
采集间隔，以及 `Token ***`（**明文 Token 在任何界面都不显示**）。

右键菜单：

| 菜单项 | 作用 |
|---|---|
| `状态：…` | 弹消息框显示同样信息（含完整 `/api/stats` URL，Token 打码） |
| `打开配置目录` | 资源管理器打开 `%USERPROFILE%\.tdisplay-pcmon` |
| `打开日志` | 记事本打开 `pcmond.log`（文件不存在就打开所在目录） |
| `退出` | 优雅停：停托盘 → 停采集线程 → 停 USB/HTTP → 进程退出 |

不用托盘：`--no-tray` 启动、配置 `tray_enabled: false`，或者不装 `requirements-tray.txt` 都行。

真机验收（2026-11-10，板子插着 COM10）：绿色 + tooltip（含 `Token ***`）+ 状态弹窗 + 打开配置目录 +
打开日志 + 菜单退出全部实测通过；黄色用 `--no-usb` 实测（日志 `仅 HTTP 在线（未发现串口）（yellow）`）。
**拔插 USB 的热切换**需要人工拔一次线：代码路径是「端口消失 → 清空 port_name → 退避重连 → 托盘变黄/回绿」，
目前只验证了绿/黄两个状态，未做手动拔插。

> **实现坑（已处理）**：pystray 传自定义 `setup` 回调时**不会**自动显示图标，必须在 setup 里
> `icon.visible = True`，否则日志一切正常但托盘图标永远不出现（真机验收抓到的坑，`tray.py` 已修，
> 单测 `test_setup_makes_icon_visible` 锁定）。另外 Windows 11 默认把新托盘图标收进
> 「显示隐藏的图标」浮层，想常显可以把它拖到任务栏上。

## 排障

| 现象 | 原因 / 处理 |
|---|---|
| `--once` 里 `net`/`disk` 是 `0.00` | 单帧刷新时速率窗口只有预热那 0.2 s，静默机器上确实接近 0；跑起来后按 1 s 窗口差分就是真实值。 |
| `gpu` 是 `null` | `nvidia-smi` 不在 PATH、驱动没装或超时（3 s）。手动执行 `nvidia-smi --query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw --format=csv,noheader,nounits` 验证；读不到就发 null，不会崩。 |
| 第二个实例启动报 `WinError 10048` / HTTP 未启动 | 8787 已被占用（可能已有一个 pcmond 在跑）。`netstat -ano \| findstr :8787` 查 PID；或 `--port` 换端口。Windows 上 pcmond 用 `SO_EXCLUSIVEADDRUSE` 独占端口，不会两个实例静默抢同一端口。 |
| 设备（Wi-Fi）连不上 HTTP | ① 设备 `api_url` 里的 IP 要和启动日志打印的局域网地址一致；② Windows 防火墙首次可能拦截入站 8787——放行需要**管理员**执行一次：`New-NetFirewallRule -DisplayName "pcmond 8787" -Direction Inbound -Protocol TCP -LocalPort 8787 -Profile Private -Action Allow`；③ 手机热点/路由器开了 AP 隔离。 |
| 烧录固件时 esptool 报串口占用 | Windows 串口是**独占访问**，pcmond 常驻时开着 COM 口，esptool 就烧不进去。先在托盘右键「退出」（或 `taskkill` 掉进程），或临时用 `.\.venv\Scripts\python.exe -m pcmond --no-usb`；烧完再启动。 |
| 串口找不到 / 一直重连 | 板子没插、只有供电线没有数据线、或串口驱动未就绪。设备管理器应出现「USB 串行设备（COMx）」，`Get-PnpDevice -PresentOnly \| Select-String 303A` 可看 VID；也可用 `python -c "from serial.tools import list_ports; print(list_ports.comports())"`。 |
| 串口被占用（`PermissionError`/`拒绝访问`） | PlatformIO 串口监视器、Arduino IDE、串口助手、esptool 同时开了同一个 COM 口。关掉它们后 pcmond 会自动重连（最多 10 s 退避）。 |
| `/api/stats` 一直 401 | `config.json` 的 `token` 与设备 NVS 的 `api_token` 不一致；改 config 后重启 pcmond，或让设备重新触发 hello 下发配置。 |
| CPU 占用率总是 0 | 已处理：启动时先 `cpu_percent(interval=None)` 预热一次（首调必返回 0.0），单测覆盖。若自己改代码，别删预热。 |
| 托盘没有图标 | ① 没装 pystray/Pillow（日志会有一条 warning；`setup.ps1 -WithTray` 重装）；② 用了 `--no-tray` 或 `tray_enabled: false`；③ pythonw 无窗口运行时看不到控制台是正常的，托盘图标就是可见入口。 |
| 托盘退出后怎么确认进程没了 | 菜单「退出」会先停采集/链路再退出；也可 `Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" \| Where-Object { $_.CommandLine -like "*-m pcmond*" }` 查，或 `taskkill /IM pythonw.exe /F`（会留下未优雅关闭的日志）。 |
| 想看更细日志 | `--verbose` 启动，或直接看 `%USERPROFILE%\.tdisplay-pcmon\pcmond.log`。 |
| 帧里没有 `dsh` 字段 | 近 7 天没有可解析的 DSH 会话日志（`%USERPROFILE%\.dsh\sessions` 为空或全坏）、用了 `--no-dsh` / `dsh_enabled:false`，或没装 `zstandard`（日志里有 warning）。 |
| `dsh.today.tok` 比 DSH 界面小 | 只统计本机会话日志，且有约 1–2 s 落盘延迟；正在生成的会话会略滞后。费用也只是估算，不是账单。 |

## 测试

在仓库根目录执行（无需安装成包，`companion/conftest.py` 会加好路径）：

```powershell
C:\Users\Lenovo\miniconda3\python.exe -m pytest companion -q
```

覆盖：差分速率（含首帧/回绕）、Bearer Token 校验与 401、JSON 契约字段与取整、`nvidia-smi`
解析（含 `N/A`、虚拟适配器、命令不存在、超时、非零退出）、`cpu_percent` 预热顺序、
`queue.Queue(maxsize=2)` 丢旧帧、端口冲突降级、串口异常重连、hello→cfg 字段；
托盘部分：状态→颜色/文案映射、Token 打码（`***`）、pystray 缺失时 `start()` 返回 False 不抛、
菜单四项、退出回调、explorer/notepad 启动参数；DSH 部分：today/week 边界、峰谷价格、
价格覆盖与未知模型回退、缓存增量命中与价格变更重建、坏文件跳过、字段缺失容错、
进程/端口统计、线程异常不退出、`dsh` 可选字段；GUI 部分只测纯函数（token 打码/单位格式化、
峰谷徽标文案与颜色、链路/设备/DSH 文案、设置校验、串口选项、自启命令、Tk 模块可导入），
**测试里不创建 Tk 窗口**。未装 Pillow 时仅真实绘图那条会 skip。

> `.venv` 只装**运行**依赖（psutil/pyserial）。要在 venv 里跑测试，先
> `.\.venv\Scripts\python.exe -m pip install pytest`；或者直接用上面的基础 python 命令。

## 未验证项

- USB CDC 真机推帧 / 断线重连 / hello 握手：2026-11-10 在 COM10 上验证过 1 Hz 推帧连续、
  设备 hello 每 5 s 正确读取；**运行中拔线→黄→插回→绿的热切换仍待人工拔一次线**。
- 1 Hz、约 490 B（不带 dsh）/ 约 800 B（带 dsh）每帧下的长时间稳定性、USB 丢帧率（需更长时间压测）。
- Windows 防火墙实际放行后设备端能否稳定轮询（本机未在局域网设备上验证）。
- **托盘绿色状态**：2026-11-10 真机验证（COM10 插着时绿色；`--no-usb` 时黄色）；热插拔切换同上待人工。
- **DSH 数字与 DSH 自身统计/官方账单的对账**：today/week token 与独立解压脚本逐条核对**完全一致**
  （2026-11-10），但没和 DSH UI 显示值、官方账单逐项对账；峰谷也简化了节假日。
- **打包版在真正没装 Python 的机器上未实测**：本机把 `pcmond.exe` 拷到无 venv 的临时目录、并用
  最小 PATH（只留 `C:\Windows\System32;C:\Windows`）跑过 `--once` 和 GUI（HTTP 200 / 优雅退出），
  证明依赖已打进包，但没有第二台干净机器可用；首次 SmartScreen 提示也未实测。
- **GUI 自启勾选**：打包版写 HKCU Run 的逻辑有单测/代码审查，但没做「重启登录后自动拉起」的真实验证；
  源码态勾选走已有的启动文件夹脚本（该脚本在 2026-11-10 验证过）。
