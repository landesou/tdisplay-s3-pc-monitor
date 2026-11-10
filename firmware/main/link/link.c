#include <math.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "link.h"
#include "link_internal.h"

/* 链路层核心：新鲜度/仲裁状态机 + 最新帧缓存 + JSON 解析。
 *
 * 状态机权威定义：docs/design/link-state-machine.model.json（logicprobe 验证，一条迁移都不许改）
 *   INIT --boot_done--> NO_LINK
 *   NO_LINK/STALE --usb_frame--> ONLINE_USB ; NO_LINK/STALE --wifi_frame--> ONLINE_WIFI
 *   ONLINE_* --tick(age<2)--> 自己(age++) ; --tick(age>=2)--> STALE(age=3)
 *   ONLINE_* --link_lost--> NO_LINK ; 任意态 --sleep--> SLEEP
 *   （ONLINE_USB 收到 wifi_frame 只刷新数据、不切态 = 模型里的自环，USB 优先）
 */

static const char *TAG = "link";

/* 3 秒无新帧转 STALE：age 到 2 的下一个 tick 就离开 */
#define LINK_STALE_AGE 3

static SemaphoreHandle_t s_lock;
static stats_t      s_latest;
static bool         s_has_frame;
static uint32_t     s_frame_count;     /* 累计收到帧数；UI 看它的变化决定要不要重绘 */
static link_state_t s_state = LINK_INIT;
static uint32_t     s_age;

const char *link_state_name(link_state_t st)
{
    switch (st) {
    case LINK_INIT:        return "INIT";
    case LINK_NO_LINK:     return "NO_LINK";
    case LINK_ONLINE_USB:  return "ONLINE_USB";
    case LINK_ONLINE_WIFI: return "ONLINE_WIFI";
    case LINK_STALE:       return "STALE";
    case LINK_SLEEP:       return "SLEEP";
    default:               return "?";
    }
}

/* ---------------- JSON 解析辅助 ---------------- */

static const cJSON *obj_item(const cJSON *obj, const char *key)
{
    if (!cJSON_IsObject(obj)) {
        return NULL;
    }
    return cJSON_GetObjectItemCaseSensitive(obj, key);
}

static int j_int(const cJSON *obj, const char *key, int def)
{
    const cJSON *it = obj_item(obj, key);
    if (!cJSON_IsNumber(it)) {
        return def;
    }
    return (int)lround(it->valuedouble);
}

static float j_float(const cJSON *obj, const char *key, float def)
{
    const cJSON *it = obj_item(obj, key);
    if (!cJSON_IsNumber(it)) {
        return def;
    }
    return (float)it->valuedouble;
}

static void j_str(const cJSON *obj, const char *key, char *dst, size_t dst_len)
{
    const cJSON *it = obj_item(obj, key);
    if (cJSON_IsString(it) && it->valuestring) {
        strlcpy(dst, it->valuestring, dst_len);
    }
}

static int clamp_pct(int v)
{
    if (v < 0) {
        return 0;
    }
    if (v > 100) {
        return 100;
    }
    return v;
}

bool link_parse_frame(const char *json, size_t len, stats_t *out)
{
    if (json == NULL || out == NULL || len == 0) {
        return false;
    }

    cJSON *root = cJSON_ParseWithLength(json, len);
    if (root == NULL) {
        return false;   /* 非法 JSON 直接丢；调用方按 debug 级别记，不刷屏 */
    }

    bool ok = cJSON_IsObject(root);
    if (ok) {
        memset(out, 0, sizeof(*out));
        out->cpu_temp = -1.0f;   /* -1 = 未知（契约 §2） */
        out->gpu_temp = -1;

        out->seq      = (uint32_t)j_int(root, "seq", 0);
        out->pc_time  = (uint32_t)j_int(root, "t", 0);
        out->uptime_s = (uint32_t)j_int(root, "up", 0);
        j_str(root, "host", out->host, sizeof(out->host));
        if (out->host[0] == '\0') {
            strlcpy(out->host, "PC", sizeof(out->host));
        }

        /* ---- cpu ---- */
        const cJSON *cpu = obj_item(root, "cpu");
        if (cJSON_IsObject(cpu)) {
            out->cpu_pct = j_float(cpu, "avg", 0.0f);
            out->cpu_mhz = j_int(cpu, "mhz", 0);
            const cJSON *temp = obj_item(cpu, "temp");
            out->cpu_temp = cJSON_IsNumber(temp) ? (float)temp->valuedouble : -1.0f;

            const cJSON *cores = obj_item(cpu, "cores");
            if (cJSON_IsArray(cores)) {
                int n = cJSON_GetArraySize(cores);
                if (n > STATS_MAX_CORES) {
                    n = STATS_MAX_CORES;   /* 契约：设备端最多渲染 32 路 */
                }
                out->n_cores = 0;
                for (int i = 0; i < n; i++) {
                    const cJSON *c = cJSON_GetArrayItem(cores, i);
                    if (cJSON_IsNumber(c)) {
                        out->cores[out->n_cores++] = (uint8_t)clamp_pct((int)lround(c->valuedouble));
                    }
                }
            }
        }

        /* ---- mem ---- */
        const cJSON *mem = obj_item(root, "mem");
        if (cJSON_IsObject(mem)) {
            out->mem_pct       = j_float(mem, "pct", 0.0f);
            out->mem_used_mib  = j_int(mem, "used", 0);
            out->mem_total_mib = j_int(mem, "total", 0);
        }

        /* ---- gpu：整块可以是 null（没有 nvidia-smi 时上位机就这样发）---- */
        const cJSON *gpu = obj_item(root, "gpu");
        if (cJSON_IsObject(gpu)) {
            out->has_gpu = true;
            j_str(gpu, "name", out->gpu_name, sizeof(out->gpu_name));
            out->gpu_util       = j_int(gpu, "util", 0);
            out->gpu_temp       = j_int(gpu, "temp", -1);
            out->gpu_vram_used  = j_int(gpu, "vram_used", 0);
            out->gpu_vram_total = j_int(gpu, "vram_total", 0);
            out->gpu_power      = j_float(gpu, "power", 0.0f);
        }

        /* ---- net / disk ---- */
        const cJSON *net = obj_item(root, "net");
        if (cJSON_IsObject(net)) {
            out->net_down = j_float(net, "down", 0.0f);
            out->net_up   = j_float(net, "up", 0.0f);
        }
        const cJSON *disk = obj_item(root, "disk");
        if (cJSON_IsObject(disk)) {
            out->disk_read  = j_float(disk, "read", 0.0f);
            out->disk_write = j_float(disk, "write", 0.0f);
        }
    }

    cJSON_Delete(root);
    return ok;
}

/* ---------------- 状态机 ---------------- */

void link_on_frame(const stats_t *snap, link_state_t source)
{
    if (snap == NULL || s_lock == NULL) {
        return;
    }
    if (source != LINK_ONLINE_USB && source != LINK_ONLINE_WIFI) {
        return;
    }

    xSemaphoreTake(s_lock, portMAX_DELAY);

    /* 模型：INIT 期间到达的帧被忽略；SLEEP 是终态，收到帧也不动 */
    if (s_state == LINK_INIT || s_state == LINK_SLEEP) {
        xSemaphoreGive(s_lock);
        ESP_LOGD(TAG, "frame dropped in state %s", link_state_name(s_state));
        return;
    }

    s_latest = *snap;
    s_has_frame = true;
    s_frame_count++;
    s_age = 0;

    if (s_state == LINK_ONLINE_USB) {
        /* USB 优先：WiFi 帧只刷新数据，不切态（模型里的自环） */
    } else if (s_state == LINK_ONLINE_WIFI && source == LINK_ONLINE_USB) {
        s_state = LINK_ONLINE_USB;   /* 插上 USB 立刻接管 */
    } else {
        s_state = source;            /* NO_LINK / STALE → 对应在线态 */
    }

    xSemaphoreGive(s_lock);
}

void link_on_link_lost(void)
{
    if (s_lock == NULL) {
        return;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_state == LINK_ONLINE_USB || s_state == LINK_ONLINE_WIFI || s_state == LINK_STALE) {
        s_state = LINK_NO_LINK;
        s_age = 0;
    }
    xSemaphoreGive(s_lock);
}

void link_boot_done(void)
{
    if (s_lock == NULL) {
        return;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_state == LINK_INIT) {
        s_state = LINK_NO_LINK;
        s_age = 0;
    }
    xSemaphoreGive(s_lock);
}

void link_tick_1s(void)
{
    if (s_lock == NULL) {
        return;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    switch (s_state) {
    case LINK_ONLINE_USB:
    case LINK_ONLINE_WIFI:
        if (s_age < 2) {
            s_age++;                    /* tick(age<2) → 自己(age++) */
        } else {
            s_state = LINK_STALE;       /* tick(age>=2) → STALE(age=3) */
            s_age = LINK_STALE_AGE;
            ESP_LOGW(TAG, "no frame for 3s -> STALE");
        }
        break;
    case LINK_STALE:
        s_age = LINK_STALE_AGE;
        break;
    default:
        break;
    }
    xSemaphoreGive(s_lock);
}

void link_sleep(void)
{
    if (s_lock == NULL) {
        return;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_state = LINK_SLEEP;
    xSemaphoreGive(s_lock);
}

/* ---------------- 对外读取 ---------------- */

bool link_get_latest(stats_t *out)
{
    if (out == NULL || s_lock == NULL) {
        return false;
    }
    bool has = false;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_has_frame) {
        *out = s_latest;
        has = true;
    }
    xSemaphoreGive(s_lock);
    return has;
}

uint32_t link_frame_seq(void)
{
    if (s_lock == NULL) {
        return 0;
    }
    uint32_t seq;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    seq = s_frame_count;
    xSemaphoreGive(s_lock);
    return seq;
}

uint32_t link_age_s(void)
{
    if (s_lock == NULL) {
        return 0;
    }
    uint32_t age;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    age = s_age;
    xSemaphoreGive(s_lock);
    return age;
}

link_state_t link_state(void)
{
    if (s_lock == NULL) {
        return LINK_INIT;
    }
    link_state_t st;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    st = s_state;
    xSemaphoreGive(s_lock);
    return st;
}

/* ---------------- 初始化 ---------------- */

esp_err_t link_init(void)
{
    s_lock = xSemaphoreCreateMutex();
    if (s_lock == NULL) {
        ESP_LOGE(TAG, "mutex create failed");
        return ESP_ERR_NO_MEM;
    }
    memset(&s_latest, 0, sizeof(s_latest));
    s_latest.cpu_temp = -1.0f;
    s_latest.gpu_temp = -1;
    s_has_frame = false;
    s_frame_count = 0;
    s_state = LINK_INIT;
    s_age = 0;

    /* USB 链路总是启动（一根线同时供电+数据）；WiFi 链路自己判断是否已配置 */
    esp_err_t err = link_usb_start();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "usb link start failed: %s (继续，WiFi 仍可用)", esp_err_to_name(err));
    }
    err = link_http_start();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "http link start failed: %s", esp_err_to_name(err));
    }

    ESP_LOGI(TAG, "link module ready (state=%s)", link_state_name(s_state));
    return ESP_OK;
}
