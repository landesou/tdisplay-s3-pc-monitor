#include <string.h>

#include "cfg.h"
#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

/* 设备侧配置持久化（NVS namespace "pcmon"）。
 * key：wifi_ssid / wifi_pass / api_url / api_token / brightness / page（契约 architecture.md §4）
 * 读不到 = 用默认值；写失败要返回错误并记日志，不能静默。 */

static const char *TAG = "cfg";

#define CFG_NS          "pcmon"
#define KEY_WIFI_SSID   "wifi_ssid"
#define KEY_WIFI_PASS   "wifi_pass"
#define KEY_API_URL     "api_url"
#define KEY_API_TOKEN   "api_token"
#define KEY_BRIGHTNESS  "brightness"
#define KEY_PAGE        "page"

static nvs_handle_t s_nvs;
static bool         s_ready;

static char    s_ssid[33];
static char    s_pass[65];
static char    s_url[160];
static char    s_token[80];
static uint8_t s_bright = 100;
static uint8_t s_page;

static void load_str(const char *key, char *dst, size_t dst_len, const char *def)
{
    size_t len = dst_len;
    esp_err_t err = nvs_get_str(s_nvs, key, dst, &len);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        strlcpy(dst, def, dst_len);
        return;
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "read %s failed: %s", key, esp_err_to_name(err));
        strlcpy(dst, def, dst_len);
        return;
    }
    ESP_LOGD(TAG, "loaded %s (%u bytes)", key, (unsigned)len);
}

esp_err_t cfg_init(void)
{
    memset(s_ssid, 0, sizeof(s_ssid));
    memset(s_pass, 0, sizeof(s_pass));
    memset(s_url, 0, sizeof(s_url));
    memset(s_token, 0, sizeof(s_token));
    s_bright = 100;
    s_page = 0;

    esp_err_t err = nvs_open(CFG_NS, NVS_READWRITE, &s_nvs);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open(%s) failed: %s", CFG_NS, esp_err_to_name(err));
        return err;
    }
    s_ready = true;

    load_str(KEY_WIFI_SSID, s_ssid, sizeof(s_ssid), "");
    load_str(KEY_WIFI_PASS, s_pass, sizeof(s_pass), "");
    load_str(KEY_API_URL, s_url, sizeof(s_url), "");
    load_str(KEY_API_TOKEN, s_token, sizeof(s_token), "");

    uint8_t value = 0;
    if (nvs_get_u8(s_nvs, KEY_BRIGHTNESS, &value) == ESP_OK && value <= 100) {
        s_bright = value;
    }
    value = 0;
    if (nvs_get_u8(s_nvs, KEY_PAGE, &value) == ESP_OK && value <= 1) {
        s_page = value;
    }

    ESP_LOGI(TAG, "config loaded: ssid=%s api_url=%s brightness=%u",
             s_ssid[0] ? "<set>" : "<empty>",
             s_url[0] ? s_url : "<empty>",
             (unsigned)s_bright);
    return ESP_OK;
}

const char *cfg_wifi_ssid(void) { return s_ssid; }
const char *cfg_wifi_pass(void) { return s_pass; }
const char *cfg_api_url(void)  { return s_url; }
const char *cfg_api_token(void){ return s_token; }

esp_err_t cfg_set_wifi(const char *ssid, const char *pass)
{
    if (ssid == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_ready) {
        ESP_LOGE(TAG, "cfg not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    strlcpy(s_ssid, ssid, sizeof(s_ssid));
    strlcpy(s_pass, pass ? pass : "", sizeof(s_pass));

    esp_err_t err = nvs_set_str(s_nvs, KEY_WIFI_SSID, s_ssid);
    if (err == ESP_OK) {
        err = nvs_set_str(s_nvs, KEY_WIFI_PASS, s_pass);
    }
    if (err == ESP_OK) {
        err = nvs_commit(s_nvs);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "save wifi failed: %s", esp_err_to_name(err));
    }
    return err;
}

esp_err_t cfg_set_api(const char *url, const char *token)
{
    if (!s_ready) {
        ESP_LOGE(TAG, "cfg not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    esp_err_t err = ESP_OK;
    if (url != NULL) {
        strlcpy(s_url, url, sizeof(s_url));
        err = nvs_set_str(s_nvs, KEY_API_URL, s_url);
    }
    if (err == ESP_OK && token != NULL) {
        strlcpy(s_token, token, sizeof(s_token));
        err = nvs_set_str(s_nvs, KEY_API_TOKEN, s_token);
    }
    if (err == ESP_OK) {
        err = nvs_commit(s_nvs);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "save api config failed: %s", esp_err_to_name(err));
    }
    return err;
}

uint8_t cfg_brightness(void) { return s_bright; }

void cfg_set_brightness(uint8_t pct)
{
    s_bright = (pct > 100) ? 100 : pct;
    if (!s_ready) {
        return;
    }
    esp_err_t err = nvs_set_u8(s_nvs, KEY_BRIGHTNESS, s_bright);
    if (err == ESP_OK) {
        err = nvs_commit(s_nvs);
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "save brightness failed: %s", esp_err_to_name(err));
    }
}

uint8_t cfg_page(void) { return s_page; }

void cfg_set_page(uint8_t page)
{
    s_page = (page > 1) ? 1 : page;
    if (!s_ready) {
        return;
    }
    esp_err_t err = nvs_set_u8(s_nvs, KEY_PAGE, s_page);
    if (err == ESP_OK) {
        err = nvs_commit(s_nvs);
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "save page failed: %s", esp_err_to_name(err));
    }
}

bool cfg_has_wifi(void)
{
    return s_ssid[0] != '\0';
}
