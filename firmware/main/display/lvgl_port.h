#pragma once

#include <stdbool.h>
#include "esp_err.h"

/**
 * LVGL 9 移植层：显示 buffer、flush 回调、tick、LVGL 任务、互斥锁。
 * 实现者：固件显示同学（firmware/main/display/lvgl_port.c）
 *
 * 参考：官方 i80_controller 示例里的 example_lvgl_flush_cb / example_lvgl_port_task，
 * 区别是我们用内部 RAM 的静态双缓冲（不开 PSRAM），并且对外暴露 lock/unlock。
 */

esp_err_t lvgl_port_init(void);

/** LVGL 不是线程安全的：所有 lv_* 调用必须包在这对函数里。 */
void lvgl_lock(void);
void lvgl_unlock(void);

/** 当前实际帧率（用于调试页显示），没数据时返回 0。 */
uint32_t lvgl_port_fps(void);
