/**
 * UI 主文件：320x170 横屏**三页**界面（LVGL 9）。
 *
 *   P0 总览：顶栏(主机名/PC 时间/链路状态) + GPU/CPU/MEM/NET 四卡 + 60 点曲线
 *   P1 明细：见 ui_pages.c（24 线程柱 + 磁盘/显存/温度/时长/帧率）
 *   P2 DSH ：见 ui_pages.c（DeepSeek Harness 今日 tokens/费用/进程 + 7 天汇总）
 *   KEY 短按 0→1→2→0 循环；顶栏右侧显示页码 x/3。
 *
 * 两条硬约束：
 *   1. **对象只在 ui_init() 里建一次**。ui_update() 只做
 *      lv_label_set_text / lv_bar_set_value / lv_chart_set_next_value / 改颜色，
 *      禁止 create/delete —— 否则 1 Hz 刷新会慢慢啃掉 LVGL 堆。
 *   2. **新鲜度纪律**（logicprobe 验证过的不变式）：数据不新鲜时
 *      （st != ONLINE_* 或 age_s > 2，或根本没有数据）所有数值必须降饱和成
 *      UI_COL_DIM 灰、顶栏明确显示 STALE/OFFLINE，绝不能把旧帧画得像实时。
 *
 * 顶栏放在页面容器之外，三页共享 —— 这样翻到明细/DSH 页也能看到 STALE 提示。
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "esp_log.h"
#include "lvgl.h"

#include "board.h"
#include "cfg.h"
#include "lcd_internal.h"
#include "lvgl_port.h"
#include "ui.h"
#include "ui_internal.h"

static const char *TAG = "ui";

/* ---- 顶栏 ---- */
static lv_obj_t *s_lbl_host;
static lv_obj_t *s_lbl_clock;
static lv_obj_t *s_lbl_link;
static lv_obj_t *s_dot_link;
static lv_obj_t *s_lbl_pageno; /* "x/3" */

/* ---- 页面容器 ---- */
static lv_obj_t *s_page0;
static lv_obj_t *s_page1;
static lv_obj_t *s_page2;
static lv_obj_t *s_offline;

/* ---- 总览页数值对象 ---- */
static lv_obj_t *s_gpu_val;
static lv_obj_t *s_gpu_bar;
static lv_obj_t *s_gpu_sub;
static lv_obj_t *s_cpu_val;
static lv_obj_t *s_cpu_bar;
static lv_obj_t *s_cpu_sub;
static lv_obj_t *s_mem_val;
static lv_obj_t *s_mem_sub;
static lv_obj_t *s_net_val;
static lv_obj_t *s_chart;
static lv_chart_series_t *s_ser_cpu;
static lv_chart_series_t *s_ser_gpu;

/* ---- 状态 ---- */
static uint8_t s_page;
static bool s_ready;

/* 更新缓存：只有「新帧 / 新鲜度变化」才重写数值，避免无意义重绘 */
static uint32_t s_last_seq;
static bool     s_have_seq;
static uint32_t s_last_chart_seq;
static uint32_t s_last_chart_time;
static bool     s_chart_have;
static bool     s_last_stale = false; /* 初值特意与「无数据时 stale=true」相反，保证首帧必刷 */
static bool     s_last_have;
/* 从 bring-up 测试图模式回来时置位：下一次 ui_update 必须走完整刷新，
 * 不能被下面「值没变就跳过」的缓存优化挡掉。 */
static bool     s_force_full_update;

/* 最近一帧快照：翻页时用它**立刻**刷新新页 —— 1 Hz 数据流下否则会有最多
 * 1 秒的占位符；也让「按页分派」不用为了预热而刷隐藏页。 */
static stats_t  s_last_stats;
static bool     s_last_stats_ok;
/* 有没有收到过数据：决定 OFFLINE 覆盖层在哪些页显示（P2 自己显 DSH N/A） */
static bool     s_have_data;

/* =====================================================================
 * 小工具
 * ===================================================================== */

static void set_label_color(lv_obj_t *lbl, uint32_t fresh_color, bool stale)
{
    lv_obj_set_style_text_color(lbl, lv_color_hex(stale ? UI_COL_DIM : fresh_color), LV_PART_MAIN);
}

static void set_bar_color(lv_obj_t *bar, uint32_t fresh_color, bool stale)
{
    lv_obj_set_style_bg_color(bar, lv_color_hex(stale ? UI_COL_DIM : fresh_color), LV_PART_INDICATOR);
}

static void apply_page(void)
{
    lv_obj_t *pages[3] = {s_page0, s_page1, s_page2};
    for (int i = 0; i < 3; i++) {
        if (i == s_page) {
            lv_obj_remove_flag(pages[i], LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(pages[i], LV_OBJ_FLAG_HIDDEN);
        }
    }

    /* 顶栏页码 x/3 */
    char buf[8];
    snprintf(buf, sizeof(buf), "%u/3", (unsigned)(s_page + 1u));
    lv_label_set_text(s_lbl_pageno, buf);

    /* OFFLINE 覆盖层只对 P0/P1 有意义：P2 没数据时自己显示 DSH N/A，
     * 两个大灰字叠在屏幕正中会糊成一团。 */
    if (s_page == 2 || s_have_data) {
        lv_obj_add_flag(s_offline, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_remove_flag(s_offline, LV_OBJ_FLAG_HIDDEN);
    }
}

/* =====================================================================
 * 顶栏
 * ===================================================================== */

static void top_bar_create(lv_obj_t *scr)
{
    lv_obj_t *bar = lv_obj_create(scr);
    lv_obj_remove_style_all(bar);
    lv_obj_set_pos(bar, 0, 0);
    lv_obj_set_size(bar, BOARD_LCD_H_RES, 20);
    lv_obj_remove_flag(bar, LV_OBJ_FLAG_SCROLLABLE);

    /* 顶栏与内容之间的 1px 分隔线 */
    lv_obj_t *line = lv_obj_create(bar);
    lv_obj_remove_style_all(line);
    lv_obj_set_pos(line, 0, 19);
    lv_obj_set_size(line, BOARD_LCD_H_RES, 1);
    lv_obj_set_style_bg_color(line, lv_color_hex(UI_COL_TRACK), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(line, LV_OPA_COVER, LV_PART_MAIN);

    /* 左：主机名（最长 15 字符，超了打点） */
    s_lbl_host = ui_tag(bar, &lv_font_montserrat_14, UI_COL_MUTED, 6, 2, "--");
    lv_obj_set_width(s_lbl_host, 96);
    lv_label_set_long_mode(s_lbl_host, LV_LABEL_LONG_DOT);

    /* 中：PC 时间 HH:MM:SS（Montserrat 20） */
    s_lbl_clock = ui_tag(bar, &lv_font_montserrat_20, UI_COL_TEXT, 0, 0, "--:--:--");
    lv_obj_align(s_lbl_clock, LV_ALIGN_TOP_MID, 0, 0);

    /* 右：状态点 + 状态/秒数 */
    s_dot_link = lv_obj_create(bar);
    lv_obj_remove_style_all(s_dot_link);
    lv_obj_set_pos(s_dot_link, 250, 6);
    lv_obj_set_size(s_dot_link, 8, 8);
    lv_obj_remove_flag(s_dot_link, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_radius(s_dot_link, LV_RADIUS_CIRCLE, LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_dot_link, lv_color_hex(UI_COL_LINK_NONE), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_dot_link, LV_OPA_COVER, LV_PART_MAIN);

    s_lbl_link = ui_tag(bar, &lv_font_montserrat_14, UI_COL_MUTED, 262, 2, "NO LINK");
    lv_obj_set_width(s_lbl_link, 56);

    /* 页码 x/3：夹在时钟（TOP_MID 居中，约 119..201）和状态点（x=250）之间 */
    s_lbl_pageno = ui_tag(bar, &lv_font_montserrat_14, UI_COL_MUTED, 210, 3, "1/3");
}

static void status_update(link_state_t st, uint32_t age_s, bool stale)
{
    uint32_t dot;
    char buf[20];

    if (!stale && st == LINK_ONLINE_USB) {
        dot = UI_COL_LINK_USB;
        snprintf(buf, sizeof(buf), "USB %us", (unsigned)age_s);
    } else if (!stale && st == LINK_ONLINE_WIFI) {
        dot = UI_COL_LINK_WIFI;
        snprintf(buf, sizeof(buf), "WIFI %us", (unsigned)age_s);
    } else if (stale && (st == LINK_STALE || st == LINK_ONLINE_USB || st == LINK_ONLINE_WIFI)) {
        /* 有数据但过期：点变黄、文字显式写 STALE —— 这是不变式要求 */
        dot = UI_COL_LINK_STALE;
        snprintf(buf, sizeof(buf), "STALE %us", (unsigned)age_s);
    } else if (st == LINK_SLEEP) {
        dot = UI_COL_LINK_NONE;
        snprintf(buf, sizeof(buf), "SLEEP");
    } else {
        dot = UI_COL_LINK_NONE;
        snprintf(buf, sizeof(buf), "NO LINK");
    }

    lv_obj_set_style_bg_color(s_dot_link, lv_color_hex(dot), LV_PART_MAIN);
    lv_label_set_text(s_lbl_link, buf);
}

static void clock_update(uint32_t pc_time, bool stale)
{
    char buf[12];
    if (pc_time == 0) {
        lv_label_set_text(s_lbl_clock, "--:--:--");
    } else {
        time_t t = (time_t)pc_time;
        struct tm tm_pc;
        if (localtime_r(&t, &tm_pc) == NULL) {
            lv_label_set_text(s_lbl_clock, "--:--:--");
        } else {
            snprintf(buf, sizeof(buf), "%02d:%02d:%02d",
                     tm_pc.tm_hour, tm_pc.tm_min, tm_pc.tm_sec);
            lv_label_set_text(s_lbl_clock, buf);
        }
    }
    /* 时钟也是「数值」：数据旧了照样降饱和，不给人「这秒还在走」的错觉 */
    lv_obj_set_style_text_color(s_lbl_clock, lv_color_hex(stale ? UI_COL_DIM : UI_COL_TEXT), LV_PART_MAIN);
}

/* =====================================================================
 * 页面 0：总览
 * ===================================================================== */

static void page0_create(lv_obj_t *scr)
{
    s_page0 = lv_obj_create(scr);
    lv_obj_remove_style_all(s_page0);
    lv_obj_set_pos(s_page0, 0, 20);
    lv_obj_set_size(s_page0, BOARD_LCD_H_RES, BOARD_LCD_V_RES - 20);
    lv_obj_remove_flag(s_page0, LV_OBJ_FLAG_SCROLLABLE);

    /* 左上 GPU 卡 150x60 */
    lv_obj_t *gpu = ui_card(s_page0, 4, 2, 150, 60);
    ui_tag(gpu, &lv_font_montserrat_14, UI_COL_GPU, 8, 4, "GPU");
    s_gpu_val = ui_label(gpu, &lv_font_montserrat_28, UI_COL_GPU, 0, 0);
    lv_obj_align(s_gpu_val, LV_ALIGN_TOP_RIGHT, -8, 0);
    lv_label_set_text(s_gpu_val, "--");
    s_gpu_bar = ui_bar(gpu, 8, 34, 134, 6, UI_COL_GPU);
    s_gpu_sub = ui_tag(gpu, &lv_font_montserrat_14, UI_COL_MUTED, 8, 42, "--°C --W");

    /* 左下 CPU 卡 150x84 */
    lv_obj_t *cpu = ui_card(s_page0, 4, 64, 150, 84);
    ui_tag(cpu, &lv_font_montserrat_14, UI_COL_CPU, 8, 4, "CPU");
    s_cpu_val = ui_label(cpu, &lv_font_montserrat_28, UI_COL_CPU, 0, 0);
    lv_obj_align(s_cpu_val, LV_ALIGN_TOP_RIGHT, -8, 0);
    lv_label_set_text(s_cpu_val, "--");
    s_cpu_bar = ui_bar(cpu, 8, 40, 134, 8, UI_COL_CPU);
    s_cpu_sub = ui_tag(cpu, &lv_font_montserrat_14, UI_COL_MUTED, 8, 54, "--MHz");

    /* 右上 MEM 卡 160x42：百分比用 Montserrat 20（Lead 要求压缩后数字不能变小） */
    lv_obj_t *mem = ui_card(s_page0, 156, 2, 160, 42);
    ui_tag(mem, &lv_font_montserrat_14, UI_COL_MEM, 8, 2, "MEM");
    s_mem_val = ui_label(mem, &lv_font_montserrat_20, UI_COL_MEM, 0, 0);
    lv_obj_align(s_mem_val, LV_ALIGN_TOP_RIGHT, -8, 0);
    lv_label_set_text(s_mem_val, "--%");
    s_mem_sub = ui_tag(mem, &lv_font_montserrat_14, UI_COL_MUTED, 8, 22, "--/-- MB");

    /* 右中 NET 卡 160x34（Montserrat 没有 ↓↑ 字形，用 ASCII DN/UP） */
    lv_obj_t *net = ui_card(s_page0, 156, 46, 160, 34);
    ui_tag(net, &lv_font_montserrat_14, UI_COL_NET, 8, 2, "NET");
    s_net_val = ui_tag(net, &lv_font_montserrat_14, UI_COL_NET, 8, 16, "DN --  UP -- MB/s");

    /* 右下 60 点曲线图 160x66（CPU 青 + GPU 紫） */
    s_chart = lv_chart_create(s_page0);
    lv_obj_set_pos(s_chart, 156, 82);
    lv_obj_set_size(s_chart, 160, 66);
    lv_obj_remove_flag(s_chart, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(s_chart, lv_color_hex(UI_COL_CARD), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_chart, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(s_chart, 4, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_chart, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_chart, 2, LV_PART_MAIN);
    lv_obj_set_style_line_width(s_chart, 1, LV_PART_ITEMS);
    lv_obj_set_style_size(s_chart, 0, 0, LV_PART_INDICATOR); /* 不要数据点圆点，只留细线 */
    lv_chart_set_type(s_chart, LV_CHART_TYPE_LINE);
    lv_chart_set_point_count(s_chart, 60);
    lv_chart_set_update_mode(s_chart, LV_CHART_UPDATE_MODE_SHIFT);
    lv_chart_set_range(s_chart, LV_CHART_AXIS_PRIMARY_Y, 0, 100);
    lv_chart_set_div_line_count(s_chart, 2, 0);

    s_ser_cpu = lv_chart_add_series(s_chart, lv_color_hex(UI_COL_CPU), LV_CHART_AXIS_PRIMARY_Y);
    s_ser_gpu = lv_chart_add_series(s_chart, lv_color_hex(UI_COL_GPU), LV_CHART_AXIS_PRIMARY_Y);
    lv_chart_set_all_value(s_chart, s_ser_cpu, 0);
    lv_chart_set_all_value(s_chart, s_ser_gpu, 0);
}

/* 顶栏主机名：属于三页共享的顶栏，不跟页面分派一起走 */
static void host_update(const stats_t *s, bool stale)
{
    if (s == NULL || s->host[0] == '\0') {
        lv_label_set_text(s_lbl_host, "--");
    } else {
        lv_label_set_text(s_lbl_host, s->host);
    }
    set_label_color(s_lbl_host, UI_COL_MUTED, stale || s == NULL);
}

static void page0_update(const stats_t *s, bool stale)
{
    char buf[40];

    if (s == NULL) {
        lv_label_set_text(s_gpu_val, "--");
        lv_label_set_text(s_gpu_sub, "--");
        lv_bar_set_value(s_gpu_bar, 0, LV_ANIM_OFF);
        lv_label_set_text(s_cpu_val, "--");
        lv_label_set_text(s_cpu_sub, "--");
        lv_bar_set_value(s_cpu_bar, 0, LV_ANIM_OFF);
        lv_label_set_text(s_mem_val, "--%");
        lv_label_set_text(s_mem_sub, "--/-- MB");
        lv_label_set_text(s_net_val, "--");
    } else {
        int gpu_util = s->has_gpu ? s->gpu_util : 0;
        if (gpu_util < 0) {
            gpu_util = 0;
        }
        if (gpu_util > 100) {
            gpu_util = 100;
        }
        lv_bar_set_value(s_gpu_bar, gpu_util, LV_ANIM_OFF);
        lv_label_set_text_fmt(s_gpu_val, "%d%%", gpu_util);
        if (s->has_gpu) {
            snprintf(buf, sizeof(buf), "%d°C %.0fW", s->gpu_temp, (double)s->gpu_power);
            lv_label_set_text(s_gpu_sub, buf);
        } else {
            lv_label_set_text(s_gpu_sub, "N/A");
        }

        int cpu_pct = (int)(s->cpu_pct + 0.5f);
        if (cpu_pct < 0) {
            cpu_pct = 0;
        }
        if (cpu_pct > 100) {
            cpu_pct = 100;
        }
        lv_bar_set_value(s_cpu_bar, cpu_pct, LV_ANIM_OFF);
        lv_label_set_text_fmt(s_cpu_val, "%d%%", cpu_pct);
        if (s->cpu_mhz > 0) {
            lv_label_set_text_fmt(s_cpu_sub, "%dMHz", s->cpu_mhz);
        } else {
            lv_label_set_text(s_cpu_sub, "--MHz");
        }

        int mem_pct = (int)(s->mem_pct + 0.5f);
        if (mem_pct < 0) {
            mem_pct = 0;
        }
        if (mem_pct > 100) {
            mem_pct = 100;
        }
        lv_label_set_text_fmt(s_mem_val, "%d%%", mem_pct);
        lv_label_set_text_fmt(s_mem_sub, "%d/%d MB", s->mem_used_mib, s->mem_total_mib);

        snprintf(buf, sizeof(buf), "DN %.1f  UP %.1f MB/s", (double)s->net_down, (double)s->net_up);
        lv_label_set_text(s_net_val, buf);
    }

    /* 颜色（新鲜/陈旧两态）——改样式不建对象，开销可忽略 */
    set_label_color(s_gpu_val, UI_COL_GPU, stale);
    set_label_color(s_gpu_sub, UI_COL_MUTED, stale);
    set_bar_color(s_gpu_bar, UI_COL_GPU, stale);
    set_label_color(s_cpu_val, UI_COL_CPU, stale);
    set_label_color(s_cpu_sub, UI_COL_MUTED, stale);
    set_bar_color(s_cpu_bar, UI_COL_CPU, stale);
    set_label_color(s_mem_val, UI_COL_MEM, stale);
    set_label_color(s_mem_sub, UI_COL_MUTED, stale);
    set_label_color(s_net_val, UI_COL_NET, stale);
    lv_chart_set_series_color(s_chart, s_ser_cpu, lv_color_hex(stale ? UI_COL_DIM : UI_COL_CPU));
    lv_chart_set_series_color(s_chart, s_ser_gpu, lv_color_hex(stale ? UI_COL_DIM : UI_COL_GPU));
}

/* =====================================================================
 * 页面 1 容器 + OFFLINE 覆盖层
 * ===================================================================== */

static void page1_create(lv_obj_t *scr)
{
    s_page1 = lv_obj_create(scr);
    lv_obj_remove_style_all(s_page1);
    lv_obj_set_pos(s_page1, 0, 20);
    lv_obj_set_size(s_page1, BOARD_LCD_H_RES, BOARD_LCD_V_RES - 20);
    lv_obj_remove_flag(s_page1, LV_OBJ_FLAG_SCROLLABLE);

    ui_detail_create(s_page1);
}

static void page2_create(lv_obj_t *scr)
{
    s_page2 = lv_obj_create(scr);
    lv_obj_remove_style_all(s_page2);
    lv_obj_set_pos(s_page2, 0, 20);
    lv_obj_set_size(s_page2, BOARD_LCD_H_RES, BOARD_LCD_V_RES - 20);
    lv_obj_remove_flag(s_page2, LV_OBJ_FLAG_SCROLLABLE);

    ui_dsh_create(s_page2);
}

/* 按当前页分派内容刷新：page 0 总览 / page 1 线程明细 / page 2 DSH。
 * 顶栏（主机名·时间·状态·页码）和 P0 的曲线历史不在这里 —— 它们与页无关，
 * 由 ui_update() 单独维护，所以翻页不会打断曲线。 */
static void dispatch_page_update(const stats_t *s, bool stale)
{
    if (s_page == 0) {
        page0_update(s, stale);
    } else if (s_page == 1) {
        ui_detail_update(s, stale);
    } else {
        ui_dsh_update(s, stale);
    }
}

static void offline_create(lv_obj_t *scr)
{
    s_offline = ui_tag(scr, &lv_font_montserrat_28, UI_COL_DIM, 0, 0, "OFFLINE");
    /* 顶栏 20px 属于界面框架，视觉中心下移 10px 落在内容区正中 */
    lv_obj_align(s_offline, LV_ALIGN_CENTER, 0, 10);
    lv_obj_add_flag(s_offline, LV_OBJ_FLAG_HIDDEN);
}

/* =====================================================================
 * 对外 API
 * ===================================================================== */

void ui_init(void)
{
    /* 设备拿不到 PC 的时区：v1 按用户所在地 UTC+8 显示 stats.pc_time（已报备 Lead）。
     * 以后要支持多时区就从 NVS 读，别在这里加 if。 */
    setenv("TZ", "CST-8", 1);
    tzset();

    lv_obj_t *scr = lv_screen_active();
    lv_obj_remove_flag(scr, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(scr, lv_color_hex(UI_COL_BG), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, LV_PART_MAIN);

    top_bar_create(scr);
    page0_create(scr);
    page1_create(scr);
    page2_create(scr);
    offline_create(scr);

    /* 上电先显 OFFLINE，等第一帧到达再撤掉（NO_LINK 的诚实显示） */
    lv_obj_remove_flag(s_offline, LV_OBJ_FLAG_HIDDEN);

    /* 恢复 NVS 里记住的页（architecture.md §4 key=page）。
     * 注意：cfg_page()/cfg_set_page() 目前把页号夹在 0..1（cfg.c 里是 `> 1`），
     * 所以 P2 重启后回不来；等 Lead/fw-link 把边界改成 2 就自动生效，
     * 这边已按 0..2 处理好。 */
    uint8_t saved = cfg_page();
    s_page = (saved <= 2) ? saved : 0;
    apply_page();

    s_ready = true;
    ESP_LOGI(TAG, "ui init done: %dx%d, start page %u", BOARD_LCD_H_RES, BOARD_LCD_V_RES, s_page);
}

void ui_update(const stats_t *s, link_state_t st, uint32_t age_s)
{
    if (!s_ready) {
        return;
    }
    /* bring-up 标定模式：LCD 正在显示测试图，UI 不要抢屏幕 */
    if (lcd_test_pattern_active()) {
        return;
    }

    bool have = (s != NULL);
    bool online = (st == LINK_ONLINE_USB || st == LINK_ONLINE_WIFI);
    /* 新鲜度：没有数据 / 不在 ONLINE / age>2 三者满足任一就按陈旧处理。
     * （不变式要求 ONLINE 时 age<=2；这里再判一次是防御，防止旧数据被画成实时） */
    bool stale = (!have) || (!online) || (age_s > 2);

    clock_update(have ? s->pc_time : 0, stale);
    status_update(st, age_s, stale);

    /* 顶栏主机名 + 最近一帧快照（翻页时立刻刷新新页要用） */
    host_update(s, stale);
    s_have_data = have;
    if (have) {
        s_last_stats = *s;
        s_last_stats_ok = true;
    }

    /* 只有「新帧 / 有无数据变化 / 新鲜度变化」才重写数值区。
     * s_force_full_update：刚退出标定图模式时强制走一次完整刷新 —— 测试图期间
     * ui_update 一直是早退的，缓存里的 seq/新鲜度可能已经和屏上状态对不上。 */
    bool changed = s_force_full_update ||
                   (have != s_last_have) ||
                   (have && (!s_have_seq || s->seq != s_last_seq)) ||
                   (stale != s_last_stale);
    if (!changed) {
        return;
    }
    s_force_full_update = false;
    s_last_have = have;
    s_last_stale = stale;
    if (have) {
        s_last_seq = s->seq;
        s_have_seq = true;
    }

    /* OFFLINE 只覆盖 P0/P1；P2 没数据时自己显示 DSH N/A */
    if (have || s_page == 2) {
        lv_obj_add_flag(s_offline, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_remove_flag(s_offline, LV_OBJ_FLAG_HIDDEN);
    }

    dispatch_page_update(s, stale);

    /* 曲线：只在「有新帧且新鲜」时推进一个点，并且按 PC 时间戳**每秒最多推一个**
     * —— 上位机 1~2 Hz 发帧，按帧推会变成 2 点/秒，60 点窗口只剩 30 秒。
     * STALE 时不能拿旧值继续刷，否则曲线会把断流画成平稳运行。 */
    if (have && !stale) {
        bool new_second = (s->pc_time != 0) ? (s->pc_time != s_last_chart_time)
                                            : (s->seq != s_last_chart_seq);
        if (!s_chart_have || new_second) {
            int32_t cpu_v = (int32_t)(s->cpu_pct + 0.5f);
            int32_t gpu_v = s->has_gpu ? s->gpu_util : 0;
            if (cpu_v < 0) {
                cpu_v = 0;
            }
            if (cpu_v > 100) {
                cpu_v = 100;
            }
            if (gpu_v < 0) {
                gpu_v = 0;
            }
            if (gpu_v > 100) {
                gpu_v = 100;
            }
            lv_chart_set_next_value(s_chart, s_ser_cpu, cpu_v);
            lv_chart_set_next_value(s_chart, s_ser_gpu, gpu_v);
            s_last_chart_seq = s->seq;
            s_last_chart_time = s->pc_time;
            s_chart_have = true;
        }
    }
}

void ui_exit_test_mode(void)
{
    if (!s_ready) {
        return;
    }
    /* 三件事缺一不可：
     * 1) 清「测试图模式」标志，让 ui_update 重新接管；
     * 2) 整屏 invalidate —— 测试图是绕过 LVGL 直接写面板的像素，LVGL 仍以为
     *    屏上是它画的内容，不整屏失效的话只有后续变化的 label 会重绘，其余
     *    区域还留着测试图；
     * 3) 强制下一次 ui_update 走完整刷新（见 s_force_full_update）。 */
    lcd_test_pattern_stop();
    lv_obj_invalidate(lv_screen_active());
    s_force_full_update = true;
    ESP_LOGI(TAG, "test-pattern mode exited, UI takes over");
}

void ui_next_page(void)
{
    if (!s_ready) {
        return;
    }
    /* KEY 短按 = 退出 bring-up 测试图模式并翻页（测试图模式下屏幕被标定图案占用）。
     * 与 BOOT 长按退出（main.c 调 ui_exit_test_mode）走同一套逻辑，保证两条
     * 退出路径行为一致：清标志 + 整屏 invalidate + 强制全刷。 */
    if (lcd_test_pattern_active()) {
        ui_exit_test_mode();
    }
    s_page = (uint8_t)((s_page + 1u) % 3u);
    apply_page();

    /* 立刻用最近一帧刷新新页：只分派当前页的话，1 Hz 数据流下重绘前会有
     * 最多 1 秒的占位符。还没有数据时按 stale 处理（数字降饱和）。 */
    bool stale_now = s_last_stats_ok ? s_last_stale : true;
    dispatch_page_update(s_last_stats_ok ? &s_last_stats : NULL, stale_now);
}

uint8_t ui_page(void)
{
    return s_page;
}
