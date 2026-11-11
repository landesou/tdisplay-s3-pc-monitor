#pragma once

#include "lvgl.h"
#include "stats.h"

/**
 * ui 模块**内部**头：ui.c（总览页 + 顶栏 + 翻页）与 ui_pages.c（明细页）共用。
 *
 * 配色与布局是 Lead 在 task-2 里定稿的：主题色不要随手加新的，
 * 尤其 UI_COL_DIM 是「陈旧数据降饱和」这条已验证不变式专用的灰，
 * 只有 fresh/stale 两种取值，不要拿它当普通配色。
 */

/* ---- 主题色（task-2 / Lead 定稿） ---- */
#define UI_COL_BG     0x0B0F14
#define UI_COL_CARD   0x161D26
#define UI_COL_CPU    0x22D3EE
#define UI_COL_GPU    0xA78BFA
#define UI_COL_MEM    0x34D399
#define UI_COL_NET    0xFBBF24
#define UI_COL_ALERT  0xF87171
#define UI_COL_MUTED  0x94A3B8 /* 次要文字 */
#define UI_COL_DIM    0x64748B /* 陈旧数据（STALE/OFFLINE）降饱和用 */
#define UI_COL_TEXT   0xE2E8F0 /* 主文字（时钟） */
#define UI_COL_TRACK  0x1E293B /* 进度条/柱状图底槽 */

/* ---- 顶栏链路状态点 ---- */
#define UI_COL_LINK_USB   0x3B82F6
#define UI_COL_LINK_WIFI  UI_COL_MEM
#define UI_COL_LINK_STALE UI_COL_NET
#define UI_COL_LINK_NONE  UI_COL_DIM

/* ---- 峰谷徽标语义色（DSH 页，task-11）：别在 UI 代码里写魔法值 ---- */
#define UI_COL_WARN  UI_COL_ALERT /* 告警红：高峰（PEAK x2） */
#define UI_COL_GOOD  UI_COL_MEM   /* 成功绿（与 MEM 同色）：空闲（OFF x1） */

/* ---- 页面 1（明细页）由 ui_pages.c 实现 ---- */
/** 在 parent（已定位好的页面容器）里创建全部对象；只调用一次。 */
void ui_detail_create(lv_obj_t *parent);
/** 更新明细页数值；s == NULL 表示还没有任何数据；stale 时全部降饱和。 */
void ui_detail_update(const stats_t *s, bool stale);

/* ---- 页面 2（DSH 页）由 ui_pages.c 实现 ---- */
/** 在 parent（已定位好的页面容器）里创建 DSH 页对象；只调用一次。 */
void ui_dsh_create(lv_obj_t *parent);
/** 更新 DSH 页；s == NULL 或 s->dsh.valid == false 时整页只显示 "DSH N/A"。 */
void ui_dsh_update(const stats_t *s, bool stale);

/* =====================================================================
 * 建对象的公共小工具（只在创建阶段用；更新阶段禁止再建对象）
 * ===================================================================== */

/** 文本颜色：fresh_color 正常，stale 时统一降饱和到 UI_COL_DIM（新鲜度不变式）。 */
static inline void ui_text_color(lv_obj_t *lbl, uint32_t fresh_color, bool stale)
{
    lv_obj_set_style_text_color(lbl, lv_color_hex(stale ? UI_COL_DIM : fresh_color), LV_PART_MAIN);
}

/** 纯文字标签：去掉主题样式后设字体/颜色/位置。 */
static inline lv_obj_t *ui_label(lv_obj_t *parent, const lv_font_t *font, uint32_t color, int x, int y)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_remove_style_all(l);
    lv_obj_set_pos(l, x, y);
    lv_obj_set_style_text_font(l, font, LV_PART_MAIN);
    lv_obj_set_style_text_color(l, lv_color_hex(color), LV_PART_MAIN);
    return l;
}

/** 带初始文字的标签。 */
static inline lv_obj_t *ui_tag(lv_obj_t *parent, const lv_font_t *font, uint32_t color, int x, int y,
                               const char *text)
{
    lv_obj_t *l = ui_label(parent, font, color, x, y);
    lv_label_set_text(l, text);
    return l;
}

/** 卡片（圆角深色底）。 */
static inline lv_obj_t *ui_card(lv_obj_t *parent, int x, int y, int w, int h)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_size(o, w, h);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(o, lv_color_hex(UI_COL_CARD), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(o, 4, LV_PART_MAIN);
    return o;
}

/** 进度条（底槽 + 彩色指示条）。 */
static inline lv_obj_t *ui_bar(lv_obj_t *parent, int x, int y, int w, int h, uint32_t color)
{
    lv_obj_t *b = lv_bar_create(parent);
    lv_obj_remove_style_all(b);
    lv_obj_set_pos(b, x, y);
    lv_obj_set_size(b, w, h);
    lv_obj_set_style_bg_color(b, lv_color_hex(UI_COL_TRACK), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(b, h / 2, LV_PART_MAIN);
    lv_obj_set_style_bg_color(b, lv_color_hex(color), LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, LV_PART_INDICATOR);
    lv_obj_set_style_radius(b, h / 2, LV_PART_INDICATOR);
    lv_bar_set_range(b, 0, 100);
    lv_bar_set_value(b, 0, LV_ANIM_OFF);
    return b;
}
