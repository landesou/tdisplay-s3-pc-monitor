#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "stats.h"

/**
 * UI 层（LVGL 9）。实现者：固件显示同学（firmware/main/ui/ui.c + ui_pages.c）
 *
 * 横屏 320x170，**三页**（KEY 短按循环）：
 *   P0 总览：顶栏(主机名/时间/链路灯) + CPU/MEM/GPU/NET 四张卡 + 60 点曲线
 *   P1 明细：24 线程迷你柱状图 + 温度/频率/显存/功率/磁盘
 *   P2 DSH ：DeepSeek Harness 今日/近 7 天 token 消耗、≈费用（界面用 `~CNY`）、请求数、进程数与内存
 *
 * 新鲜度纪律：st == LINK_STALE 或 age_s > 2 时，数值必须降饱和（灰字）并显式标 STALE/OFFLINE，
 * 绝不能让旧数据显示成实时（这是 logicprobe 验证过的不变式）。
 * 注意：`dsh.valid == false`（帧里没带 DSH 段）与「数据过期」是两个正交状态，页面分别处理。
 */

void ui_init(void);

/** 有新帧或状态变化时调用。s 为 NULL 表示还没有任何数据。 */
void ui_update(const stats_t *s, link_state_t st, uint32_t age_s);

/** 切换到下一页。 */
void ui_next_page(void);

/**
 * 退出 bring-up 标定图模式：把屏幕交还 UI 并整屏重绘。
 * 为什么需要：测试图是绕过 LVGL 直接写面板的，LVGL 并不知道屏上已经变了；
 * 光把「测试模式」标志清掉不够，必须整屏 invalidate，否则只有变化的 label 重绘、
 * 其余区域还留着测试图。
 * 调用方（main.c 的 BOOT 长按退出分支）必须持 lvgl_lock()。
 */
void ui_exit_test_mode(void);

uint8_t ui_page(void);
