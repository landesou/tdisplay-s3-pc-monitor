#pragma once

#include <stdbool.h>
#include <stdint.h>

/** 设备侧解析出来的一帧性能快照（对应 docs/design/architecture.md §2） */
#define STATS_MAX_CORES 32
#define STATS_HOST_LEN 16
#define STATS_GPU_NAME_LEN 20

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
