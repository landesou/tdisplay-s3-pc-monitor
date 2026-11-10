#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "config/cfg.h"
#include "link_internal.h"

/* USB-Serial/JTAG：收行 → JSON 帧 / 串口命令行，设备 → PC 的 hello / cfg_ok。
 *
 * 为什么用 driver 而不是直接读 stdin：
 *   USB-Serial/JTAG 的 RX FIFO 只有 64 字节，上位机一帧 400~600 B 是突发写入的，
 *   纯轮询（console 的 no-driver VFS 路径）会丢字节。驱动的 ISR 会立刻把 FIFO 搬进
 *   4096 B 的 ring buffer，这才是可靠收帧的方式。
 *   console 用的 VFS 不会安装驱动（它用 no_driver 函数指针），所以这里 install 不会冲突；
 *   装完后调 usb_serial_jtag_vfs_use_driver() 让 console 日志也走驱动的 TX 互斥锁，
 *   避免日志和我们的 JSON 行在同一个 FIFO 里交错。
 */

/* lcd 变体切换命令（`lcd` / `lcd N`）：调 fw-display 提供的 lcd_test_variant_*
 * 标定屏幕 offset/mirror/invert/颜色序。 */
#define PCM_LCD_TEST_VARIANTS 1
#if PCM_LCD_TEST_VARIANTS
#include "display/lcd.h"
#endif

static const char *TAG = "link_usb";

#define USB_RX_RING_SIZE   4096
#define USB_TX_RING_SIZE   2048
#define USB_LINE_MAX       1024
#define USB_TASK_STACK     4096
#define USB_TASK_PRIO      5
#define HELLO_PERIOD_US    (5 * 1000 * 1000)

static bool s_cfg_received;

static void usb_write(const char *s)
{
    if (s == NULL) {
        return;
    }
    size_t len = strlen(s);
    if (len == 0) {
        return;
    }
    int sent = usb_serial_jtag_write_bytes(s, len, pdMS_TO_TICKS(100));
    if (sent < (int)len) {
        ESP_LOGD(TAG, "usb tx short write %d/%u", sent, (unsigned)len);
    }
}

static void usb_write_line(const char *s)
{
    char buf[192];
    int n = snprintf(buf, sizeof(buf), "%s\n", s);
    if (n > 0 && n < (int)sizeof(buf)) {
        usb_write(buf);
    }
}

static void send_hello(void)
{
    char buf[64];
    snprintf(buf, sizeof(buf), "{\"t\":\"hello\",\"need_cfg\":%d}", cfg_has_wifi() ? 0 : 1);
    usb_write_line(buf);
    ESP_LOGI(TAG, "hello sent (need_cfg=%d)", cfg_has_wifi() ? 0 : 1);
}

/* ---------------- 串口命令行 ---------------- */

static void cmd_print_cfg(void)
{
    const char *token = cfg_api_token();
    char token_head[5] = {0};
    if (token[0] != '\0') {
        strlcpy(token_head, token, sizeof(token_head));   /* 只打前 4 位 */
    }
    ESP_LOGI(TAG, "cfg: ssid=\"%s\" pass=%s url=\"%s\" token=\"%s****\" bright=%u page=%u",
             cfg_wifi_ssid(),
             cfg_wifi_pass()[0] ? "<set>" : "<empty>",
             cfg_api_url(),
             token_head,
             (unsigned)cfg_brightness(),
             (unsigned)cfg_page());
}

static void cmd_help(void)
{
    ESP_LOGI(TAG, "commands: cfg | wifi <ssid> <pass> | url <url> | token <tok> | lcd [0-3] | reboot | help");
}

static void handle_command(char *line)
{
    char *save = NULL;
    const char *cmd = strtok_r(line, " \t", &save);
    if (cmd == NULL) {
        return;
    }

    if (strcmp(cmd, "help") == 0) {
        cmd_help();
    } else if (strcmp(cmd, "cfg") == 0) {
        cmd_print_cfg();
    } else if (strcmp(cmd, "wifi") == 0) {
        const char *ssid = strtok_r(NULL, " \t", &save);
        const char *pass = strtok_r(NULL, " \t", &save);
        if (ssid == NULL) {
            ESP_LOGW(TAG, "usage: wifi <ssid> <pass>");
            return;
        }
        esp_err_t err = cfg_set_wifi(ssid, pass ? pass : "");
        ESP_LOGI(TAG, "wifi saved: %s（%s）", esp_err_to_name(err), ssid);
    } else if (strcmp(cmd, "url") == 0) {
        const char *url = strtok_r(NULL, " \t", &save);
        if (url == NULL) {
            ESP_LOGW(TAG, "usage: url <http://ip:8787/api/stats>");
            return;
        }
        ESP_LOGI(TAG, "url saved: %s", esp_err_to_name(cfg_set_api(url, NULL)));
    } else if (strcmp(cmd, "token") == 0) {
        const char *token = strtok_r(NULL, " \t", &save);
        if (token == NULL) {
            ESP_LOGW(TAG, "usage: token <bearer-token>");
            return;
        }
        ESP_LOGI(TAG, "token saved: %s", esp_err_to_name(cfg_set_api(NULL, token)));
    } else if (strcmp(cmd, "reboot") == 0) {
        ESP_LOGI(TAG, "rebooting...");
        vTaskDelay(pdMS_TO_TICKS(100));
        esp_restart();
    } else if (strcmp(cmd, "lcd") == 0) {
#if PCM_LCD_TEST_VARIANTS
        const char *arg = strtok_r(NULL, " \t", &save);
        if (arg == NULL) {
            lcd_test_variant_next();
            ESP_LOGI(TAG, "lcd test variant -> %d", lcd_test_variant_get());
        } else {
            int v = atoi(arg);
            lcd_test_variant_set(v);
            ESP_LOGI(TAG, "lcd test variant set to %d", lcd_test_variant_get());
        }
#else
        ESP_LOGW(TAG, "lcd variant switching not wired yet (lcd.h 还没有 lcd_test_variant_*)");
#endif
    } else {
        ESP_LOGW(TAG, "unknown command: %s (try help)", cmd);
    }
}

/* ---------------- 配置下发（上位机 → 设备） ---------------- */

static void handle_cfg_message(const char *json, size_t len)
{
    cJSON *root = cJSON_ParseWithLength(json, len);
    if (root == NULL) {
        ESP_LOGW(TAG, "cfg message is not valid JSON");
        return;
    }

    const cJSON *ssid = cJSON_GetObjectItemCaseSensitive(root, "wifi_ssid");
    const cJSON *pass = cJSON_GetObjectItemCaseSensitive(root, "wifi_pass");
    const cJSON *url  = cJSON_GetObjectItemCaseSensitive(root, "api_url");
    const cJSON *tok  = cJSON_GetObjectItemCaseSensitive(root, "api_token");

    if (cJSON_IsString(ssid) && ssid->valuestring) {
        esp_err_t err = cfg_set_wifi(ssid->valuestring,
                                     (cJSON_IsString(pass) && pass->valuestring) ? pass->valuestring : "");
        ESP_LOGI(TAG, "cfg from PC: ssid=\"%s\" -> %s", ssid->valuestring, esp_err_to_name(err));
    } else {
        ESP_LOGW(TAG, "cfg message without wifi_ssid, ignored");
        cJSON_Delete(root);
        return;
    }
    if (cJSON_IsString(url) || cJSON_IsString(tok)) {
        esp_err_t err = cfg_set_api((cJSON_IsString(url) && url->valuestring) ? url->valuestring : NULL,
                                    (cJSON_IsString(tok) && tok->valuestring) ? tok->valuestring : NULL);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "cfg_set_api failed: %s", esp_err_to_name(err));
        }
    }
    cJSON_Delete(root);

    s_cfg_received = true;
    usb_write_line("{\"t\":\"cfg_ok\"}");
    ESP_LOGI(TAG, "config applied, rebooting to start Wi-Fi link");
    vTaskDelay(pdMS_TO_TICKS(300));
    esp_restart();
}

/* ---------------- 行处理 ---------------- */

static void handle_line(const char *line, size_t len)
{
    if (len == 0) {
        return;
    }

    if (line[0] == '{') {
        /* 先看是不是控制消息（t=cfg / hello），不是就当性能帧解 */
        cJSON *root = cJSON_ParseWithLength(line, len);
        if (root != NULL) {
            const cJSON *t = cJSON_GetObjectItemCaseSensitive(root, "t");
            bool is_cfg = cJSON_IsString(t) && strcmp(t->valuestring, "cfg") == 0;
            bool is_hello = cJSON_IsString(t) && strcmp(t->valuestring, "hello") == 0;
            cJSON_Delete(root);
            if (is_cfg) {
                handle_cfg_message(line, len);
                return;
            }
            if (is_hello) {
                return;
            }
        }
        stats_t snap;
        if (link_parse_frame(line, len, &snap)) {
            link_on_frame(&snap, LINK_ONLINE_USB);
        } else {
            ESP_LOGD(TAG, "bad frame dropped (%.32s...)", line);
        }
        return;
    }

    /* 非 JSON 行 = 串口命令。复制一份，strtok 会改内容。 */
    static char cmd_buf[USB_LINE_MAX];
    strlcpy(cmd_buf, line, sizeof(cmd_buf));
    handle_command(cmd_buf);
}

/* ---------------- 任务 ---------------- */

static void usb_rx_task(void *arg)
{
    uint8_t buf[128];
    static char line[USB_LINE_MAX];
    size_t line_len = 0;
    bool   skipping = false;
    bool   hello_sent_once = false;
    int64_t next_hello = esp_timer_get_time() + 2000 * 1000;   /* 上电 2 秒后先握一次手 */

    ESP_LOGI(TAG, "usb rx task started");

    while (true) {
        int n = usb_serial_jtag_read_bytes(buf, sizeof(buf), pdMS_TO_TICKS(50));
        for (int i = 0; i < n; i++) {
            char c = (char)buf[i];
            if (c == '\n') {
                if (!skipping && line_len > 0) {
                    line[line_len] = '\0';
                    handle_line(line, line_len);
                }
                line_len = 0;
                skipping = false;
            } else if (c == '\r') {
                continue;
            } else if (!skipping) {
                if (line_len < sizeof(line) - 1) {
                    line[line_len++] = c;
                } else {
                    skipping = true;     /* 超长行整行丢掉，等下一个 \n */
                    line_len = 0;
                    ESP_LOGW(TAG, "line too long, dropped");
                }
            }
        }

        /* 握手：上电先发一次（让上位机知道设备活着）；
         * 还没配 Wi-Fi 时每 5 秒重复，等上位机把配置推下来。 */
        if (esp_timer_get_time() >= next_hello) {
            if (!hello_sent_once || !cfg_has_wifi()) {
                if (!s_cfg_received) {
                    send_hello();
                    hello_sent_once = true;
                }
            }
            next_hello = esp_timer_get_time() + HELLO_PERIOD_US;
        }
    }
}

esp_err_t link_usb_start(void)
{
    usb_serial_jtag_driver_config_t cfg = {
        .tx_buffer_size = USB_TX_RING_SIZE,
        .rx_buffer_size = USB_RX_RING_SIZE,
    };
    esp_err_t err = usb_serial_jtag_driver_install(&cfg);
    if (err == ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "usb serial jtag driver already installed, reuse it");
        err = ESP_OK;
    } else if (err != ESP_OK) {
        ESP_LOGE(TAG, "usb serial jtag driver install failed: %s", esp_err_to_name(err));
        return err;
    }

    /* console 也走驱动，避免日志和 JSON 行在 TX FIFO 里交错。
     * console=USB_SERIAL_JTAG 时 VFS 已由系统自动注册，这里再注册一次只会返回
     * ESP_ERR_INVALID_STATE，忽略即可；console 配到别处时这一步让 VFS 也能用起来。 */
    (void)usb_serial_jtag_vfs_register();
    usb_serial_jtag_vfs_use_driver();

    BaseType_t ok = xTaskCreate(usb_rx_task, "link_usb", USB_TASK_STACK, NULL, USB_TASK_PRIO, NULL);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "usb rx task create failed");
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}
