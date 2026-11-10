#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_sleep.h"
#include "nvs_flash.h"
#include "driver/gpio.h"

#include "board.h"
#include "stats.h"
#include "config/cfg.h"
#include "display/lcd.h"
#include "display/lcd_internal.h"   /* 标定模式开关的唯一状态源（lcd_test_pattern_active） */
#include "display/lvgl_port.h"
#include "ui/ui.h"
#include "link/link.h"

#include "esp_lcd_panel_io.h"
#include "esp_lcd_types.h"

static const char *TAG = "tdisplay-pcmon";

/* =====================================================================
 * 开机显示自检（bring-up 排障用，可用 CONFIG/boot 参数关闭）
 *
 * 为什么要有它：LVGL 和 lcd_test_pattern 都走 esp_lcd_panel_draw_bitmap()，
 * 而这个驱动的 draw_bitmap 只加 gap、不处理旋转。如果总线/寻址根本没通，
 * 两条路径都会"调用成功但屏上什么都没有"，无法区分。
 * 这里绕过 panel 驱动，直接下发 CASET/RASET/RAMWR + tx_color，
 * 用最原始的物理坐标系（0..239 列 × 0..319 行）刷整屏纯色。
 * 能看到颜色闪 = i80 总线与面板通信正常，问题只在坐标系/参数；
 * 完全没反应 = 硬件层（引脚/供电/时序）问题。
 * ===================================================================== */
#define BOOT_DISPLAY_SELFTEST 0  /* bring-up 时改回 1：上电先刷 3 屏纯色自检 */

#if BOOT_DISPLAY_SELFTEST
/**
 * 分块刷一个矩形：**每块都重新设窗口 + 重新发 RAMWR**。
 *
 * 为什么这样：ST7789 在一次 RAMWR 之后会持续收数据，但 i80 每笔传输都会拉 CS，
 * 有些面板在 CS 上升沿后就不再续写当前窗口（实测现象正是"只刷出一条/半屏"）。
 * 每块自成一次「设窗口 + RAMWR + 数据」，语义最干净，和大驱动的 draw_bitmap 一致。
 */
static void selftest_fill_rect(esp_lcd_panel_io_handle_t io, int x0, int y0, int w, int h,
                               uint16_t color, const char *name)
{
    static uint16_t row_buf[240];
    uint16_t be = (uint16_t)((color >> 8) | (color << 8));  /* 面板收大端 */
    for (int i = 0; i < w && i < 240; i++) {
        row_buf[i] = be;
    }
    for (int y = y0; y < y0 + h; y++) {
        uint8_t caset[4] = { (x0) >> 8, (x0) & 0xff, (x0 + w - 1) >> 8, (x0 + w - 1) & 0xff };
        uint8_t raset[4] = { y >> 8, y & 0xff, y >> 8, y & 0xff };
        esp_err_t err = esp_lcd_panel_io_tx_param(io, 0x2A, caset, 4);
        if (err == ESP_OK) err = esp_lcd_panel_io_tx_param(io, 0x2B, raset, 4);
        if (err == ESP_OK) err = esp_lcd_panel_io_tx_color(io, 0x2C, row_buf, (size_t)w * 2);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "[selftest] %s: failed at y=%d: %s", name, y, esp_err_to_name(err));
            return;
        }
    }
    ESP_LOGI(TAG, "[selftest] %s: rect(%d,%d %dx%d) done", name, x0, y0, w, h);
}

static void boot_display_selftest(void)
{
    esp_lcd_panel_io_handle_t io = lcd_panel_io();
    if (!io) {
        ESP_LOGE(TAG, "[selftest] no panel io, skip");
        return;
    }
    /* RD(GPIO9) 必须保持高（非读状态）：拉低会让面板向数据总线驱动，
     * 与我们的写操作抢线，典型现象就是"半屏白 + 半屏花屏"。 */
    {
        gpio_config_t rd = { .pin_bit_mask = 1ULL << BOARD_LCD_RD, .mode = GPIO_MODE_OUTPUT };
        ESP_ERROR_CHECK(gpio_config(&rd));
        ESP_ERROR_CHECK(gpio_set_level(BOARD_LCD_RD, 1));
        ESP_LOGI(TAG, "[selftest] RD(GPIO%d) driven HIGH", BOARD_LCD_RD);
    }
    /* ST7789 上电默认 sleep-in：不 SLPOUT + DISPON，屏上不会有任何变化 */
    esp_lcd_panel_io_tx_param(io, 0x11, NULL, 0);   /* SLPOUT */
    vTaskDelay(pdMS_TO_TICKS(120));
    esp_lcd_panel_io_tx_param(io, 0x29, NULL, 0);   /* DISPON */

    /* 画面 1/3：整块物理 RAM（240x320）刷红 —— 看是否整屏变红 */
    ESP_LOGI(TAG, "[selftest] screen 1/3: full physical RAM = RED");
    selftest_fill_rect(io, 0, 0, 240, 320, 0xF800, "ram-red");
    vTaskDelay(pdMS_TO_TICKS(2500));

    /* 画面 2/3：只刷「可见区」（列 35..204）绿 —— 验证 170 宽的偏移假设 */
    ESP_LOGI(TAG, "[selftest] screen 2/3: visible window x=35..204 = GREEN");
    selftest_fill_rect(io, 35, 0, 170, 320, 0x07E0, "visible-green");
    vTaskDelay(pdMS_TO_TICKS(2500));

    /* 画面 3/3：四象限（物理坐标）—— 一次性判定镜像/翻转：
     *   左上红 / 右上绿 / 左下蓝 / 右下白 */
    ESP_LOGI(TAG, "[selftest] screen 3/3: quadrants TL=RED TR=GREEN BL=BLUE BR=WHITE");
    selftest_fill_rect(io, 0,   0,   120, 160, 0xF800, "q-tl-red");
    selftest_fill_rect(io, 120, 0,   120, 160, 0x07E0, "q-tr-green");
    selftest_fill_rect(io, 0,   160, 120, 160, 0x001F, "q-bl-blue");
    selftest_fill_rect(io, 120, 160, 120, 160, 0xFFFF, "q-br-white");
    vTaskDelay(pdMS_TO_TICKS(4500));

    ESP_LOGI(TAG, "[selftest] done, handing over to LVGL");
}
#endif  /* BOOT_DISPLAY_SELFTEST */

/* 亮度三档，BOOT 键循环切换 */
static const uint8_t k_bright_levels[] = {100, 55, 20};
static int s_bright_idx = 0;

static void buttons_init(void)
{
    gpio_config_t cfg = {
        .pin_bit_mask = (1ULL << BOARD_BTN_BOOT) | (1ULL << BOARD_BTN_KEY),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&cfg));
}

/* 简单按键扫描：软件消抖 + 短按/长按。100ms 调用一次。
 * KEY  短按：翻页；KEY  长按(2s)：深睡。
 * BOOT 短按：切换亮度；BOOT 长按(2s)：进入/退出「屏幕标定模式」（配合 lcd_test_variant_*）。
 *   ——标定模式下 BOOT 短按改成切下一组候选参数，便于板子到手后不重烧固件就轮换 4 组参数。
 *   标定模式的开关状态以 lcd.c 为准（lcd_test_pattern_active()），main 不另存状态。 */
static bool s_force_repaint = false;

static void buttons_poll(void)
{
    static bool key_down_prev = false;
    static bool boot_down_prev = false;
    static int  key_hold_ticks = 0;
    static int  boot_hold_ticks = 0;
    static bool key_long_fired = false;
    static bool boot_long_fired = false;

    bool key_down = gpio_get_level(BOARD_BTN_KEY) == 0;
    bool boot_down = gpio_get_level(BOARD_BTN_BOOT) == 0;

    /* --- KEY --- */
    if (key_down) {
        if (key_hold_ticks < 1000) {
            key_hold_ticks++;
        }
        if (!key_long_fired && key_hold_ticks >= 20) { /* 20 * 100ms = 2s */
            key_long_fired = true;
            ESP_LOGI(TAG, "KEY long press -> deep sleep");
            link_sleep();
            lcd_backlight_set(0);
            vTaskDelay(pdMS_TO_TICKS(50));
            esp_deep_sleep_start();
        }
    }
    if (!key_down && key_down_prev) {
        /* 抬起：没触发长按才算短按 */
        if (!key_long_fired) {
            lvgl_lock();
            ui_next_page();
            lvgl_unlock();
            cfg_set_page(ui_page());
            ESP_LOGI(TAG, "KEY short press -> page %u", ui_page());
        }
        key_hold_ticks = 0;
        key_long_fired = false;
    }
    key_down_prev = key_down;

    /* --- BOOT：短按切亮度 / 标定模式下切参数组；长按 2s 切换标定模式 --- */
    if (boot_down) {
        if (boot_hold_ticks < 1000) {
            boot_hold_ticks++;
        }
        if (!boot_long_fired && boot_hold_ticks >= 20) { /* 20 * 100ms = 2s */
            boot_long_fired = true;
            /* 单一状态源：标定模式的开关以 lcd.c 的 s_test_active 为准（串口 `lcd` 命令
             * 也会置它）。main 不再维护自己的布尔量，否则从串口进标定后 BOOT 开关会错乱。 */
            if (lcd_test_pattern_active()) {
                /* 测试图是绕过 LVGL 直写面板的：必须让 UI 层清标志 + 整屏 invalidate */
                lvgl_lock();
                ui_exit_test_mode();
                lvgl_unlock();
                s_force_repaint = true;
                ESP_LOGI(TAG, "LCD calibration mode OFF");
            } else {
                lvgl_lock();
                int v = lcd_test_variant_get();
                lcd_test_pattern(v);
                lvgl_unlock();
                ESP_LOGI(TAG, "LCD calibration mode ON, variant %d", v);
            }
        }
    }
    if (!boot_down && boot_down_prev) {
        if (!boot_long_fired) {
            if (lcd_test_pattern_active()) {
                lvgl_lock();
                int v = lcd_test_variant_next();
                lvgl_unlock();
                ESP_LOGI(TAG, "LCD calibration variant -> %d", v);
            } else {
                s_bright_idx = (s_bright_idx + 1) % (int)(sizeof(k_bright_levels) / sizeof(k_bright_levels[0]));
                lcd_backlight_set(k_bright_levels[s_bright_idx]);
                cfg_set_brightness(k_bright_levels[s_bright_idx]);
                ESP_LOGI(TAG, "brightness -> %u%%", k_bright_levels[s_bright_idx]);
            }
        }
        boot_hold_ticks = 0;
        boot_long_fired = false;
    }
    boot_down_prev = boot_down;
}

/* 主任务：100ms 节拍。1s 走一次状态机 tick，有新帧就刷 UI。 */
static void app_task(void *arg)
{
    (void)arg;
    uint32_t last_seq = 0;
    bool     last_has = false;
    int      tick_countdown = 10;

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(100));

        buttons_poll();

        if (--tick_countdown <= 0) {
            tick_countdown = 10;
            link_tick_1s();
        }

        stats_t snap;
        bool    has = link_get_latest(&snap);
        uint32_t seq = link_frame_seq();
        link_state_t st = link_state();

        /* 标定模式：屏幕交给 lcd_test_pattern，UI 不要抢刷新（状态源以 lcd.c 为准） */
        if (lcd_test_pattern_active() && !s_force_repaint) {
            continue;
        }

        bool need_repaint = s_force_repaint || (has != last_has) ||
                            (has && seq != last_seq) ||
                            (st != LINK_ONLINE_USB && st != LINK_ONLINE_WIFI);
        if (need_repaint) {
            s_force_repaint = false;
            last_has = has;
            last_seq = seq;
            lvgl_lock();
            ui_update(has ? &snap : NULL, st, link_age_s());
            lvgl_unlock();
        }
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "boot: T-Display-S3 PC monitor");

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    ESP_ERROR_CHECK(cfg_init());

    /* RD(读使能) 必须从第一笔总线操作之前就保持高电平：
     * 浮空/拉低会让面板向 8 位数据总线反向驱动，与我们的写抢线 → 半屏白+半屏花屏。
     * 官方 Arduino 配置里 RD 也被当作普通输出脚驱动（TFT_RD 9）。 */
    {
        gpio_config_t rd = { .pin_bit_mask = 1ULL << BOARD_LCD_RD, .mode = GPIO_MODE_OUTPUT };
        ESP_ERROR_CHECK(gpio_config(&rd));
        ESP_ERROR_CHECK(gpio_set_level(BOARD_LCD_RD, 1));
    }

    /* 屏幕：先上电，再初始化总线与面板 */
    ESP_ERROR_CHECK(lcd_init());
    lcd_backlight_set(cfg_brightness());
#if BOOT_DISPLAY_SELFTEST
    boot_display_selftest();
#endif
    ESP_ERROR_CHECK(lvgl_port_init());

    lvgl_lock();
    ui_init();
    lvgl_unlock();

    ESP_ERROR_CHECK(link_init());
    link_boot_done();

    buttons_init();
    xTaskCreate(app_task, "app", 4096, NULL, 4, NULL);

    ESP_LOGI(TAG, "boot done, %s", link_state_name(link_state()));
}
