#include <string.h>
#include "link.h"
#include "esp_log.h"

/* 占位实现（Lead 建的骨架）。由固件链路同学替换成真正的状态机 + 双传输。
 * 状态机规范：docs/design/link-state-machine.model.json（已 logicprobe 验证） */

static const char *TAG = "link";

static link_state_t s_state = LINK_INIT;
static uint32_t     s_age = 0;

const char *link_state_name(link_state_t st)
{
    switch (st) {
    case LINK_INIT:         return "INIT";
    case LINK_NO_LINK:      return "NO_LINK";
    case LINK_ONLINE_USB:   return "ONLINE_USB";
    case LINK_ONLINE_WIFI:  return "ONLINE_WIFI";
    case LINK_STALE:        return "STALE";
    case LINK_SLEEP:        return "SLEEP";
    default:                return "?";
    }
}

esp_err_t link_init(void)
{
    ESP_LOGW(TAG, "link_init: stub, 尚未实现");
    return ESP_OK;
}

void link_boot_done(void) { s_state = LINK_NO_LINK; }

void link_tick_1s(void)
{
    if (s_state == LINK_ONLINE_USB || s_state == LINK_ONLINE_WIFI) {
        s_age++;
        if (s_age >= 3) { s_state = LINK_STALE; }
    } else if (s_state == LINK_STALE) {
        s_age = 3;
    }
}

bool link_get_latest(stats_t *out) { (void)out; return false; }
uint32_t link_frame_seq(void) { return 0; }
uint32_t link_age_s(void) { return s_age; }
link_state_t link_state(void) { return s_state; }
void link_sleep(void) { s_state = LINK_SLEEP; }
