/**
 * 页面 1：明细页（24 线程迷你柱状图 + 底部两行小字）。
 *
 * 对象只在 ui_detail_create() 里建一次；之后 ui_detail_update() 只改
 * 高度/位置/颜色/文字 —— 这是 task-2 的硬约束（不许每帧重建对象）。
 *
 * 与 Lead 给的原始布局的两处必要偏差（已报备）：
 *   1. 24 根柱 × 12px + 2px 间隔 = 334 px > 320 px，放不下 → 柱宽 11px（总宽 310）。
 *   2. 顶栏现在由 ui.c 统一画在屏幕顶部（两页共享），所以本页可用高度是 150 px 而不是 170。
 */

#include <stdio.h>

#include "lvgl.h"

#include "lvgl_port.h"
#include "ui.h"
#include "ui_internal.h"

#define DETAIL_BARS     24
#define DETAIL_BAR_W    11
#define DETAIL_BAR_GAP  2
#define DETAIL_BAR_X0   5
#define DETAIL_BAR_TOP  2
#define DETAIL_BAR_BASE 100                                  /* 柱底基线（相对页面容器） */
#define DETAIL_BAR_H    (DETAIL_BAR_BASE - DETAIL_BAR_TOP)   /* 98 px 满量程 */

static lv_obj_t *s_fills[DETAIL_BARS];
static lv_obj_t *s_line1;
static lv_obj_t *s_line2;

static uint32_t core_color(uint8_t pct)
{
    if (pct >= 90) {
        return UI_COL_ALERT;
    }
    if (pct >= 60) {
        return UI_COL_NET;
    }
    return UI_COL_CPU;
}

/* 单根柱：高度按百分比，底端对齐基线；stale 时降饱和成 UI_COL_DIM。 */
static void set_fill(int idx, uint8_t pct, bool stale, bool has_data)
{
    int h = 2;
    if (has_data) {
        h = 2 + ((int)pct * DETAIL_BAR_H) / 100;
        if (h > DETAIL_BAR_H) {
            h = DETAIL_BAR_H;
        }
    }
    lv_obj_set_y(s_fills[idx], DETAIL_BAR_BASE - h);
    lv_obj_set_height(s_fills[idx], (int32_t)h);

    uint32_t color = has_data ? (stale ? UI_COL_DIM : core_color(pct)) : UI_COL_TRACK;
    lv_obj_set_style_bg_color(s_fills[idx], lv_color_hex(color), LV_PART_MAIN);
}

/* 开机时长 -> "3d 05:12" / "5:12" */
static void fmt_uptime(uint32_t up_s, char *buf, size_t len)
{
    uint32_t days = up_s / 86400u;
    uint32_t hours = (up_s % 86400u) / 3600u;
    uint32_t mins = (up_s % 3600u) / 60u;
    if (days > 0) {
        snprintf(buf, len, "%ud %02u:%02u", (unsigned)days, (unsigned)hours, (unsigned)mins);
    } else {
        snprintf(buf, len, "%u:%02u", (unsigned)hours, (unsigned)mins);
    }
}

void ui_detail_create(lv_obj_t *parent)
{
    /* 先建 24 条底槽，再建 24 根填充柱，保证填充柱压在底槽上面 */
    for (int i = 0; i < DETAIL_BARS; i++) {
        lv_obj_t *track = lv_obj_create(parent);
        lv_obj_remove_style_all(track);
        lv_obj_set_pos(track, DETAIL_BAR_X0 + i * (DETAIL_BAR_W + DETAIL_BAR_GAP), DETAIL_BAR_TOP);
        lv_obj_set_size(track, DETAIL_BAR_W, DETAIL_BAR_H);
        lv_obj_remove_flag(track, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_set_style_bg_color(track, lv_color_hex(UI_COL_TRACK), LV_PART_MAIN);
        lv_obj_set_style_bg_opa(track, LV_OPA_COVER, LV_PART_MAIN);
        lv_obj_set_style_radius(track, 2, LV_PART_MAIN);
    }
    for (int i = 0; i < DETAIL_BARS; i++) {
        lv_obj_t *fill = lv_obj_create(parent);
        lv_obj_remove_style_all(fill);
        lv_obj_set_pos(fill, DETAIL_BAR_X0 + i * (DETAIL_BAR_W + DETAIL_BAR_GAP), DETAIL_BAR_BASE - 2);
        lv_obj_set_size(fill, DETAIL_BAR_W, 2);
        lv_obj_remove_flag(fill, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_set_style_bg_color(fill, lv_color_hex(UI_COL_TRACK), LV_PART_MAIN);
        lv_obj_set_style_bg_opa(fill, LV_OPA_COVER, LV_PART_MAIN);
        lv_obj_set_style_radius(fill, 2, LV_PART_MAIN);
        s_fills[i] = fill;
    }

    /* 底部两行：磁盘 R/W + 显存 / 温度 + 开机时长 + LVGL 帧率 */
    s_line1 = ui_tag(parent, &lv_font_montserrat_14, UI_COL_MUTED, 6, 104, "DISK --  VRAM --/-- MB");
    s_line2 = ui_tag(parent, &lv_font_montserrat_14, UI_COL_MUTED, 6, 124, "T --  UP --  SEQ --");
}

void ui_detail_update(const stats_t *s, bool stale)
{
    bool has_data = (s != NULL);
    uint32_t text_color = (stale || !has_data) ? UI_COL_DIM : UI_COL_MUTED;
    lv_obj_set_style_text_color(s_line1, lv_color_hex(text_color), LV_PART_MAIN);
    lv_obj_set_style_text_color(s_line2, lv_color_hex(text_color), LV_PART_MAIN);

    if (!has_data) {
        for (int i = 0; i < DETAIL_BARS; i++) {
            set_fill(i, 0, true, false);
        }
        lv_label_set_text(s_line1, "DISK --  VRAM --/-- MB");
        lv_label_set_text(s_line2, "T --  UP --  SEQ --");
        return;
    }

    int n = s->n_cores;
    if (n < 0) {
        n = 0;
    }
    if (n > DETAIL_BARS) {
        n = DETAIL_BARS;
    }
    for (int i = 0; i < DETAIL_BARS; i++) {
        if (i < n) {
            set_fill(i, s->cores[i], stale, true);
        } else {
            set_fill(i, 0, true, false);
        }
    }

    char line1[64];
    if (s->has_gpu) {
        snprintf(line1, sizeof(line1), "DISK R %.1f W %.1f MB/s  VRAM %d/%d MB",
                 (double)s->disk_read, (double)s->disk_write,
                 s->gpu_vram_used, s->gpu_vram_total);
    } else {
        snprintf(line1, sizeof(line1), "DISK R %.1f W %.1f MB/s  VRAM --/-- MB",
                 (double)s->disk_read, (double)s->disk_write);
    }
    lv_label_set_text(s_line1, line1);

    /* 温度：优先独显（nvidia-smi 有），否则 CPU（笔记本 v1 基本是 null） */
    int temp = -1;
    if (s->has_gpu) {
        temp = s->gpu_temp;
    } else if (s->cpu_temp >= 0.0f) {
        temp = (int)(s->cpu_temp + 0.5f);
    }

    char up[24];
    fmt_uptime(s->uptime_s, up, sizeof(up));

    char line2[64];
    /* 第三列显示帧号（seq）而不是 FPS：
     * LVGL 是事件驱动的，1 Hz 数据更新下平均刷新率本来就≈0~1，显示 FPS 只会误导；
     * 帧号能直接看出有没有丢帧（PC 端单调递增）。 */
    if (temp >= 0) {
        snprintf(line2, sizeof(line2), "T %dC  UP %s  SEQ %u", temp, up, (unsigned)s->seq);
    } else {
        snprintf(line2, sizeof(line2), "T --  UP %s  SEQ %u", up, (unsigned)s->seq);
    }
    lv_label_set_text(s_line2, line2);
}

/* =====================================================================
 * 页面 2：DSH（DeepSeek Harness）token / 进程页
 * =====================================================================
 * 布局（坐标是页面容器内的：容器从顶栏 20 px 之下开始，320x150）：
 *   左卡 (4,2,150,108)    TOKENS TODAY / 大数字(28) / IN·OUT / CACHE·命中率
 *   右卡 (158,2,158,108)  今日费用(20) / REQ(20) / DSH 进程 / RSS·CPU
 *   底部条 (4,112,312,36) 7 天滚动："7D 8.8M TOK | ~CNY 8.40 | 2100 REQ"
 *   覆盖层：dsh.valid == false 时隐藏三块卡、整页显示 "DSH N/A"（不留一堆 0）
 *
 * 字形限制（查过 vendored Montserrat 的 unicode 表：0x20-0x7E + 0xB0(度) +
 * U+2022(圆点) + LVGL 私有符号；**没有** U+2248(约等号)、U+00A5(人民币符号)、
 * U+00B7(中点) 的字形），所以界面上不能直接用这三个字符：
 *   费用 -> "~CNY 1.23"（`~` 表示估算，契约要求「必须标估算」的意图不变）
 *   分隔 -> ASCII "|"（中点没字形；圆点虽有字形但会搞崩 Windows 构建日志，见下）
 *   要真显示约等号/人民币符号需要额外字体（占 flash），等 Lead 决定。
 *
 * 另：**这两个文件里不要出现 GBK 编不出的字符**（U+2022、U+00A5 等）。
 * 实测 idf.py 在 Windows 上用 GBK 往控制台写构建日志，编译器诊断一旦回显含
 * 这类字符的源码行，idf.py 就抛 UnicodeEncodeError 把整个构建搞挂（不是编译错误）。
 *
 * 数据语义（link.c 解析侧用哨兵默认值，UI 要把它们当"缺字段"）：
 *   today_req/week_req == UINT32_MAX、proc_count/proc_rss_mb == UINT16_MAX -> "--"
 *   week_cache_pct == 0 -> "--"（0% 多半是没数据，别让人误读成"零命中"）
 */

static lv_obj_t *s_dsh_left;
static lv_obj_t *s_dsh_right;
static lv_obj_t *s_dsh_bottom;
static lv_obj_t *s_dsh_na;
static lv_obj_t *s_dsh_tok;    /* 今日 tokens 大数字 */
static lv_obj_t *s_dsh_inout;  /* IN ... OUT ... */
static lv_obj_t *s_dsh_cache;  /* CACHE ... | 命中率 */
static lv_obj_t *s_dsh_cost;   /* ~CNY x.xx */
static lv_obj_t *s_dsh_req;    /* REQ 值 */
static lv_obj_t *s_dsh_proc;   /* "10 PROC" */
static lv_obj_t *s_dsh_rss;    /* "RSS 906M  CPU 12%" */
static lv_obj_t *s_dsh_week;   /* 底部 7 天汇总 */

/* token 自动缩单位：<1000 原数；<1e6 用 K；否则 M。
 * 小数位：K 最多 1 位、M 最多 2 位，末尾的 0 去掉（620K 比 620.0K 省横向空间）。
 * 全部用整数运算：用 %f 的话 GCC 的 -Wformat-truncation 会按 double 的极端量级
 * （300+ 字符）判"可能截断"，在 -Werror=all 下就是构建错误。 */
static void fmt_tok(uint64_t n, char *buf, size_t len)
{
    if (n < 1000ULL) {
        snprintf(buf, len, "%llu", (unsigned long long)n);
    } else if (n < 995000ULL) { /* 995K 以上直接进位到 M，避免显示成 "1000K" */
        unsigned long long k10 = (n + 50ULL) / 100ULL; /* 单位 0.1K，四舍五入 */
        if (k10 % 10ULL == 0ULL) {
            snprintf(buf, len, "%lluK", k10 / 10ULL);
        } else {
            snprintf(buf, len, "%llu.%lluK", k10 / 10ULL, k10 % 10ULL);
        }
    } else {
        unsigned long long m100 = (n + 5000ULL) / 10000ULL; /* 单位 0.01M，四舍五入 */
        if (m100 % 100ULL == 0ULL) {
            snprintf(buf, len, "%lluM", m100 / 100ULL);
        } else if (m100 % 10ULL == 0ULL) {
            snprintf(buf, len, "%llu.%lluM", m100 / 100ULL, (m100 % 100ULL) / 10ULL);
        } else {
            snprintf(buf, len, "%llu.%02lluM", m100 / 100ULL, m100 % 100ULL);
        }
    }
}

/* 费用：估算是契约要求必须标出来的，用 ASCII `~` 代替字体里没有的"约等号"。
 * 同样用整数分位输出，避开 %f 的 -Wformat-truncation。 */
static void fmt_cost(float cost, char *buf, size_t len)
{
    if (!(cost > 0.0f)) { /* NaN / 负数都按 0 处理 */
        cost = 0.0f;
    }
    if (cost > 1.0e6f) { /* 界面上限；估算金额本来也不该到百万级 */
        cost = 1.0e6f;
    }
    unsigned long cents = (unsigned long)(cost * 100.0f + 0.5f);
    snprintf(buf, len, "~CNY %lu.%02lu", cents / 100UL, cents % 100UL);
}

void ui_dsh_create(lv_obj_t *parent)
{
    /* ---- 左卡：今日 tokens ---- */
    s_dsh_left = ui_card(parent, 4, 2, 150, 108);
    ui_tag(s_dsh_left, &lv_font_montserrat_14, UI_COL_MUTED, 8, 4, "TOKENS TODAY");
    s_dsh_tok = ui_tag(s_dsh_left, &lv_font_montserrat_28, UI_COL_CPU, 8, 22, "--");
    s_dsh_inout = ui_tag(s_dsh_left, &lv_font_montserrat_14, UI_COL_MUTED, 8, 60, "IN --  OUT --");
    s_dsh_cache = ui_tag(s_dsh_left, &lv_font_montserrat_14, UI_COL_MUTED, 8, 80, "CACHE --");

    /* ---- 右卡：费用 / 请求数 / DSH 进程 ---- */
    s_dsh_right = ui_card(parent, 158, 2, 158, 108);
    s_dsh_cost = ui_tag(s_dsh_right, &lv_font_montserrat_20, UI_COL_NET, 0, 0, "--");
    lv_obj_align(s_dsh_cost, LV_ALIGN_TOP_RIGHT, -8, 2);
    ui_tag(s_dsh_right, &lv_font_montserrat_14, UI_COL_MUTED, 8, 32, "REQ");
    s_dsh_req = ui_tag(s_dsh_right, &lv_font_montserrat_20, UI_COL_CPU, 0, 0, "--");
    lv_obj_align(s_dsh_req, LV_ALIGN_TOP_RIGHT, -8, 28);
    ui_tag(s_dsh_right, &lv_font_montserrat_14, UI_COL_MUTED, 8, 56, "DSH");
    s_dsh_proc = ui_tag(s_dsh_right, &lv_font_montserrat_14, UI_COL_CPU, 0, 0, "--");
    lv_obj_align(s_dsh_proc, LV_ALIGN_TOP_RIGHT, -8, 56);
    s_dsh_rss = ui_tag(s_dsh_right, &lv_font_montserrat_14, UI_COL_MUTED, 8, 80, "--");

    /* ---- 底部条：近 7 天滚动 ---- */
    s_dsh_bottom = ui_card(parent, 4, 112, 312, 36);
    s_dsh_week = ui_tag(s_dsh_bottom, &lv_font_montserrat_14, UI_COL_MUTED, 0, 0, "--");
    lv_obj_center(s_dsh_week);

    /* ---- 整页 N/A 覆盖层（默认隐藏） ---- */
    s_dsh_na = ui_tag(parent, &lv_font_montserrat_28, UI_COL_DIM, 0, 0, "DSH N/A");
    lv_obj_align(s_dsh_na, LV_ALIGN_CENTER, 0, 0);
    lv_obj_add_flag(s_dsh_na, LV_OBJ_FLAG_HIDDEN);
}

void ui_dsh_update(const stats_t *s, bool stale)
{
    bool valid = (s != NULL) && s->dsh.valid;

    /* 帧里没带 dsh（老上位机 / 采集失败降级）：整页 "DSH N/A"，不留 0 */
    if (!valid) {
        lv_obj_add_flag(s_dsh_left, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_dsh_right, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_dsh_bottom, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(s_dsh_na, LV_OBJ_FLAG_HIDDEN);
        return;
    }

    lv_obj_remove_flag(s_dsh_left, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(s_dsh_right, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(s_dsh_bottom, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_dsh_na, LV_OBJ_FLAG_HIDDEN);

    const dsh_stats_t *d = &s->dsh;
    char tok[48];
    char in_s[48];
    char out_s[48];
    char sub[160];
    char cost[48];

    /* ---- 左卡：今日 tokens ---- */
    fmt_tok(d->today_tok, tok, sizeof(tok));
    lv_label_set_text(s_dsh_tok, tok);

    fmt_tok(d->today_in, in_s, sizeof(in_s));
    fmt_tok(d->today_out, out_s, sizeof(out_s));
    snprintf(sub, sizeof(sub), "IN %s  OUT %s", in_s, out_s);
    lv_label_set_text(s_dsh_inout, sub);

    fmt_tok(d->today_cache, tok, sizeof(tok));
    if (d->week_cache_pct == 0) {
        /* 0% 多半是「没数据」，任务要求显示 -- */
        snprintf(sub, sizeof(sub), "CACHE %s | --", tok);
    } else {
        snprintf(sub, sizeof(sub), "CACHE %s | %u%%", tok, (unsigned)d->week_cache_pct);
    }
    lv_label_set_text(s_dsh_cache, sub);

    /* ---- 右卡：费用 / 请求数 / 进程 ---- */
    fmt_cost(d->today_cost, cost, sizeof(cost));
    lv_label_set_text(s_dsh_cost, cost);

    if (d->today_req == UINT32_MAX) {
        lv_label_set_text(s_dsh_req, "--");
    } else {
        snprintf(sub, sizeof(sub), "%u", (unsigned)d->today_req);
        lv_label_set_text(s_dsh_req, sub);
    }

    bool proc_missing = (d->proc_count == UINT16_MAX);
    if (proc_missing) {
        lv_label_set_text(s_dsh_proc, "--");
        lv_label_set_text(s_dsh_rss, "--");
    } else {
        snprintf(sub, sizeof(sub), "%u PROC", (unsigned)d->proc_count);
        lv_label_set_text(s_dsh_proc, sub);

        if (d->proc_rss_mb == UINT16_MAX) {
            snprintf(sub, sizeof(sub), "RSS --  CPU %u%%", (unsigned)d->proc_cpu_pct);
        } else {
            snprintf(sub, sizeof(sub), "RSS %uM  CPU %u%%",
                     (unsigned)d->proc_rss_mb, (unsigned)d->proc_cpu_pct);
        }
        lv_label_set_text(s_dsh_rss, sub);
    }

    /* ---- 底部条：近 7 天滚动（费用同样按估算标 ~） ---- */
    char week_tok[48];
    fmt_tok(d->week_tok, week_tok, sizeof(week_tok));
    fmt_cost(d->week_cost, cost, sizeof(cost));
    if (d->week_req == UINT32_MAX) {
        snprintf(sub, sizeof(sub), "7D %s TOK | %s | -- REQ", week_tok, cost);
    } else {
        snprintf(sub, sizeof(sub), "7D %s TOK | %s | %u REQ",
                 week_tok, cost, (unsigned)d->week_req);
    }
    lv_label_set_text(s_dsh_week, sub);

    /* ---- 新鲜度：数值全部 fresh/stale 两态 ---- */
    ui_text_color(s_dsh_tok, UI_COL_CPU, stale);
    ui_text_color(s_dsh_inout, UI_COL_MUTED, stale);
    ui_text_color(s_dsh_cache, UI_COL_MUTED, stale);
    ui_text_color(s_dsh_cost, UI_COL_NET, stale);
    ui_text_color(s_dsh_req, UI_COL_CPU, stale);
    /* 进程在跑但本地 GUI 端口没监听 = 异常，用告警色（不是 stale 色） */
    uint32_t proc_color = (!proc_missing && !d->proc_port_up && d->proc_count > 0)
                          ? UI_COL_ALERT : UI_COL_CPU;
    ui_text_color(s_dsh_proc, proc_color, stale);
    ui_text_color(s_dsh_rss, UI_COL_MUTED, stale);
    ui_text_color(s_dsh_week, UI_COL_MUTED, stale);
}
