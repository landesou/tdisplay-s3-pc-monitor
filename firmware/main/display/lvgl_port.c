/**
 * LVGL 9 移植层：draw buffer、flush 回调、tick、LVGL 任务、互斥锁、FPS 统计。
 *
 * 参考：ESP-IDF v5.5.3 examples/peripherals/lcd/i80_controller 里的
 *       example_lvgl_flush_cb / example_lvgl_port_task（同一份移植层的官方写法）。
 * 与官方示例的差异（task-2 要求）：
 *   1. 不开 PSRAM，两块 draw buffer 从**内部 RAM** 分配（esp_lcd_i80_alloc_draw_buffer
 *      会按 DMA burst / cache line 要求对齐，比裸 heap_caps_malloc 稳）；
 *   2. 对外暴露 lvgl_lock/unlock 和 lvgl_port_fps()；
 *   3. FPS 用 esp_timer 时间戳统计，窗口 1 秒。
 */

#include <sys/lock.h>
#include <sys/param.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_heap_caps.h"
#include "esp_lcd_io_i80.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_log.h"
#include "esp_timer.h"

#include "lvgl.h"

#include "board.h"
#include "lcd_internal.h"
#include "lvgl_port.h"

static const char *TAG = "lvgl_port";

#define LVGL_TICK_PERIOD_MS    2
#define LVGL_TASK_MAX_DELAY_MS 500
#define LVGL_TASK_MIN_DELAY_MS (1000 / CONFIG_FREERTOS_HZ)
#define LVGL_TASK_STACK_SIZE   (6 * 1024)
#define LVGL_TASK_PRIORITY     2

/* LVGL 不是线程安全的：所有 lv_* 调用都要在 _lock_acquire/_lock_release 之间。
 * 与官方示例一致用 newlib 的 _lock_t（首次 acquire 时内部才建递归信号量）。 */
static _lock_t s_lvgl_lock;

static lv_display_t *s_display = NULL;
static void *s_draw_buf1 = NULL;
static void *s_draw_buf2 = NULL;

/* FPS：只在「一整帧刷完」（flush_is_last）时计数，避免双缓冲把一帧算成 2~3 帧 */
static volatile uint32_t s_fps = 0;
static uint32_t s_frames_in_window = 0;
static int64_t  s_fps_window_start = 0;

static esp_timer_handle_t s_tick_timer = NULL;

/* ---- flush 完成通知：i80 DMA 传完一批颜色数据后回调，放行 LVGL ---- */
static bool lvgl_flush_ready_cb(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_io_event_data_t *edata,
                                void *user_ctx)
{
    (void)panel_io;
    (void)edata;
    lv_display_t *disp = (lv_display_t *)user_ctx;
    lv_display_flush_ready(disp);
    return false;
}

static void lvgl_flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *px_map)
{
    esp_lcd_panel_handle_t panel = (esp_lcd_panel_handle_t)lv_display_get_user_data(disp);
    int32_t x1 = area->x1;
    int32_t y1 = area->y1;
    int32_t x2 = area->x2;
    int32_t y2 = area->y2;

    /* ST7789 的 RGB565 是**大端**（高字节先走 D7..D0），LVGL 的缓冲区是本机小端，
     * 所以发之前要把每个像素的两个字节换一下。官方示例同一行。 */
    lv_draw_sw_rgb565_swap(px_map, (uint32_t)(x2 - x1 + 1) * (uint32_t)(y2 - y1 + 1));
    esp_lcd_panel_draw_bitmap(panel, x1, y1, x2 + 1, y2 + 1, px_map);

    /* FPS 统计：只统计一帧里的最后一次 flush */
    if (lv_display_flush_is_last(disp)) {
        s_frames_in_window++;
        int64_t now = esp_timer_get_time();
        int64_t elapsed = now - s_fps_window_start;
        if (elapsed >= 1000000) {
            s_fps = (uint32_t)(((uint64_t)s_frames_in_window * 1000000ULL) / (uint64_t)elapsed);
            s_frames_in_window = 0;
            s_fps_window_start = now;
        }
    }
}

static void lvgl_tick_cb(void *arg)
{
    (void)arg;
    lv_tick_inc(LVGL_TICK_PERIOD_MS);
}

static void lvgl_port_task(void *arg)
{
    (void)arg;
    ESP_LOGI(TAG, "lvgl task started (prio %d, stack %d)", LVGL_TASK_PRIORITY, LVGL_TASK_STACK_SIZE);
    while (1) {
        uint32_t delay_ms = 0;
        _lock_acquire(&s_lvgl_lock);
        delay_ms = lv_timer_handler();
        _lock_release(&s_lvgl_lock);

        /* 下限防 LVGL 忙等把看门狗喂爆，上限防没刷新任务时睡死 */
        delay_ms = MAX(delay_ms, (uint32_t)LVGL_TASK_MIN_DELAY_MS);
        delay_ms = MIN(delay_ms, (uint32_t)LVGL_TASK_MAX_DELAY_MS);
        vTaskDelay(pdMS_TO_TICKS(delay_ms));
    }
}

esp_err_t lvgl_port_init(void)
{
    esp_lcd_panel_handle_t panel = lcd_panel_handle();
    esp_lcd_panel_io_handle_t io = lcd_panel_io();
    if (panel == NULL || io == NULL) {
        ESP_LOGE(TAG, "lcd not initialized (panel=%p io=%p)", (void *)panel, (void *)io);
        return ESP_ERR_INVALID_STATE;
    }

    _lock_init(&s_lvgl_lock);

    lv_init();
    s_display = lv_display_create(BOARD_LCD_H_RES, BOARD_LCD_V_RES);
    if (s_display == NULL) {
        ESP_LOGE(TAG, "lv_display_create failed");
        return ESP_ERR_NO_MEM;
    }

    /* 每块 320x40x2 = 25.6 KB，双缓冲放内部 RAM（v1 不开 PSRAM）。
     * 用 esp_lcd_i80_alloc_draw_buffer 是为了拿到 DMA burst 要求的对齐。 */
    size_t buf_size = BOARD_LCD_H_RES * LCD_DRAW_BUF_LINES * sizeof(lv_color16_t);
    s_draw_buf1 = esp_lcd_i80_alloc_draw_buffer(io, buf_size, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
    s_draw_buf2 = esp_lcd_i80_alloc_draw_buffer(io, buf_size, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
    if (s_draw_buf1 == NULL || s_draw_buf2 == NULL) {
        ESP_LOGE(TAG, "draw buffer alloc failed (%u B x2)", (unsigned)buf_size);
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "draw buffers %p / %p, %u B each", s_draw_buf1, s_draw_buf2, (unsigned)buf_size);

    lv_display_set_buffers(s_display, s_draw_buf1, s_draw_buf2, buf_size, LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_color_format(s_display, LV_COLOR_FORMAT_RGB565);
    lv_display_set_user_data(s_display, panel);
    lv_display_set_flush_cb(s_display, lvgl_flush_cb);

    /* DMA 传输完成 -> 通知 LVGL 这块 buffer 可以继续用了 */
    const esp_lcd_panel_io_callbacks_t cbs = {
        .on_color_trans_done = lvgl_flush_ready_cb,
    };
    esp_err_t err = esp_lcd_panel_io_register_event_callbacks(io, &cbs, s_display);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "register io callbacks failed: %s", esp_err_to_name(err));
        return err;
    }

    /* 2 ms 周期 tick 给 LVGL */
    const esp_timer_create_args_t tick_args = {
        .callback = lvgl_tick_cb,
        .name = "lvgl_tick",
    };
    err = esp_timer_create(&tick_args, &s_tick_timer);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "tick timer create failed: %s", esp_err_to_name(err));
        return err;
    }
    err = esp_timer_start_periodic(s_tick_timer, LVGL_TICK_PERIOD_MS * 1000);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "tick timer start failed: %s", esp_err_to_name(err));
        return err;
    }

    s_fps_window_start = esp_timer_get_time();
    s_frames_in_window = 0;
    s_fps = 0;

    BaseType_t ok = xTaskCreate(lvgl_port_task, "lvgl", LVGL_TASK_STACK_SIZE, NULL, LVGL_TASK_PRIORITY, NULL);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "create lvgl task failed");
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG, "init done: %dx%d, partial mode, %d lines/buf",
             BOARD_LCD_H_RES, BOARD_LCD_V_RES, LCD_DRAW_BUF_LINES);
    return ESP_OK;
}

void lvgl_lock(void)
{
    _lock_acquire(&s_lvgl_lock);
}

void lvgl_unlock(void)
{
    _lock_release(&s_lvgl_lock);
}

uint32_t lvgl_port_fps(void)
{
    return s_fps;
}
