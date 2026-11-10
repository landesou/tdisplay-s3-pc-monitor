#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "stats.h"

/**
 * UI 层（LVGL 9）。实现者：固件显示同学（firmware/main/ui/ui.c + ui_pages.c）
 *
 * 横屏 320x170，两页：
 *   P0 总览：顶栏(主机名/时间/链路灯) + CPU/MEM/GPU/NET 四张卡 + 60 点曲线
 *   P1 明细：24 线程迷你柱状图 + 温度/频率/显存/功率/磁盘
 *
 * 新鲜度纪律：st == LINK_STALE 或 age_s > 2 时，数值必须降饱和（灰字）并显式标 STALE/OFFLINE，
 * 绝不能让旧数据显示成实时（这是 logicprobe 验证过的不变式）。
 */

void ui_init(void);

/** 有新帧或状态变化时调用。s 为 NULL 表示还没有任何数据。 */
void ui_update(const stats_t *s, link_state_t st, uint32_t age_s);

/** 切换到下一页。 */
void ui_next_page(void);

uint8_t ui_page(void);
