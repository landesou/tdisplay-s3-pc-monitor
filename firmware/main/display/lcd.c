#include "lcd.h"
#include "esp_log.h"

/* 占位实现（Lead 建的骨架）。由固件显示同学替换成真正的 i80 + ST7789 驱动。
 * 参考：ESP-IDF examples/peripherals/lcd/i80_controller */

static const char *TAG = "lcd";

esp_err_t lcd_init(void)
{
    ESP_LOGW(TAG, "lcd_init: stub, 尚未实现");
    return ESP_OK;
}

void lcd_backlight_set(uint8_t pct)
{
    (void)pct;
}

void lcd_test_pattern(int variant)
{
    (void)variant;
}
