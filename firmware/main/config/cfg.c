#include <string.h>
#include "cfg.h"
#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

/* 占位实现（Lead 建的骨架）。由固件链路同学替换成真正的 NVS 读写。 */

static const char *TAG = "cfg";

static char    s_ssid[33] = "";
static char    s_pass[65] = "";
static char    s_url[128] = "";
static char    s_token[64] = "";
static uint8_t s_bright = 100;
static uint8_t s_page = 0;

esp_err_t cfg_init(void)
{
    ESP_LOGW(TAG, "cfg_init: stub, 尚未实现（先用默认值）");
    return ESP_OK;
}

const char *cfg_wifi_ssid(void) { return s_ssid; }
const char *cfg_wifi_pass(void) { return s_pass; }
const char *cfg_api_url(void)  { return s_url; }
const char *cfg_api_token(void){ return s_token; }

esp_err_t cfg_set_wifi(const char *ssid, const char *pass)
{
    if (!ssid) return ESP_ERR_INVALID_ARG;
    strlcpy(s_ssid, ssid, sizeof(s_ssid));
    if (pass) strlcpy(s_pass, pass, sizeof(s_pass));
    return ESP_OK;
}

esp_err_t cfg_set_api(const char *url, const char *token)
{
    if (url)   strlcpy(s_url, url, sizeof(s_url));
    if (token) strlcpy(s_token, token, sizeof(s_token));
    return ESP_OK;
}

uint8_t cfg_brightness(void) { return s_bright; }
void cfg_set_brightness(uint8_t pct) { s_bright = pct > 100 ? 100 : pct; }
uint8_t cfg_page(void) { return s_page; }
void cfg_set_page(uint8_t page) { s_page = page; }
bool cfg_has_wifi(void) { return s_ssid[0] != '\0'; }
