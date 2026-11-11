# 架构与接口契约 v1

> 本文件是**固件与上位机之间的合同**。任何一方改字段，先改这里，再改代码。
> 创建于 2026-11-10，作者：Lead。

## 1. 数据流

```text
┌────────────────────── PC (Windows, 上位机 pcmond) ──────────────────────┐
│ 采集器 collectors/                                                       │
│   cpu  : psutil (占用率/频率)                                            │
│   mem  : psutil                                                          │
│   disk : psutil (读写速率，差分)                                          │
│   net  : psutil (上下行速率，差分)                                        │
│   gpu  : nvidia-smi 子进程 (占用/温度/显存/功耗)                          │
│        │                                                                 │
│        ▼  StatsSnapshot → 紧凑 JSON（一行，UTF-8，≤ 1 KB）              │
│  ┌───────────────┐            ┌──────────────────────┐                  │
│  │ USB CDC 推送   │            │ HTTP 服务 :8787       │                  │
│  │ 1–2 Hz 一行   │            │ GET /api/stats (+Token)│                 │
│  └───────┬───────┘            └──────────┬───────────┘                  │
└──────────┼────────────────────────────────┼─────────────────────────────┘
           │ USB-C 线(供电+数据)             │ Wi-Fi LAN / HTTP 轮询(1–2 Hz)
           ▼                                ▼
┌──────────────────── T-Display-S3 (ESP32-S3, ESP-IDF 5.5.3 + LVGL 9) ─────┐
│ link_usb (CDC 行读取) / link_http (esp_http_client) → 同一解析器 cJSON    │
│        │                                                                 │
│        ▼  StatsSnapshot + age(距上一帧的秒数)                            │
│  仲裁：USB 有帧 → USB 优先；否则用 Wi-Fi；两者都没有 → NO_LINK/STALE     │
│        │                                                                 │
│        ▼                                                                 │
│  ui.c (LVGL 9)：横屏 320×170，2 页，60 点 history 环形缓冲               │
└──────────────────────────────────────────────────────────────────────────┘
```

## 2. 帧格式

**一行一帧**，`\n` 结尾，UTF-8，无 BOM，字段全部可选（缺失按 null 处理并显示 `--`）。
两条传输链路共用同一个 JSON，报文语义完全一致。

```json
{"v":1,"seq":42,"t":1762761600,"host":"LENOVO-83NN","up":123456,
 "cpu":{"avg":23.4,"mhz":2400,"temp":null,"cores":[12,34,5,0]},
 "mem":{"pct":61.2,"used":9340,"total":15288},
 "gpu":{"name":"RTX 5060","util":16,"temp":58,"vram_used":1864,"vram_total":8151,"power":29.2,"mhz":null},
 "net":{"down":1.2,"up":0.3},
 "disk":{"read":0.4,"write":2.1},
 "src":"usb"}
```

字段表（单位写死，不要再转换）：

| 字段 | 类型 | 单位 | 说明 |
|---|---|---|---|
| `v` | int | – | 协议版本，当前 1 |
| `seq` | int | – | 单调递增帧号，用于判断掉帧 |
| `t` | int | unix 秒 | PC 本地时间 |
| `host` | string | – | 主机名，≤ 15 字符；设备只取前 15 |
| `up` | int | 秒 | 开机时长 |
| `cpu.avg` | float | % | 0–100，全核平均 |
| `cpu.mhz` | int | MHz | 当前平均频率，读不到则 null |
| `cpu.temp` | float/null | °C | v1 笔记本基本为 null（见 journal 未验证项） |
| `cpu.cores` | int[] | % | 每线程占用率；**设备端最多渲染 32 个** |
| `mem.pct` | float | % | |
| `mem.used/total` | int | MiB | |
| `gpu.name` | string | – | 设备只取前 16 字符 |
| `gpu.util` | int | % | |
| `gpu.temp` | int | °C | |
| `gpu.vram_used/total` | int | MiB | |
| `gpu.power` | float | W | |
| `net.down/up` | float | MiB/s | |
| `disk.read/write` | float | MiB/s | |
| `src` | string | – | 由设备填写：`usb` / `wifi`（PC 端不要发这个字段） |
| `dsh` | object | – | **可选**（v1.1 新增）。DeepSeek Harness 的运行状态与 token 消耗，见 §2.5；缺省时设备显示 `--` |

**帧长度上限与缓冲余量**（v1.1 更新）：

- 契约上限从 **600 B 放宽到 1 KB**：带完整 `dsh` 段的一帧实测 **625 B**（24 核 + 全部 dsh 字段）；
- 两端缓冲都是 **1024 B**：设备 USB 行缓冲、HTTP body 缓冲。**超过会被整行丢弃/截断解析失败**，
  所以设备侧对超长行要记一条 warning（方便定位"帧又长大了"），不要静默吞掉；
- 以后若核数或字段继续增长，优先压缩字段（`cores` 可改 delta/定点）而不是加大缓冲——1024 B 是刻意留的边界。

### 2.5 `dsh` 对象（v1.1，可选字段）

数据源：上位机扫描 `~/.dsh/sessions/**/session*.jsonl.zstd` 里的
`assistant/message.data.usage`（与 DSH 自带 token 统计同源），外加 psutil 读 DSH 进程。

```json
"dsh": {
  "today": {"tok": 1280000, "in": 620000, "out": 96000, "cache": 566000, "req": 366, "cost": 1.23},
  "week":  {"tok": 8800000, "req": 2100, "cache_pct": 87, "cost": 8.40},
  "proc":  {"count": 10, "rss_mb": 906, "cpu_pct": 12, "port_up": true}
}
```

| 字段 | 类型 | 单位 | 说明 |
|---|---|---|---|
| `today.tok` | int64 | tokens | 今日（本地时区 00:00 起）总 tokens |
| `today.in/out/cache` | int64 | tokens | 今日：未命中输入 / 输出 / 缓存命中输入 |
| `today.req` | int | 次 | 今日 LLM 请求数（= `assistant/message` 条数） |
| `today.cost` | float | 元 | 今日费用**估算**（见下方口径，界面必须标 `≈`） |
| `week.tok/req/cost` | – | – | 近 7 天（滚动，含今天） |
| `week.cache_pct` | int | % | 近 7 天缓存命中率 = cache / (in + cache) |
| `proc.count/rss_mb/cpu_pct` | int | – / MiB / % | DSH 进程数、合计常驻内存、合计 CPU |
| `proc.port_up` | bool | – | 本地 GUI 端口（默认 10899）是否在监听 |

**费用口径（必须遵守，界面标 `≈`）**：

- 逐条 `assistant/message` 按该条的 `time` 判断峰谷 + `source.model` 取价，不做"均价 × 总量"；
- 峰谷规则（DeepSeek 官方，2026-11 查）：**北京时间周一至周五 9:00–12:00、14:00–18:00 为高峰
  （法定节假日除外），其余时段为空闲；空闲价 = 高峰价的一半**；
- `deepseek-flash` 官方单价（元 / 百万 tokens，高峰）：缓存命中 0.04、未命中 2、输出 8；
- **只统计本机 DSH 会话日志**，不含网页版/其它客户端用量；日志未落盘前不计入（延迟 1–2 s）；
- 日志格式是 DSH 内部实现，解析失败一律降级为"没有 dsh 字段"，**不许让采集器崩**。

## 3. 设备侧状态机（新鲜度/仲裁）

阈值：**3 秒**没有新帧 → `STALE`（界面标灰 + 顶部状态灯变黄/红）。

| 状态 | 含义 |
|---|---|
| `INIT` | 上电，外设/UI 初始化中 |
| `NO_LINK` | 还没收到过任何一帧 |
| `ONLINE_USB` | USB 帧新鲜，USB 优先 |
| `ONLINE_WIFI` | Wi-Fi HTTP 帧新鲜（USB 无帧时） |
| `STALE` | 曾经有过数据，但超过 3 秒没有新帧 |

不变式（logicprobe 建模验证，2026-11-10：`verdict=pass_with_findings`，0 error / 21 warning，
modelHash `88fee4edf864`，见 `docs/design/link-state-machine.model.json`）：

- **`ONLINE_*` 时 `age ≤ 2`**：绝不在旧数据上显示「在线」（S7 不变式检查通过）。
- **数据停流后第 3 个 tick 必然离开 `ONLINE_*`**：`tick` 事件的两个守卫（`age<2` 自转 / `age>=2`
  转 STALE）完整覆盖，无未定义分支（S6 通过）。这里**故意不用 A14 的 `maxTicks`**：驻留计数会被
  `usb_frame` 重置 age 但不重置驻留，而「数据一直在流就一直在 ONLINE」是正确行为，用截止期反而会
  把正常行为判成违规（首轮验证就是这么挂的，见 journal）。
- **无死锁**：每个非终态都有出口（S2 通过）；运行态不是封闭 SCC，`SLEEP`（长按深睡）是唯一终态（S3 通过）。
- **进入 `ONLINE_USB` 必须先收到过 `usb_frame`**：不会拿 WiFi 数据冒充 USB（event-before-state 通过）。
- 剩余 21 条警告全部为设计使然：10 条「该状态下不可能发生的事件被忽略」（如非 INIT 状态的
  `boot_done`、INIT 期间到达的帧），1 条「帧与 tick 的到达顺序会影响结果」（物理事实）。

## 4. 设备侧配置（NVS）

| key | 说明 |
|---|---|
| `wifi_ssid` / `wifi_pass` | Wi-Fi 凭据 |
| `api_url` | 形如 `http://192.168.1.10:8787/api/stats` |
| `api_token` | Bearer Token（与上位机 config.json 一致） |
| `brightness` | 0–100 |
| `page` | 上次停留页 |

首次配置两种方式（都实现，成本很低）：
1. 串口命令行：`wifi <ssid> <pass>`、`url <url>`、`token <tok>`、`cfg`、`reboot`、`lcd [0-3]`、`help`。
2. **USB 下发**：设备启动后发一行 `{"t":"hello","need_cfg":1}`（`need_cfg` = 未配置 Wi-Fi 时为 1），
   上位机回一帧配置，设备写 NVS 并重启。**字段名以上位机实际实现为准**（2026-11-10 核对
   `companion/pcmond/usb_link.py`）：

```json
{"t":"cfg","wifi_ssid":"MyWiFi","wifi_pass":"***","api_url":"http://10.21.132.85:8787/api/stats","api_token":"<24字符>"}
```

> 注意：上面是**真实字段名**（`wifi_ssid` / `wifi_pass` / `api_url` / `api_token`），早期草稿里写成
> `ssid`/`pass`/`url`/`token` 是简写，不要照抄。`api_url` 由上位机的 `lan_ip()` 生成，不写 `127.0.0.1`。

## 5. 目录/写作用域（团队协作用）

| 路径 | 归属 |
|---|---|
| `firmware/main/display/`、`firmware/main/ui/` | 固件显示同学 |
| `firmware/main/link/`、`firmware/main/config/` | 固件链路同学 |
| `firmware/main/main.c`、`firmware/main/CMakeLists.txt`、`firmware/CMakeLists.txt`、`sdkconfig.defaults`、`partitions.csv` | Lead |
| `companion/` | 上位机同学 |
| `docs/journal.md` | Lead |
| `docs/README.draft.md`、`docs/blog-draft.md` | 写作同学 |
