#pragma once

#include <stdbool.h>

#include "esp_lcd_types.h"

/**
 * display 模块**内部**接口（lcd.c <-> lvgl_port.c），不属于对外契约。
 *
 * 为什么需要它：lvgl_port_init(void) 没有参数（头文件由 Lead 定稿），
 * 但 flush 回调必须拿到 panel handle，所以由 lcd.c 暴露这两个 getter，
 * 只给同目录的移植层用。对外 API 仍然只有 lcd.h 里的那几个函数。
 */

/** 面板 handle；lcd_init() 之前返回 NULL。 */
esp_lcd_panel_handle_t lcd_panel_handle(void);

/** i80 panel IO handle；lcd_init() 之前返回 NULL。 */
esp_lcd_panel_io_handle_t lcd_panel_io(void);

/* 每块 LVGL draw buffer 的行数（320x40，见 task-2）。lcd.c 用它算
 * i80 总线的 max_transfer_bytes，lvgl_port.c 用它分配两块 buffer——
 * 两处必须一致，所以放在这里共享。 */
#define LCD_DRAW_BUF_LINES 40

/* ---- Bring-up 测试图模式 ----
 * lcd_test_pattern() 把画面直接写进面板（绕过 LVGL）。如果 UI 层继续按帧刷新，
 * LVGL 下一次重绘就会把测试图盖掉，标定就白做了。所以：
 *   - 测试图激活后 ui.c 调 lcd_test_pattern_active() 暂停刷新；
 *   - 用户按 KEY 翻页时 ui_next_page() 调 lcd_test_pattern_stop() 退出测试模式。
 */
bool lcd_test_pattern_active(void);
void lcd_test_pattern_stop(void);
