/**
 * LCD 驱动：ESP32-S3 LCD_CAM 的 Intel 8080 并口（8-bit）+ ST7789。
 *
 * 参考：ESP-IDF v5.5.3 examples/peripherals/lcd/i80_controller/main/i80_controller_example_main.c
 *       （本机路径 D:\ESP32-S3\...\esp-idf-v5.5.3\examples\peripherals\lcd\i80_controller）
 * 该示例是 240x280 SPI 屏，**总线/DC/时序照抄，尺寸/旋转/gap 按本板改**。
 *
 * 本板硬件事实（见 docs/journal.md「硬件事实核对」）：
 *   D0..D7 = 39/40/41/42/45/46/47/48，WR = 8，DC = 7，CS = 6，RST = 5，
 *   背光 = 38（LEDC），LCD_POWER_ON = 15（高有效，必须先拉高，否则黑屏）。
 *
 * 已知不确定性（板子还没插电脑，无法实测）：
 *   面板原生 170x320，横屏 320x170 靠 MADCTL 的 MV/MX/MY + gap + invert + RGB/BGR。
 *   这 6 个参数的正确组合只能上板看画面确定，所以集中在 k_variants[] 的 4 组里，
 *   由 lcd_test_pattern(variant) / lcd_test_variant_next() 轮换，看测试图一眼判断。
 */

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "driver/gpio.h"
#include "driver/ledc.h"

#include "esp_check.h"
#include "esp_lcd_io_i80.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_log.h"

#include "board.h"
#include "lcd.h"
#include "lcd_internal.h"

static const char *TAG = "lcd";

/* =====================================================================
 * 四组候选参数
 * =====================================================================
 * ST7789 MADCTL(0x36)：MY=0x80 MX=0x40 MV=0x20 ML=0x10 BGR=0x08 MH=0x04
 *
 * 横屏 320x170 = MV(行列交换) + 170 那条边落在 240 的 RAM 行轴上：
 *   控制器 GRAM 240 x 320，MV=1 后 CASET 走 320 方向、RASET 走 240 方向，
 *   170 的可见窗口在 240 行里居中 → 偏移 (240-170)/2 = 35，且只能加在 y 轴
 *   （x 轴 320 已顶满 320，再加会越界）。所以所有 variant 的 gap_x 都是 0。
 *
 * 首选 V0 的理由：MX+MV 是 ST7789 横屏最常见组合；这块是 IPS 屏，通常要 INON。
 * 四组覆盖：镜像两组、颜色两组（invert/BGR）、gap 有无，未覆盖的组合等上板后再补。
 */
#define LCD_VARIANT_COUNT 4

typedef struct {
    const char *desc;
    bool swap_xy;   /* MV */
    bool mirror_x;  /* MX */
    bool mirror_y;  /* MY */
    bool invert;    /* INON/INVOFF */
    bool bgr;       /* MADCTL BGR 位 */
    int  gap_x;
    int  gap_y;
} lcd_variant_t;

static const lcd_variant_t k_variants[LCD_VARIANT_COUNT] = {
    /* 0 首选：横屏、MX+MV、IPS 反色、RGB、170 居中 */
    { "landscape MV+MX invert RGB gap(0,35)",    true,  true,  false, true,  false, 0, 35 },
    /* 1 换镜像轴：MY+MV（画面应左右/上下翻一次） */
    { "landscape MV+MY invert RGB gap(0,35)",    true,  false, true,  true,  false, 0, 35 },
    /* 2 颜色两个维度都反：不反色 + BGR（红蓝互换/黑白反相时选它） */
    { "landscape MV+MX no-inv BGR gap(0,35)",    true,  true,  false, false, true,  0, 35 },
    /* 3 最"裸"：不镜像、不加 gap（若只有这组是正的，说明 V0 的镜像/偏移假设都错） */
    { "landscape MV no-mirror invert RGB gap0",  true,  false, false, true,  false, 0, 0  },
};

/* ---- 驱动状态 ---- */
static esp_lcd_panel_io_handle_t s_io = NULL;
static esp_lcd_panel_handle_t s_panel = NULL;
static int  s_variant = 0;
static bool s_backlight_pwm_ready = false;
static bool s_test_active = false;

/* 测试图案用的一行缓冲（内部 RAM，320 px = 640 B）；写面板前转成大端 */
static uint16_t s_row_buf[BOARD_LCD_H_RES];

/* =====================================================================
 * 电源 / 背光
 * ===================================================================== */

/* GPIO15 高有效：不拉高这块屏完全不工作（journal 里记的关键坑）。 */
static esp_err_t lcd_power_on(void)
{
    gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << BOARD_LCD_POWER_ON,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&cfg), TAG, "power gpio config failed");
    ESP_RETURN_ON_ERROR(gpio_set_level(BOARD_LCD_POWER_ON, 1), TAG, "power on failed");
    vTaskDelay(pdMS_TO_TICKS(10)); /* 等面板电源稳定再发命令 */
    return ESP_OK;
}

/* 背光：LEDC 5 kHz / 10 bit，GPIO38，高电平点亮。 */
static esp_err_t backlight_pwm_init(void)
{
    ledc_timer_config_t timer = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_10_BIT,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = 5000,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_RETURN_ON_ERROR(ledc_timer_config(&timer), TAG, "ledc timer config failed");

    ledc_channel_config_t channel = {
        .gpio_num = BOARD_LCD_BCKL,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_0,
        .intr_type = LEDC_INTR_DISABLE,
        .timer_sel = LEDC_TIMER_0,
        .duty = 0, /* 先关背光，等面板初始化完再亮 */
        .hpoint = 0,
    };
    ESP_RETURN_ON_ERROR(ledc_channel_config(&channel), TAG, "ledc channel config failed");
    s_backlight_pwm_ready = true;
    return ESP_OK;
}

/* =====================================================================
 * 总线 / 面板
 * ===================================================================== */

static esp_err_t i80_bus_init(void)
{
    esp_lcd_i80_bus_handle_t bus = NULL;
    esp_lcd_i80_bus_config_t bus_cfg = {
        .clk_src = LCD_CLK_SRC_DEFAULT,
        .dc_gpio_num = BOARD_LCD_DC,
        .wr_gpio_num = BOARD_LCD_WR,
        .data_gpio_nums = {
            BOARD_LCD_D0, BOARD_LCD_D1, BOARD_LCD_D2, BOARD_LCD_D3,
            BOARD_LCD_D4, BOARD_LCD_D5, BOARD_LCD_D6, BOARD_LCD_D7,
        },
        .bus_width = 8,
        /* 一次 DMA 够传一整块 draw buffer（320x40x2 = 25600 B） */
        .max_transfer_bytes = BOARD_LCD_H_RES * LCD_DRAW_BUF_LINES * sizeof(uint16_t),
        .dma_burst_size = 64, /* 与官方示例一致 */
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_i80_bus(&bus_cfg, &bus), TAG, "new i80 bus failed");

    /* DC 电平是 8080 并口最关键的一项，与官方示例逐字段一致：
     * idle=0, cmd=0(命令期 DC 低), dummy=0, data=1(数据期 DC 高)。 */
    esp_lcd_panel_io_i80_config_t io_cfg = {
        .cs_gpio_num = BOARD_LCD_CS,
        .pclk_hz = BOARD_LCD_PIXEL_CLOCK_HZ,
        .trans_queue_depth = 10,
        .dc_levels = {
            .dc_idle_level = 0,
            .dc_cmd_level = 0,
            .dc_dummy_level = 0,
            .dc_data_level = 1,
        },
        .lcd_cmd_bits = 8,   /* ST7789：命令 8 bit */
        .lcd_param_bits = 8, /* 参数 8 bit */
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_i80(bus, &io_cfg, &s_io), TAG, "new i80 io failed");
    return ESP_OK;
}

static esp_err_t panel_create(void)
{
    esp_lcd_panel_dev_config_t dev_cfg = {
        .reset_gpio_num = BOARD_LCD_RST,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB, /* BGR 变体在 MADCTL 里单独处理 */
        .bits_per_pixel = 16,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_st7789(s_io, &dev_cfg, &s_panel), TAG, "new st7789 failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(s_panel), TAG, "panel reset failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(s_panel), TAG, "panel init failed");
    return ESP_OK;
}

/* ST7789 MADCTL 位定义 */
#define LCD_MADCTL_MY  (1u << 7)
#define LCD_MADCTL_MX  (1u << 6)
#define LCD_MADCTL_MV  (1u << 5)
#define LCD_MADCTL_BGR (1u << 3)

/**
 * 应用一组候选参数。
 *
 * 为什么 MADCTL 用 tx_param 直接写、而不是调 esp_lcd_panel_mirror/swap_xy：
 * 那几个驱动的 setter 各自发一条 0x36，而 BGR 位没有 setter（只在建 panel 时定死）。
 * 把 MX/MY/MV/BGR 算成一个字节一次写下去，结果与连续调用等价、且不用重建 panel
 * （重建会让 LVGL 缓存的 panel handle 悬空）。gap 和 invert 仍走驱动 API。
 */
static esp_err_t lcd_apply_variant(int variant)
{
    if (variant < 0 || variant >= LCD_VARIANT_COUNT) {
        ESP_LOGE(TAG, "variant %d out of range [0,%d]", variant, LCD_VARIANT_COUNT - 1);
        return ESP_ERR_INVALID_ARG;
    }
    const lcd_variant_t *v = &k_variants[variant];

    uint8_t madctl = 0;
    if (v->mirror_y) {
        madctl |= LCD_MADCTL_MY;
    }
    if (v->mirror_x) {
        madctl |= LCD_MADCTL_MX;
    }
    if (v->swap_xy) {
        madctl |= LCD_MADCTL_MV;
    }
    if (v->bgr) {
        madctl |= LCD_MADCTL_BGR;
    }

    ESP_RETURN_ON_ERROR(esp_lcd_panel_io_tx_param(s_io, 0x36, &madctl, 1), TAG, "madctl failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_set_gap(s_panel, v->gap_x, v->gap_y), TAG, "set_gap failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_invert_color(s_panel, v->invert), TAG, "invert_color failed");

    s_variant = variant;
    ESP_LOGI(TAG, "variant %d: %s (MADCTL=0x%02x gap=(%d,%d))",
             variant, v->desc, madctl, v->gap_x, v->gap_y);
    return ESP_OK;
}

/* =====================================================================
 * 对外 API
 * ===================================================================== */

esp_err_t lcd_init(void)
{
    ESP_LOGI(TAG, "init: LCD_POWER_ON=GPIO%d -> high", BOARD_LCD_POWER_ON);
    ESP_RETURN_ON_ERROR(lcd_power_on(), TAG, "lcd power on failed");

    /* 先关背光（LEDC duty=0），面板初始化完再由 main 调 lcd_backlight_set 打开 */
    ESP_RETURN_ON_ERROR(backlight_pwm_init(), TAG, "backlight init failed");

    ESP_LOGI(TAG, "init: i80 bus (D0..D7=39/40/41/42/45/46/47/48 WR=8 DC=7 CS=6)");
    ESP_RETURN_ON_ERROR(i80_bus_init(), TAG, "i80 bus init failed");

    ESP_LOGI(TAG, "init: st7789 panel (RST=5)");
    ESP_RETURN_ON_ERROR(panel_create(), TAG, "panel create failed");

    ESP_RETURN_ON_ERROR(lcd_apply_variant(s_variant), TAG, "apply variant failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_disp_on_off(s_panel, true), TAG, "disp on failed");

    ESP_LOGI(TAG, "init done: %dx%d logical, panel %dx%d native",
             BOARD_LCD_H_RES, BOARD_LCD_V_RES, BOARD_PANEL_W, BOARD_PANEL_H);
    return ESP_OK;
}

void lcd_backlight_set(uint8_t pct)
{
    if (!s_backlight_pwm_ready) {
        return;
    }
    if (pct > 100) {
        pct = 100;
    }
    uint32_t duty = ((uint32_t)pct * 1023u) / 100u;
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, duty);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
}

esp_lcd_panel_handle_t lcd_panel_handle(void)
{
    return s_panel;
}

esp_lcd_panel_io_handle_t lcd_panel_io(void)
{
    return s_io;
}

bool lcd_test_pattern_active(void)
{
    return s_test_active;
}

void lcd_test_pattern_stop(void)
{
    s_test_active = false;
}

/* =====================================================================
 * Bring-up 测试图案
 * =====================================================================
 * 没有示波器时唯一能判断参数对错的工具。设计目标（task-2）：
 *   - 1px 白边框：缺哪条边 → 该方向 offset/gap 或坐标系错；
 *   - 四角 L 形：左上红 / 右上绿 / 左下蓝 / 右下黄，方向错了一看便知；
 *   - 顶部 R/G/B 三块：红蓝互换 → RGB/BGR 错；黑白反相 → invert 错；
 *   - 中间白色十字 + 一个大号数字（variant 号），确认几何中心与当前组号。
 */

/* 5x7 点阵数字，用来在屏上标出当前 variant（不引字体，几行就够）。 */
#define TEST_GLYPH_W 5
#define TEST_GLYPH_H 7

static const uint8_t k_digit_glyphs[10][TEST_GLYPH_H] = {
    { 0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E }, /* 0 */
    { 0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E }, /* 1 */
    { 0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F }, /* 2 */
    { 0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E }, /* 3 */
    { 0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02 }, /* 4 */
    { 0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E }, /* 5 */
    { 0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E }, /* 6 */
    { 0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08 }, /* 7 */
    { 0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E }, /* 8 */
    { 0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C }, /* 9 */
};

/* 填一个矩形（逻辑坐标，已被 swap_xy 处理过的 320x170 空间）。
 * 数据要大端：ST7789 收 RGB565 是大端，所以逐像素交换高低字节。 */
static void fill_rect(int x, int y, int w, int h, uint16_t rgb565)
{
    if (s_panel == NULL || w <= 0 || h <= 0) {
        return;
    }
    if (x < 0) {
        w += x;
        x = 0;
    }
    if (y < 0) {
        h += y;
        y = 0;
    }
    if (x + w > BOARD_LCD_H_RES) {
        w = BOARD_LCD_H_RES - x;
    }
    if (y + h > BOARD_LCD_V_RES) {
        h = BOARD_LCD_V_RES - y;
    }
    if (w <= 0 || h <= 0) {
        return;
    }

    uint16_t be = (uint16_t)((rgb565 >> 8) | (rgb565 << 8));
    for (int i = 0; i < w; i++) {
        s_row_buf[i] = be;
    }
    for (int row = 0; row < h; row++) {
        esp_lcd_panel_draw_bitmap(s_panel, x, y + row, x + w, y + row + 1, s_row_buf);
    }
}

static void draw_digit(int digit, int x, int y, int scale, uint16_t color)
{
    if (digit < 0 || digit > 9) {
        return;
    }
    for (int row = 0; row < TEST_GLYPH_H; row++) {
        uint8_t bits = k_digit_glyphs[digit][row];
        for (int col = 0; col < TEST_GLYPH_W; col++) {
            if (bits & (1u << (TEST_GLYPH_W - 1 - col))) {
                fill_rect(x + col * scale, y + row * scale, scale, scale, color);
            }
        }
    }
}

/* RGB565 常用色 */
#define TEST_BLACK  0x0000
#define TEST_WHITE  0xFFFF
#define TEST_RED    0xF800
#define TEST_GREEN  0x07E0
#define TEST_BLUE   0x001F
#define TEST_YELLOW 0xFFE0

void lcd_test_pattern(int variant)
{
    if (s_panel == NULL) {
        ESP_LOGE(TAG, "test pattern: lcd_init() not called");
        return;
    }
    if (variant < 0 || variant >= LCD_VARIANT_COUNT) {
        ESP_LOGW(TAG, "test pattern: variant %d clamped to 0", variant);
        variant = 0;
    }
    if (lcd_apply_variant(variant) != ESP_OK) {
        return;
    }

    const int w = BOARD_LCD_H_RES;
    const int h = BOARD_LCD_V_RES;

    /* 1. 背景：全黑（同时清掉上一组参数可能留下的乱码） */
    fill_rect(0, 0, w, h, TEST_BLACK);

    /* 2. 1px 白边框 */
    fill_rect(0, 0, w, 1, TEST_WHITE);
    fill_rect(0, h - 1, w, 1, TEST_WHITE);
    fill_rect(0, 0, 1, h, TEST_WHITE);
    fill_rect(w - 1, 0, 1, h, TEST_WHITE);

    /* 3. 四角 L 形标记（粗 4px），颜色顺序 = 判断镜像/方向 */
    const int L = 26, T = 4;
    fill_rect(0, 0, L, T, TEST_RED);                                /* 左上 红 */
    fill_rect(0, 0, T, L, TEST_RED);
    fill_rect(w - L, 0, L, T, TEST_GREEN);                          /* 右上 绿 */
    fill_rect(w - T, 0, T, L, TEST_GREEN);
    fill_rect(0, h - T, L, T, TEST_BLUE);                           /* 左下 蓝 */
    fill_rect(0, h - L, T, L, TEST_BLUE);
    fill_rect(w - L, h - T, L, T, TEST_YELLOW);                     /* 右下 黄 */
    fill_rect(w - T, h - L, T, L, TEST_YELLOW);

    /* 4. 顶部 R/G/B 三块（各 100x24，y=30..54）：
     *    红蓝互换 = RGB/BGR 错；整块反相 = invert 错 */
    fill_rect(10, 30, 100, 24, TEST_RED);
    fill_rect(110, 30, 100, 24, TEST_GREEN);
    fill_rect(210, 30, 100, 24, TEST_BLUE);

    /* 5. 中间白色十字 */
    fill_rect(10, 84, w - 20, 2, TEST_WHITE); /* 横 */
    fill_rect(w / 2 - 1, 62, 2, h - 118, TEST_WHITE); /* 竖 */

    /* 6. 大号 variant 数字（先垫黑块遮住十字，再画白字） */
    const int scale = 8;
    const int glyph_w = TEST_GLYPH_W * scale;
    const int glyph_h = TEST_GLYPH_H * scale;
    const int gx = (w - glyph_w) / 2;
    const int gy = h - 12 - glyph_h; /* 贴近底部，避开十字与色块 */
    fill_rect(gx - 4, gy - 4, glyph_w + 8, glyph_h + 8, TEST_BLACK);
    draw_digit(variant, gx, gy, scale, TEST_WHITE);

    s_test_active = true;
    ESP_LOGI(TAG, "test pattern drawn: variant=%d (%s)", variant, k_variants[variant].desc);
}

int lcd_test_variant_get(void)
{
    return s_variant;
}

void lcd_test_variant_set(int variant)
{
    lcd_test_pattern(variant);
}

int lcd_test_variant_next(void)
{
    int next = (s_variant + 1) % LCD_VARIANT_COUNT;
    lcd_test_pattern(next);
    return s_variant;
}
