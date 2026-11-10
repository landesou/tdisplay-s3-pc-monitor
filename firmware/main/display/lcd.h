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

/*
 * 串口标定命令（Lead 批准；落点：fw-link 的串口命令 `lcd` / `lcd N`）。
 * 这三组参数只能上板实测，所以板子到手后要能不重烧固件就轮换。
 */
/** 当前候选参数组号 0..3。 */
int lcd_test_variant_get(void);
/** 切到下一组候选参数并立刻重画测试图，返回切换后的组号。 */
int lcd_test_variant_next(void);
/** 设定指定组（0..3）并重画测试图。 */
void lcd_test_variant_set(int variant);
