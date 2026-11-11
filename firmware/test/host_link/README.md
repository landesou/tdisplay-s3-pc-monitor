# host_link —— 链路层主机离线测试

把固件里**真实的** `firmware/main/link/link.c`（不是复制品、不是重写）用主机 C 编译器编出来，
在 PC 上直接跑「新鲜度状态机 + JSON 解析器」的断言。**不需要 ESP32、不需要插板子。**

## 为什么要这个

链接层的两块核心逻辑——「3 秒没数据必须转 STALE、USB 优先、SLEEP 是终态」的新鲜度状态机，
以及「上位机帧 → stats_t」的解析规则——都是纯 C 逻辑，跟 GPIO/LCD/WiFi 无关。
板子不在手边时，这两块完全可以在主机上真跑一遍；靠肉眼读代码看不出边界 bug。

状态机的权威定义是 [`docs/design/link-state-machine.model.json`](../../../docs/design/link-state-machine.model.json)
（已用 logicprobe 建模验证），本测试是在**实现层**再验一遍：模型说的迁移，C 代码真的那么走。

## 怎么跑

在仓库根目录：

```bash
bash firmware/test/host_link/run.sh
```

需要主机 C 编译器，按优先级找：`$GCC` → `gcc` → `clang`。

**Windows 默认没有主机 gcc**（ESP-IDF 的 `xtensa-esp32s3-elf-gcc` 是交叉编译器，不能编主机程序），
所以要么装 MinGW-w64 / MSYS2，要么显式指定：

```bash
GCC="/c/msys64/mingw64/bin/gcc.exe" bash firmware/test/host_link/run.sh
```

Linux / macOS 上 `gcc` 或 `clang` 一般自带，直接跑即可。
找不到编译器时脚本只打印一行提示并退出（不猜路径、不静默跳过）。

还需要 **ESP-IDF 源码**里的 cJSON：脚本按 `$IDF_PATH/components/json/cJSON` 找，
找不到就按常见安装位置试（`D:/ESP32-S3/Espressif/frameworks/esp-idf-v5.5.3`、`~/esp/esp-idf`、
`~/.espressif/frameworks/esp-idf`、`/opt/esp-idf`），都没有就提示设 `IDF_PATH`。

产物写到 `firmware/test/host_link/build/`（已被仓库 `.gitignore` 忽略）。

## 覆盖了什么（83 条断言）

| 组 | 验的东西 |
|---|---|
| `[1]` 状态机 | INIT 期间丢帧 → `boot_done` 进 NO_LINK → USB/WiFi 帧进对应在线态 → 3 个 tick 转 STALE(`age=3`) → WiFi 帧从 STALE 回到 ONLINE_WIFI → USB 帧抢占 → **WiFi 帧在 USB 在线时被忽略**（seq 与 age 都不变）→ `link_lost` 回 NO_LINK → `sleep` 进 SLEEP 终态后吃帧不动 |
| `[1]` 不变式 | 每次 tick 后断言 `ONLINE_* 时 age <= 2`（绝不在旧数据上显示"在线"） |
| `[2]` 真实帧 | 解析 `frame.json`——上位机 `pcmond --once` 的**真实输出**（424 B、24 核、带 GPU 段）：host/gpu.name 截断、cpu.temp=null→未知、mem/gpu/net/disk 各字段单位；并确认这份老帧**没有 `dsh` 段 → `dsh.valid=false`**（老帧兼容） |
| `[3]` 边界 | `gpu:null`（无独显）、`cpu.avg:null`、40 核截到 32、占用率越界夹到 0..100、截断 JSON / JSON 数组 / 空输入全部拒收 |
| `[4]` `dsh` 段 | `frame_dsh.json`（契约 §2.5）：`today.tok/in/out/cache/req/cost`、`week.tok/req/cache_pct/cost`、`proc.count/rss_mb/cpu_pct/port_up` 全部数值；**int64 token 回归**（2^40 的 token 必须原样，不能被 `cJSON` 的 `valueint(int)` 截断）；`port_up:false`；缺子字段时默认 0 |

`frame.json` 是 2026-11-10 在本机抓的上位机输出快照，字段口径见
[`docs/design/architecture.md` §2](../../../docs/design/architecture.md)。

## 它**不代表**什么

- **不代表实机验证。** 这里跑的是 PC 上的 x86 代码，没碰过 ESP32-S3 的外设：
  USB-Serial/JTAG 收帧、WiFi 连接、HTTP 轮询、NVS 读写、串口命令、深睡——这些只有上板才算数。
- 不验证时序/并发：`xSemaphoreTake/Give` 在 shim 里是空实现，真实 RTOS 下的竞态、栈溢出、
  中断优先级这里都测不到。
- 不验证链接层以外的代码（LCD 驱动、LVGL 界面）。

## 目录

```
host_link/
├── run.sh          # 找编译器 + 编 cJSON/link.c + 跑
├── test_link.c     # 断言本体（83 条）
├── frame.json      # 上位机真实帧快照（无 dsh，测老帧兼容）
├── frame_dsh.json  # 带 dsh 段的手工样例（契约 §2.5，测新段解析）
├── shim/           # 让 link.c 能在主机上编过的最小 IDF/FreeRTOS 替身
│   ├── esp_err.h   #   esp_err_t / ESP_OK / ESP_ERR_*
│   ├── esp_log.h   #   ESP_LOGx → stderr
│   ├── compat.h    #   MinGW 没有 strlcpy，宏映射到 pcm_strlcpy
│   └── freertos/   #   FreeRTOS.h / semphr.h / task.h（互斥锁是空实现）
└── build/          # 编译产物（gitignore）
```
