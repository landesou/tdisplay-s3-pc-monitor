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
#include "display/lvgl_port.h"
#include "ui/ui.h"
#include "link/link.h"

static const char *TAG = "tdisplay-pcmon";

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
 * BOOT 短按：切换亮度（BOOT 是 strapping 脚，不支持长按）。 */
static void buttons_poll(void)
{
    static bool key_down_prev = false;
    static bool boot_down_prev = false;
    static int  key_hold_ticks = 0;
    static bool key_long_fired = false;

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

    /* --- BOOT（只在按下沿动作）--- */
    if (boot_down && !boot_down_prev) {
        s_bright_idx = (s_bright_idx + 1) % (int)(sizeof(k_bright_levels) / sizeof(k_bright_levels[0]));
        lcd_backlight_set(k_bright_levels[s_bright_idx]);
        cfg_set_brightness(k_bright_levels[s_bright_idx]);
        ESP_LOGI(TAG, "brightness -> %u%%", k_bright_levels[s_bright_idx]);
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

        bool need_repaint = (has != last_has) || (has && seq != last_seq) ||
                            (st != LINK_ONLINE_USB && st != LINK_ONLINE_WIFI);
        if (need_repaint) {
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

    /* 屏幕：先上电，再初始化总线与面板 */
    ESP_ERROR_CHECK(lcd_init());
    lcd_backlight_set(cfg_brightness());
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
