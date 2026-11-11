#pragma once

#include <stdbool.h>
#include <stdint.h>

/** 设备侧解析出来的一帧性能快照（对应 docs/design/architecture.md §2） */
#define STATS_MAX_CORES 32
#define STATS_HOST_LEN 16
#define STATS_GPU_NAME_LEN 20

/** DSH（DeepSeek Harness）运行状态与 token 消耗（契约 §2.5，v1.1 可选段）
 *  注意：所有 token 字段由 PC 端直接给累计值，设备只负责显示，不做单位换算逻辑以外的计算。 */
typedef struct {
    bool     valid;            /* 帧里是否带 dsh 段；false = 设备显示 -- */

    /* 今日（本地时区 00:00 起） */
    uint64_t today_tok;
    uint64_t today_in;         /* 未命中输入 */
    uint64_t today_out;
    uint64_t today_cache;      /* 缓存命中输入 */
    uint32_t today_req;        /* LLM 请求数 */
    float    today_cost;       /* 元，估算值（界面必须标 ≈） */

    /* 近 7 天滚动（含今天） */
    uint64_t week_tok;
    uint32_t week_req;
    float    week_cost;
    uint8_t  week_cache_pct;   /* 缓存命中率 % */

    /* DSH 进程 */
    uint16_t proc_count;
    uint16_t proc_rss_mb;
    uint8_t  proc_cpu_pct;
    bool     proc_port_up;     /* 本地 GUI 端口（10899）是否在监听 */
} dsh_stats_t;

typedef struct {
    /* CPU */
    float    cpu_pct;          /* 0..100 */
    int      cpu_mhz;          /* MHz，0 表示未知 */
    float    cpu_temp;         /* °C，-1 表示未知 */
    int      n_cores;          /* 有效核心数，<= STATS_MAX_CORES */
    uint8_t  cores[STATS_MAX_CORES];

    /* 内存 */
    float    mem_pct;
    int      mem_used_mib;
    int      mem_total_mib;

    /* GPU */
    bool     has_gpu;
    char     gpu_name[STATS_GPU_NAME_LEN];
    int      gpu_util;         /* % */
    int      gpu_temp;         /* °C */
    int      gpu_vram_used;    /* MiB */
    int      gpu_vram_total;   /* MiB */
    float    gpu_power;        /* W */

    /* 吞吐（MiB/s） */
    float    net_down;
    float    net_up;
    float    disk_read;
    float    disk_write;

    /* 其它 */
    uint32_t uptime_s;
    uint32_t pc_time;          /* unix 秒 */
    char     host[STATS_HOST_LEN];
    uint32_t seq;              /* 上位机单调帧号 */

    /* DSH 统计（v1.1，字段可选；valid=false 时 UI 显示 --） */
    dsh_stats_t dsh;
} stats_t;

/** 链路状态（必须与 docs/design/link-state-machine.model.json 一致） */
typedef enum {
    LINK_INIT = 0,
    LINK_NO_LINK,
    LINK_ONLINE_USB,
    LINK_ONLINE_WIFI,
    LINK_STALE,
    LINK_SLEEP,
} link_state_t;

const char *link_state_name(link_state_t st);
