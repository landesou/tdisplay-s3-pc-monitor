#pragma once

#include <stdint.h>
#include "esp_err.h"

/**
 * LCD 驱动层：Intel 8080 并口 + ST7789
 * 实现者：固件显示同学（firmware/main/display/lcd.c）
 *
 * 参考实现：ESP-IDF 官方示例 examples/peripherals/lcd/i80_controller
 *           （esp_lcd_new_i80_bus + esp_lcd_new_panel_st7789 + 双缓冲）
 */

/** 初始化电源脚、8080 总线、ST7789 面板，并把面板旋成横向 320x170。 */
esp_err_t lcd_init(void);

/** 背光亮度 0..100（LEDC PWM）。 */
void lcd_backlight_set(uint8_t pct);

/**
 * Bring-up 自检图案：画边框 + 四角标记 + R/G/B 色块 + 文字，用来一眼判断
 * offset / mirror / swap_xy / invert / RGB-BGR 五个参数对不对。
 * variant 0..3 对应 4 组候选参数（见 docs/journal.md 待验证项）。
 */
void lcd_test_pattern(int variant);
