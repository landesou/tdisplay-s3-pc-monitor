# pcmond — T-Display-S3 PC 上位机

采集本机 CPU / 内存 / GPU / 网络 / 磁盘指标，按统一契约（[`docs/design/architecture.md`](../docs/design/architecture.md) §2）
打成**一行紧凑 JSON**，同时通过 **USB CDC 串口** 和 **HTTP** 推给 T-Display-S3。

- 采集与传输解耦：采集线程固定周期（默认 1.0 s）产出一帧放入 `queue.Queue(maxsize=2)`，
  队列满丢最旧帧，**采集永不阻塞**；USB 与 HTTP 各自读最新快照。
- HTTP 的 `/api/stats` 直接返回最近一帧，**不为每个请求重新采集**。
- 所有异常都降级：串口拔掉自动重连；`nvidia-smi` 不存在/超时（3 s）时 GPU 字段发 `null`；
  HTTP 端口被占只关闭 HTTP，采集和 USB 继续。
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
│   └── main.py         # argparse / 日志 / 进程编排
├── tests/              # pytest（差分、Token、JSON 契约、GPU 解析、预热、队列）
├── requirements.txt
├── setup.ps1           # 创建 .venv 并装依赖
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

`setup.ps1` 会用本机 conda Python 建 `.venv` 并安装 `psutil`、`pyserial`（默认源失败自动换清华源）。
也可以手动装：

```powershell
C:\Users\Lenovo\miniconda3\python.exe -m pip install -r requirements.txt
```

## 运行

```powershell
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
| `--once` | 采集一帧打印到 stdout 后退出 |
| `--verbose` | DEBUG 日志 |
| `--port 8787` | 覆盖 HTTP 端口 |
| `--token xxx` | 覆盖 Bearer Token |
| `--serial COM7` | 指定串口（默认按 VID 0x303A / 描述自动发现） |
| `--no-usb` / `--no-http` | 关闭某条链路 |
| `--interval 1.0` | 采集周期（秒，≥0.2） |

日志写到 `%USERPROFILE%\.tdisplay-pcmon\pcmond.log`（1 MB × 3 轮转，UTF-8）。

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
  "interval_s": 1.0,
  "usb_hz": 1.0,
  "wifi_ssid": null,
  "wifi_pass": null
}
```

- `serial: null` = 自动发现；也可以写死 `"COM7"`。
- `usb_hz` 契约要求 1–2 Hz，超出会被夹到边界。
- `wifi_ssid` / `wifi_pass` 是可选项：填了之后，设备通过 USB 发 `{"t":"hello","need_cfg":1}` 时，
  pcmond 会回一帧 `{"t":"cfg","wifi_ssid":...,"wifi_pass":...,"api_token":...,"api_url":...}`
  让设备写 NVS 并重启（架构 §4 的推荐配置路径）。`api_url` 自动取本机局域网 IP。
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
- 端口发现顺序：`--serial` 指定 > `VID 0x303A`（Espressif USB-Serial/JTAG）>
  描述含 `USB 串行设备` / `USB JTAG` / `USB-SERIAL` / `ESP32`。
- 断开后指数退避重连（0.5 → 1 → 2 … 最大 10 s，连上后重置），不影响采集与 HTTP。
- 收到的设备行若是 JSON 且 `t == "hello"`、`need_cfg` 非 0，则按上节回 cfg 帧；其他串口输出只记 DEBUG 日志。

## 开机自启

```powershell
powershell -ExecutionPolicy Bypass -File .\install_startup.ps1    # 当前用户登录自启
powershell -ExecutionPolicy Bypass -File .\uninstall_startup.ps1  # 取消（并停掉运行中的 pcmond）
```

在当前用户的「启动」文件夹创建 `pcmond.lnk`，指向 `.venv\Scripts\pythonw.exe -m pcmond`，
工作目录设为 `companion\`。**不需要管理员权限、不写注册表。**

## 排障

| 现象 | 原因 / 处理 |
|---|---|
| `--once` 里 `net`/`disk` 是 `0.00` | 单帧刷新时速率窗口只有预热那 0.2 s，静默机器上确实接近 0；跑起来后按 1 s 窗口差分就是真实值。 |
| `gpu` 是 `null` | `nvidia-smi` 不在 PATH、驱动没装或超时（3 s）。手动执行 `nvidia-smi --query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw --format=csv,noheader,nounits` 验证；读不到就发 null，不会崩。 |
| 第二个实例启动报 `WinError 10048` / HTTP 未启动 | 8787 已被占用（可能已有一个 pcmond 在跑）。`netstat -ano \| findstr :8787` 查 PID；或 `--port` 换端口。Windows 上 pcmond 用 `SO_EXCLUSIVEADDRUSE` 独占端口，不会两个实例静默抢同一端口。 |
| 设备（Wi-Fi）连不上 HTTP | ① 设备 `api_url` 里的 IP 要和启动日志打印的局域网地址一致；② Windows 防火墙首次可能拦截入站 8787——放行需要**管理员**执行一次：`New-NetFirewallRule -DisplayName "pcmond 8787" -Direction Inbound -Protocol TCP -LocalPort 8787 -Profile Private -Action Allow`；③ 手机热点/路由器开了 AP 隔离。 |
| 串口找不到 / 一直重连 | 板子没插、只有供电线没有数据线、或串口驱动未就绪。设备管理器应出现「USB 串行设备（COMx）」，`Get-PnpDevice -PresentOnly \| Select-String 303A` 可看 VID；也可用 `python -c "from serial.tools import list_ports; print(list_ports.comports())"`。 |
| 串口被占用（`PermissionError`/`拒绝访问`） | PlatformIO 串口监视器、Arduino IDE、串口助手、esptool 同时开了同一个 COM 口。关掉它们后 pcmond 会自动重连（最多 10 s 退避）。 |
| `/api/stats` 一直 401 | `config.json` 的 `token` 与设备 NVS 的 `api_token` 不一致；改 config 后重启 pcmond，或让设备重新触发 hello 下发配置。 |
| CPU 占用率总是 0 | 已处理：启动时先 `cpu_percent(interval=None)` 预热一次（首调必返回 0.0），单测覆盖。若自己改代码，别删预热。 |
| 想看更细日志 | `--verbose` 启动，或直接看 `%USERPROFILE%\.tdisplay-pcmon\pcmond.log`。 |

## 测试

在仓库根目录执行（无需安装成包，`companion/conftest.py` 会加好路径）：

```powershell
C:\Users\Lenovo\miniconda3\python.exe -m pytest companion -q
```

覆盖：差分速率（含首帧/回绕）、Bearer Token 校验与 401、JSON 契约字段与取整、`nvidia-smi`
解析（含 `N/A`、虚拟适配器、命令不存在、超时、非零退出）、`cpu_percent` 预热顺序、
`queue.Queue(maxsize=2)` 丢旧帧、端口冲突降级、串口异常重连、hello→cfg 字段。

> `.venv` 只装**运行**依赖（psutil/pyserial）。要在 venv 里跑测试，先
> `.\.venv\Scripts\python.exe -m pip install pytest`；或者直接用上面的基础 python 命令。

## 未验证项（等真机）

- 板子现在没插在电脑上（无 `VID_303A` 设备），**USB CDC 真机推帧/断线重连/hello 握手未做硬件联调**，
  目前只有单测 + 假串口验证。
- 1 Hz、约 400 B/帧下的长时间稳定性、USB 丢帧率（需实机压测）。
- Windows 防火墙实际放行后设备端能否稳定轮询（本机未在局域网设备上验证）。
