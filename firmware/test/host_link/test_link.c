/*
 * 主机侧离线测试：直接编译固件里**真实的 firmware/main/link/link.c**，配 shim 头文件
 * 跑在 PC 上（不需要 ESP32、不需要板子）。
 *
 * 目的：板子没插电脑时 USB/WiFi 传输验证不了，但「新鲜度状态机 + JSON 解析」这两块
 *       纯逻辑可以在主机上真跑一遍，比"看着像对"强得多。
 *
 * 覆盖（共 83 条断言）：
 *   [1] 状态机全部迁移 —— INIT 丢帧 / boot_done / NO_LINK / USB 与 WiFi 进在线 /
 *       3 个 tick 转 STALE(age=3) / USB 优先抢占 / WiFi 帧在 USB 在线时被忽略 /
 *       link_lost 回 NO_LINK / SLEEP 终态吃帧不动
 *       + 不变式「ONLINE_* 时 age <= 2」在每次 tick 后断言（logicprobe 验过的那条）
 *   [2] 解析上位机 pcmond --once 输出的**真实帧**（frame.json，424 B、24 核、带 GPU 段）
 *       并确认它**没有 dsh 段** → dsh.valid=false（老帧兼容）
 *   [3] 解析边界：gpu:null、cpu.avg:null、cores 40 个截到 32、越界占用率夹到 0..100、
 *       截断 JSON / 数组 / 空输入全部拒收
 *   [4] 解析 dsh 段（frame_dsh.json，契约 §2.5）：today/week/proc 各字段数值、
 *       int64 token 不被截断（> 2^32 的值必须原样）、port_up 布尔、费用浮点误差
 *
 * 构建/运行：bash firmware/test/host_link/run.sh
 */
#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "link.h"
#include "link_internal.h"

/* ---------------- 桩：FreeRTOS / IDF ---------------- */

static int s_mutex_holder;

SemaphoreHandle_t xSemaphoreCreateMutex(void) { return &s_mutex_holder; }
BaseType_t xSemaphoreTake(SemaphoreHandle_t sem, TickType_t ticks) { (void)sem; (void)ticks; return pdTRUE; }
BaseType_t xSemaphoreGive(SemaphoreHandle_t sem) { (void)sem; return pdTRUE; }
const char *esp_err_to_name(esp_err_t code) { (void)code; return "ESP_ERR"; }
esp_err_t link_usb_start(void) { return ESP_OK; }
esp_err_t link_http_start(void) { return ESP_OK; }

/* MinGW 8.1 没有 strlcpy */
size_t pcm_strlcpy(char *dst, const char *src, size_t size)
{
    size_t len = strlen(src);
    if (size > 0) {
        size_t n = (len >= size) ? size - 1 : len;
        memcpy(dst, src, n);
        dst[n] = '\0';
    }
    return len;
}

/* ---------------- 断言工具 ---------------- */

static int s_checks;
static int s_failures;

#define CHECK(cond, ...)                                     \
    do {                                                     \
        s_checks++;                                          \
        if (cond) {                                          \
            printf("  ok   : ");                             \
        } else {                                             \
            printf("  FAIL : ");                             \
            s_failures++;                                    \
        }                                                    \
        printf(__VA_ARGS__);                                 \
        printf("\n");                                        \
    } while (0)

static void check_state(link_state_t want, const char *what)
{
    link_state_t got = link_state();
    s_checks++;
    if (got == want) {
        printf("  ok   : %-34s -> %s\n", what, link_state_name(got));
    } else {
        printf("  FAIL : %-34s -> %s (want %s)\n", what, link_state_name(got), link_state_name(want));
        s_failures++;
    }
}

/* ONLINE_* 时 age 必须 <= 2：这是 logicprobe 验过的不变式 */
static void check_age_invariant(const char *where)
{
    link_state_t st = link_state();
    if (st == LINK_ONLINE_USB || st == LINK_ONLINE_WIFI) {
        uint32_t age = link_age_s();
        s_checks++;
        if (age <= 2) {
            printf("  ok   : invariant age=%u<=2 in %s (%s)\n", age, link_state_name(st), where);
        } else {
            printf("  FAIL : invariant age=%u>2 in %s (%s)\n", age, link_state_name(st), where);
            s_failures++;
        }
    }
}

static stats_t make_frame(uint32_t seq)
{
    stats_t s;
    memset(&s, 0, sizeof(s));
    s.seq = seq;
    s.cpu_pct = 12.5f;
    s.n_cores = 2;
    s.cores[0] = 10;
    s.cores[1] = 20;
    strlcpy(s.host, "TEST-PC", sizeof(s.host));
    return s;
}

static void feed_usb(uint32_t seq)
{
    stats_t s = make_frame(seq);
    link_on_frame(&s, LINK_ONLINE_USB);
}

static void feed_wifi(uint32_t seq)
{
    stats_t s = make_frame(seq);
    link_on_frame(&s, LINK_ONLINE_WIFI);
}

/* ---------------- 测试 1：状态机 ---------------- */

static void test_state_machine(void)
{
    printf("\n[1] state machine\n");

    CHECK(link_init() == ESP_OK, "link_init() == ESP_OK");
    check_state(LINK_INIT, "after init");

    feed_usb(1);
    check_state(LINK_INIT, "frame during INIT is dropped");
    CHECK(link_get_latest(NULL) == false, "link_get_latest(NULL) is false");
    stats_t tmp;
    CHECK(link_get_latest(&tmp) == false, "no cached frame before boot_done");

    link_boot_done();
    check_state(LINK_NO_LINK, "boot_done");

    feed_usb(2);
    check_state(LINK_ONLINE_USB, "usb frame from NO_LINK");
    CHECK(link_age_s() == 0, "age reset to 0 on frame");
    CHECK(link_frame_seq() == 1, "frame seq = 1");
    CHECK(link_get_latest(&tmp) == true && tmp.seq == 2, "cached frame seq matches");

    link_tick_1s();
    CHECK(link_age_s() == 1, "tick 1 -> age 1");
    check_age_invariant("after tick 1");
    link_tick_1s();
    CHECK(link_age_s() == 2, "tick 2 -> age 2");
    check_age_invariant("after tick 2");
    link_tick_1s();
    check_state(LINK_STALE, "tick 3 (age>=2) -> STALE");
    CHECK(link_age_s() == 3, "STALE age = 3");

    feed_wifi(3);
    check_state(LINK_ONLINE_WIFI, "wifi frame from STALE");

    feed_usb(4);
    check_state(LINK_ONLINE_USB, "usb frame preempts wifi");

    link_tick_1s();   /* age -> 1，用来验证 wifi 帧不会在 USB 在线时重置它 */
    uint32_t seq_before = link_frame_seq();
    uint32_t age_before = link_age_s();
    feed_wifi(5);
    check_state(LINK_ONLINE_USB, "wifi frame while USB online keeps USB state");
    CHECK(link_frame_seq() == seq_before, "wifi frame is IGNORED while USB online (model: no updates)");
    CHECK(link_age_s() == age_before, "wifi frame does not reset age while USB online");

    for (int i = 0; i < 3; i++) {
        link_tick_1s();
    }
    check_state(LINK_STALE, "3 ticks without frames -> STALE");

    feed_usb(6);
    check_state(LINK_ONLINE_USB, "usb frame recovers from STALE");

    link_on_link_lost();
    check_state(LINK_NO_LINK, "link_lost from ONLINE_USB -> NO_LINK");
    CHECK(link_age_s() == 0, "link_lost resets age");

    feed_wifi(7);
    check_state(LINK_ONLINE_WIFI, "wifi frame recovers from NO_LINK");

    link_sleep();
    check_state(LINK_SLEEP, "sleep -> SLEEP terminal");

    uint32_t seq_before_sleep = link_frame_seq();
    feed_usb(8);
    CHECK(link_frame_seq() == seq_before_sleep, "frame ignored in SLEEP");
    check_state(LINK_SLEEP, "still SLEEP after frame");

    link_tick_1s();
    check_state(LINK_SLEEP, "tick does not move SLEEP");
}

/* ---------------- 测试 2：解析真实上位机帧 ---------------- */

static char *read_file(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        return NULL;
    }
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (len <= 0) {
        fclose(f);
        return NULL;
    }
    char *buf = malloc((size_t)len + 1);
    if (buf == NULL) {
        fclose(f);
        return NULL;
    }
    size_t n = fread(buf, 1, (size_t)len, f);
    buf[n] = '\0';
    fclose(f);
    return buf;
}

static void test_parse_real_frame(const char *frame_path)
{
    printf("\n[2] parse real pcmond frame (%s)\n", frame_path);
    char *json = read_file(frame_path);
    if (json == NULL) {
        printf("  FAIL : cannot open %s\n", frame_path);
        s_failures++;
        s_checks++;
        return;
    }

    stats_t s;
    bool ok = link_parse_frame(json, strlen(json), &s);
    CHECK(ok, "link_parse_frame(real frame) == true");
    if (ok) {
        CHECK(s.seq > 0, "seq = %u", s.seq);
        CHECK(s.pc_time > 1600000000u, "t = %u (unix seconds)", s.pc_time);
        CHECK(s.uptime_s > 0, "up = %u s", s.uptime_s);
        CHECK(strcmp(s.host, "LAPTOP-4J1FBMVH") == 0, "host = %s", s.host);
        CHECK(s.cpu_pct > 0.0f && s.cpu_pct <= 100.0f, "cpu.avg = %.1f%%", s.cpu_pct);
        CHECK(s.cpu_mhz > 0, "cpu.mhz = %d MHz", s.cpu_mhz);
        CHECK(s.cpu_temp < 0.0f, "cpu.temp null -> %.1f (unknown)", s.cpu_temp);
        CHECK(s.n_cores == 24, "cpu.cores parsed = %d", s.n_cores);
        CHECK(s.mem_pct > 0.0f, "mem.pct = %.1f%%", s.mem_pct);
        CHECK(s.mem_used_mib > 0 && s.mem_total_mib > s.mem_used_mib,
              "mem %d/%d MiB", s.mem_used_mib, s.mem_total_mib);
        CHECK(s.has_gpu, "gpu present");
        CHECK(strncmp(s.gpu_name, "NVIDIA GeForce", 14) == 0, "gpu.name truncated = \"%s\" (%u chars)",
              s.gpu_name, (unsigned)strlen(s.gpu_name));
        CHECK(strlen(s.gpu_name) < 20, "gpu.name fits STATS_GPU_NAME_LEN (%u < 20)",
              (unsigned)strlen(s.gpu_name));
        CHECK(s.gpu_util >= 0, "gpu.util = %d%%", s.gpu_util);
        CHECK(s.gpu_temp >= 0, "gpu.temp = %d C", s.gpu_temp);
        CHECK(s.gpu_vram_total > 0, "gpu.vram %d/%d MiB", s.gpu_vram_used, s.gpu_vram_total);
        CHECK(s.gpu_power >= 0.0f, "gpu.power = %.1f W", s.gpu_power);
        CHECK(s.net_down >= 0.0f, "net.down = %.2f MiB/s", s.net_down);
        CHECK(s.disk_write >= 0.0f, "disk.write = %.2f MiB/s", s.disk_write);
        /* 老帧兼容：这份真实帧里没有 dsh 段，必须 valid=false 且不报错 */
        CHECK(!s.dsh.valid, "old frame without dsh -> dsh.valid == false");
    }
    free(json);
}

/* ---------------- 测试 3：解析边界 ---------------- */

static void test_parse_edges(void)
{
    printf("\n[3] parse edge cases\n");
    stats_t s;

    const char *gpu_null =
        "{\"v\":1,\"seq\":9,\"t\":1762761600,\"host\":\"PC\",\"up\":10,"
        "\"cpu\":{\"avg\":null,\"mhz\":null,\"temp\":null,\"cores\":[]},"
        "\"mem\":{\"pct\":50.0,\"used\":100,\"total\":200},"
        "\"gpu\":null,"
        "\"net\":{\"down\":1.0,\"up\":2.0},\"disk\":{\"read\":0.0,\"write\":0.0}}";
    bool ok = link_parse_frame(gpu_null, strlen(gpu_null), &s);
    CHECK(ok, "gpu:null / avg:null frame parses");
    CHECK(!s.has_gpu, "gpu null -> has_gpu = false");
    CHECK(s.cpu_pct == 0.0f, "cpu.avg null -> 0.0");
    CHECK(s.n_cores == 0, "empty cores -> n_cores = 0");
    CHECK(s.mem_total_mib == 200, "mem.total = 200");

    /* 40 个核：设备端只保留 32 个 */
    char big[2048];
    int off = snprintf(big, sizeof(big),
                       "{\"v\":1,\"seq\":1,\"cpu\":{\"avg\":1.0,\"cores\":[");
    for (int i = 0; i < 40; i++) {
        off += snprintf(big + off, sizeof(big) - off, "%d%s", i, (i == 39) ? "" : ",");
    }
    snprintf(big + off, sizeof(big) - off, "]}}");
    ok = link_parse_frame(big, strlen(big), &s);
    CHECK(ok, "40-core frame parses");
    CHECK(s.n_cores == 32, "cores truncated to STATS_MAX_CORES = %d", s.n_cores);

    const char *broken = "{\"v\":1,\"seq\":";
    ok = link_parse_frame(broken, strlen(broken), &s);
    CHECK(!ok, "truncated JSON -> false (dropped)");

    ok = link_parse_frame("[]", 2, &s);
    CHECK(!ok, "JSON array (not object) -> false");

    ok = link_parse_frame("", 0, &s);
    CHECK(!ok, "empty input -> false");

    /* 超范围占用率被夹到 0..100 */
    const char *clamp = "{\"v\":1,\"seq\":1,\"cpu\":{\"avg\":1,\"cores\":[-5,120,55]}}";
    ok = link_parse_frame(clamp, strlen(clamp), &s);
    CHECK(ok && s.n_cores == 3 && s.cores[0] == 0 && s.cores[1] == 100 && s.cores[2] == 55,
          "cores clamped to 0..100 -> [%u,%u,%u]",
          s.cores[0], s.cores[1], s.cores[2]);
}

/* ---------------- 测试 4：dsh 段（契约 §2.5，v1.1 可选） ---------------- */

static void test_parse_dsh(const char *frame_path)
{
    printf("\n[4] parse dsh section (%s)\n", frame_path);
    char *json = read_file(frame_path);
    if (json == NULL) {
        printf("  FAIL : cannot open %s\n", frame_path);
        s_failures++;
        s_checks++;
        return;
    }

    stats_t s;
    bool ok = link_parse_frame(json, strlen(json), &s);
    CHECK(ok, "frame with dsh parses");
    CHECK(s.dsh.valid, "dsh.valid == true when section present");
    CHECK(s.dsh.today_tok == 1280000ULL, "today.tok = %.0f", (double)s.dsh.today_tok);
    CHECK(s.dsh.today_in == 620000ULL, "today.in = %.0f", (double)s.dsh.today_in);
    CHECK(s.dsh.today_out == 96000ULL, "today.out = %.0f", (double)s.dsh.today_out);
    CHECK(s.dsh.today_cache == 566000ULL, "today.cache = %.0f", (double)s.dsh.today_cache);
    CHECK(s.dsh.today_req == 366U, "today.req = %u", s.dsh.today_req);
    CHECK(fabsf(s.dsh.today_cost - 1.23f) < 1e-3f, "today.cost = %.3f (want 1.23)", s.dsh.today_cost);
    CHECK(s.dsh.week_tok == 8800000ULL, "week.tok = %.0f", (double)s.dsh.week_tok);
    CHECK(s.dsh.week_req == 2100U, "week.req = %u", s.dsh.week_req);
    CHECK(s.dsh.week_cache_pct == 87U, "week.cache_pct = %u", s.dsh.week_cache_pct);
    CHECK(fabsf(s.dsh.week_cost - 8.40f) < 1e-3f, "week.cost = %.3f (want 8.40)", s.dsh.week_cost);
    CHECK(s.dsh.proc_count == 10U, "proc.count = %u", s.dsh.proc_count);
    CHECK(s.dsh.proc_rss_mb == 906U, "proc.rss_mb = %u", s.dsh.proc_rss_mb);
    CHECK(s.dsh.proc_cpu_pct == 12U, "proc.cpu_pct = %u", s.dsh.proc_cpu_pct);
    CHECK(s.dsh.proc_port_up == true, "proc.port_up = %s", s.dsh.proc_port_up ? "true" : "false");
    CHECK(s.n_cores == 24, "performance fields unaffected (n_cores = %d)", s.n_cores);
    free(json);

    /* int64 回归：token 超过 2^32 也必须原样，不能被 cJSON valueint(int) 截断。
     * 2^40 = 1099511627776，double 能精确表示。 */
    const char *big =
        "{\"v\":1,\"seq\":7,\"dsh\":{\"today\":{\"tok\":1099511627776},"
        "\"proc\":{\"port_up\":false}}}";
    ok = link_parse_frame(big, strlen(big), &s);
    CHECK(ok, "dsh frame with only partial fields parses");
    CHECK(s.dsh.today_tok == 1099511627776ULL,
          "int64 token survives (>2^32): today.tok = %.0f",
          (double)s.dsh.today_tok);
    CHECK(s.dsh.proc_port_up == false, "proc.port_up false parsed as %s",
          s.dsh.proc_port_up ? "true" : "false");
    CHECK(s.dsh.week_tok == 0ULL, "missing dsh fields default to 0 (week.tok = %.0f)",
          (double)s.dsh.week_tok);
}

int main(int argc, char **argv)
{
    /* run.sh 会把两个 fixture 的绝对路径传进来；手动跑时默认同目录。 */
    const char *frame_path = (argc > 1) ? argv[1] : "frame.json";
    const char *dsh_path = (argc > 2) ? argv[2] : "frame_dsh.json";

    printf("=== fw-link host test (real firmware/main/link/link.c) ===\n");
    test_state_machine();
    test_parse_real_frame(frame_path);
    test_parse_edges();
    test_parse_dsh(dsh_path);

    printf("\n=== %d checks, %d failure(s) ===\n", s_checks, s_failures);
    return (s_failures == 0) ? 0 : 1;
}
