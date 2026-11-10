#include "ui.h"
#include "esp_log.h"
#include "lvgl.h"

/* 占位实现（Lead 建的骨架）。由固件显示同学替换成真正的两页界面。 */

static const char *TAG = "ui";

void ui_init(void)
{
    ESP_LOGW(TAG, "ui_init: stub, 尚未实现");
}

void ui_update(const stats_t *s, link_state_t st, uint32_t age_s)
{
    (void)s; (void)st; (void)age_s;
}

void ui_next_page(void) {}

uint8_t ui_page(void) { return 0; }
