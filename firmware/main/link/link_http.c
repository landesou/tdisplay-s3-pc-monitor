#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"

#include "config/cfg.h"
#include "link.h"
#include "link_internal.h"

/* WiFi STA + HTTP 轮询：GET api_url（Bearer Token），1 秒一次。
 *
 * 设计要点：
 *  - 只在上位机配好 Wi-Fi 凭据后才启动（没配就整条链路不启动，只打一行日志）。
 *  - 连续 3 次轮询失败 → link_on_link_lost() + 重连 Wi-Fi（2s→4s→8s→15s 退避）。
 *  - 状态机里 link_lost 只对「WiFi 在线」有意义：USB 在线时 WiFi 掉线不能把状态打回 NO_LINK，
 *    所以这里调用前判断当前状态。
 */

static const char *TAG = "link_http";

#define WIFI_UP_BIT          BIT0
#define HTTP_TIMEOUT_MS      2000
#define HTTP_BODY_MAX        1024
#define POLL_PERIOD_MS       1000
#define FAIL_THRESHOLD       3
#define BACKOFF_MIN_S        2
#define BACKOFF_MAX_S        15

static EventGroupHandle_t s_events;
static bool s_started;

static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        ESP_LOGI(TAG, "wifi sta started, connecting...");
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *ev = (const wifi_event_sta_disconnected_t *)data;
        xEventGroupClearBits(s_events, WIFI_UP_BIT);
        ESP_LOGW(TAG, "wifi disconnected (reason=%d)", ev ? ev->reason : -1);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *ev = (const ip_event_got_ip_t *)data;
        xEventGroupSetBits(s_events, WIFI_UP_BIT);
        ESP_LOGI(TAG, "got ip: " IPSTR, IP2STR(&ev->ip_info.ip));
    }
}

/* 一次 HTTP GET。成功且 JSON 合法返回 true。 */
static bool http_poll_once(stats_t *out)
{
    const char *url = cfg_api_url();
    if (url[0] == '\0') {
        return false;
    }

    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = HTTP_TIMEOUT_MS,
        .disable_auto_redirect = true,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == NULL) {
        ESP_LOGW(TAG, "http client init failed");
        return false;
    }

    const char *token = cfg_api_token();
    if (token[0] != '\0') {
        char auth[96];
        snprintf(auth, sizeof(auth), "Bearer %s", token);
        esp_http_client_set_header(client, "Authorization", auth);
    }

    bool ok = false;
    esp_err_t err = esp_http_client_open(client, 0);
    if (err == ESP_OK) {
        esp_http_client_fetch_headers(client);
        int status = esp_http_client_get_status_code(client);
        if (status == 200) {
            char body[HTTP_BODY_MAX];
            int total = 0;
            while (total < (int)sizeof(body) - 1) {
                int n = esp_http_client_read(client, body + total, sizeof(body) - 1 - total);
                if (n <= 0) {
                    break;
                }
                total += n;
            }
            if (total > 0) {
                body[total] = '\0';
                ok = link_parse_frame(body, (size_t)total, out);
                if (!ok) {
                    ESP_LOGD(TAG, "http body is not a valid stats frame (%.32s...)", body);
                }
            } else {
                ESP_LOGD(TAG, "http body empty");
            }
        } else if (status == 401) {
            ESP_LOGW(TAG, "http 401: api_token 与上位机 config.json 不一致");
        } else {
            ESP_LOGD(TAG, "http status %d", status);
        }
        esp_http_client_close(client);
    } else {
        ESP_LOGD(TAG, "http open failed: %s", esp_err_to_name(err));
    }

    esp_http_client_cleanup(client);
    return ok;
}

static void http_poll_task(void *arg)
{
    (void)arg;
    int fails = 0;
    int backoff_s = BACKOFF_MIN_S;

    ESP_LOGI(TAG, "poll task started: %s", cfg_api_url());

    while (true) {
        bool up = (xEventGroupGetBits(s_events) & WIFI_UP_BIT) != 0;
        if (!up) {
            ESP_LOGD(TAG, "wifi down, retry in %ds", backoff_s);
            vTaskDelay(pdMS_TO_TICKS(backoff_s * 1000));
            backoff_s = (backoff_s * 2 > BACKOFF_MAX_S) ? BACKOFF_MAX_S : backoff_s * 2;
            esp_wifi_connect();   /* 已在连接中会返回错误，忽略即可 */
            continue;
        }

        backoff_s = BACKOFF_MIN_S;

        stats_t snap;
        if (http_poll_once(&snap)) {
            fails = 0;
            link_on_frame(&snap, LINK_ONLINE_WIFI);
        } else if (++fails >= FAIL_THRESHOLD) {
            fails = 0;
            link_state_t st = link_state();
            if (st == LINK_ONLINE_WIFI || st == LINK_STALE) {
                link_on_link_lost();
            }
            ESP_LOGW(TAG, "%d consecutive poll failures, reconnecting wifi", FAIL_THRESHOLD);
            esp_wifi_disconnect();   /* 触发 STA_DISCONNECTED 事件 → 清 bit → 走退避重连 */
        }

        vTaskDelay(pdMS_TO_TICKS(POLL_PERIOD_MS));
    }
}

esp_err_t link_http_start(void)
{
    if (s_started) {
        return ESP_OK;
    }
    if (!cfg_has_wifi()) {
        ESP_LOGI(TAG, "wifi 未配置，HTTP 链路不启动（USB 链路可用；配好凭据后重启生效）");
        return ESP_OK;
    }
    if (cfg_api_url()[0] == '\0') {
        ESP_LOGW(TAG, "wifi 已配置但 api_url 为空，HTTP 链路不启动");
        return ESP_OK;
    }

    s_events = xEventGroupCreate();
    if (s_events == NULL) {
        ESP_LOGE(TAG, "event group create failed");
        return ESP_ERR_NO_MEM;
    }

    esp_err_t err = esp_netif_init();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "netif init failed: %s", esp_err_to_name(err));
        return err;
    }
    err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "event loop create failed: %s", esp_err_to_name(err));
        return err;
    }
    if (esp_netif_create_default_wifi_sta() == NULL) {
        ESP_LOGE(TAG, "create default wifi sta failed");
        return ESP_FAIL;
    }

    wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    err = esp_wifi_init(&init_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "wifi init failed: %s", esp_err_to_name(err));
        return err;
    }

    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL);

    wifi_config_t wifi_cfg = {0};
    strlcpy((char *)wifi_cfg.sta.ssid, cfg_wifi_ssid(), sizeof(wifi_cfg.sta.ssid));
    strlcpy((char *)wifi_cfg.sta.password, cfg_wifi_pass(), sizeof(wifi_cfg.sta.password));
    wifi_cfg.sta.threshold.authmode = WIFI_AUTH_OPEN;   /* 开放/加密都接受 */

    err = esp_wifi_set_mode(WIFI_MODE_STA);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "wifi set mode failed: %s", esp_err_to_name(err));
        return err;
    }
    err = esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "wifi set config failed: %s", esp_err_to_name(err));
        return err;
    }
    err = esp_wifi_start();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "wifi start failed: %s", esp_err_to_name(err));
        return err;
    }

    BaseType_t ok = xTaskCreate(http_poll_task, "link_http", 6144, NULL, 4, NULL);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "poll task create failed");
        return ESP_ERR_NO_MEM;
    }

    s_started = true;
    ESP_LOGI(TAG, "wifi link started, ssid=\"%s\"", cfg_wifi_ssid());
    return ESP_OK;
}
