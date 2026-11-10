#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "esp_err.h"
#include "stats.h"

/* link 模块内部共享的接口（link.c / link_usb.c / link_http.c 三方共用） */

/** 解析一帧 JSON 文本（不含换行符）。成功返回 true 并填充 out。 */
bool link_parse_frame(const char *json, size_t len, stats_t *out);

/** 一帧解析成功后的统一入口：更新状态机 + 缓存快照。source 用 LINK_ONLINE_USB/WIFI。 */
void link_on_frame(const stats_t *snap, link_state_t source);

/** 显式链路丢失（USB 拔出 / WiFi 掉线）。 */
void link_on_link_lost(void);

esp_err_t link_usb_start(void);
esp_err_t link_http_start(void);
