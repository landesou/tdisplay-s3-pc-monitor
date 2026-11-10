#pragma once

/**
 * LilyGO T-Display-S3 板级引脚定义
 *
 * 屏：1.9" ST7789V IPS，170x320（物理），8-bit Intel 8080 并口（不是 SPI！）
 * 来源：CircuitPython 板级定义 / PlatformIO board def（经 TinkerNews 汇总页转述），
 *      见 docs/journal.md「硬件事实核对」。
 *
 * 关键坑：LCD_POWER_ON(GPIO15) 必须先拉高，否则屏不亮、背光开了也是黑屏。
 */

/* ---- LCD 并口 ---- */
#define BOARD_LCD_D0        39
#define BOARD_LCD_D1        40
#define BOARD_LCD_D2        41
#define BOARD_LCD_D3        42
#define BOARD_LCD_D4        45
#define BOARD_LCD_D5        46
#define BOARD_LCD_D6        47
#define BOARD_LCD_D7        48
#define BOARD_LCD_WR        8   /* 8080 的 WR，也是 i80 的 PCLK */
#define BOARD_LCD_RD        9   /* 读使能，只写模式下不使用 */
#define BOARD_LCD_DC        7
#define BOARD_LCD_CS        6
#define BOARD_LCD_RST       5
#define BOARD_LCD_BCKL      38  /* 背光，LEDC PWM 调光 */
#define BOARD_LCD_POWER_ON  15  /* 屏电源使能，高有效 */

/* 物理分辨率（面板原生方向：竖） */
#define BOARD_PANEL_W       170
#define BOARD_PANEL_H       320

/* 逻辑分辨率（本产品横屏摆放：320x170） */
#define BOARD_LCD_H_RES     320
#define BOARD_LCD_V_RES     170

/* ---- 按键 ---- */
#define BOARD_BTN_BOOT      0   /* 也是下载模式 strapping 脚 */
#define BOARD_BTN_KEY       14

/* ---- 电源/电池 ---- */
#define BOARD_PIN_BATTERY   4   /* ADC1_CH3，v1 仅读取备用 */

/* ---- 其它 ---- */
/* 官方 LilyGO 配置用 16 MHz（"Too low or too high pixel clock may cause screen mosaic"），
 * 先用官方值保证稳定，跑通后再往上试。 */
#define BOARD_LCD_PIXEL_CLOCK_HZ  (16 * 1000 * 1000)
