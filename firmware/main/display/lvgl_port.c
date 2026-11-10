#include "lvgl_port.h"
#include "esp_log.h"
#include "lvgl.h"

/* 占位实现（Lead 建的骨架）。由固件显示同学替换。 */

static const char *TAG = "lvgl_port";

esp_err_t lvgl_port_init(void)
{
    ESP_LOGW(TAG, "lvgl_port_init: stub, 尚未实现");
    return ESP_OK;
}

void lvgl_lock(void) {}
void lvgl_unlock(void) {}

uint32_t lvgl_port_fps(void) { return 0; }
