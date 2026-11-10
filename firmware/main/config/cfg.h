#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

/**
 * 设备侧配置（NVS）。实现者：固件链路同学（firmware/main/config/cfg.c）
 *
 * key：wifi_ssid / wifi_pass / api_url / api_token / brightness / page
 * 首次配置两种方式（见 docs/design/architecture.md §4）：
 *   1) 串口命令行：wifi/url/token/cfg/reboot
 *   2) USB 下发：上位机读到 {"t":"hello","need_cfg":1} 后回 {"t":"cfg",...}
 */

esp_err_t cfg_init(void);

const char *cfg_wifi_ssid(void);
const char *cfg_wifi_pass(void);
const char *cfg_api_url(void);
const char *cfg_api_token(void);

esp_err_t cfg_set_wifi(const char *ssid, const char *pass);
esp_err_t cfg_set_api(const char *url, const char *token);

uint8_t cfg_brightness(void);
void    cfg_set_brightness(uint8_t pct);

uint8_t cfg_page(void);
void    cfg_set_page(uint8_t page);

/** Wi-Fi 凭据是否已配置（空 SSID 视作未配置）。 */
bool cfg_has_wifi(void);
