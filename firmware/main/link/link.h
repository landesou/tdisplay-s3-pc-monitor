#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "stats.h"

/**
 * 链路层：USB CDC 与 WiFi HTTP 两条传输 + 新鲜度状态机。
 * 实现者：固件链路同学（firmware/main/link 下的 link.c / link_usb.c / link_http.c）
 *
 * 状态机必须严格按 docs/design/link-state-machine.model.json（已 logicprobe 验证）：
 *   INIT --boot_done--> NO_LINK
 *   NO_LINK/STALE --usb_frame--> ONLINE_USB ; NO_LINK/STALE --wifi_frame--> ONLINE_WIFI
 *   ONLINE_* --tick(age<2)--> 自己(age+1) ; --tick(age>=2)--> STALE(age=3)
 *   ONLINE_* --link_lost--> NO_LINK
 */

esp_err_t link_init(void);

/** 外设/UI 初始化完成时调用一次：INIT -> NO_LINK。 */
void link_boot_done(void);

/** 1 秒周期调用：age+1，必要时转 STALE。 */
void link_tick_1s(void);

/** 取最新一帧。返回 true 表示有数据可用（可能是旧数据，请结合 link_age_s 判断新鲜度）。 */
bool link_get_latest(stats_t *out);

/** 取当前帧号；UI 用它判断是否需要重绘（帧号没变就不用重画）。 */
uint32_t link_frame_seq(void);

/** 距上一帧的秒数（由状态机的 age 变量直接给出）。 */
uint32_t link_age_s(void);

/** 当前链路状态。 */
link_state_t link_state(void);

/** 进入深睡前调用（SLEEP 终态）。 */
void link_sleep(void);
