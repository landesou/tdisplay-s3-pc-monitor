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
    s_line2 = ui_tag(parent, &lv_font_montserrat_14, UI_COL_MUTED, 6, 124, "T --  UP --  FPS --");
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
        lv_label_set_text(s_line2, "T --  UP --  FPS --");
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
    if (temp >= 0) {
        snprintf(line2, sizeof(line2), "T %dC  UP %s  FPS %u", temp, up, (unsigned)lvgl_port_fps());
    } else {
        snprintf(line2, sizeof(line2), "T --  UP %s  FPS %u", up, (unsigned)lvgl_port_fps());
    }
    lv_label_set_text(s_line2, line2);
}
