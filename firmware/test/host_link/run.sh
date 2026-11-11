#!/usr/bin/env bash
# =====================================================================
#  host_link 离线测试
#
#  用主机 C 编译器把固件里**真实的 firmware/main/link/link.c** 编译出来
#  （配 shim 头文件 + ESP-IDF 自带的 cJSON），在 PC 上直接跑状态机与解析器断言。
#  板子不在手边时，这是唯一能真正跑一遍链路层逻辑的手段。
#
#  用法（任意目录都行，脚本自己定位仓库）：
#      bash firmware/test/host_link/run.sh
#      GCC=/path/to/gcc.exe bash firmware/test/host_link/run.sh
#
#  依赖：
#    - 主机 C 编译器：$GCC → gcc → clang，三者都找不到就报错退出（不猜路径）
#    - ESP-IDF 源码里的 cJSON：$IDF_PATH/components/json/cJSON，或常见安装位置
#  产物：firmware/test/host_link/build/test_link.exe（已被 .gitignore 忽略）
# =====================================================================
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
PROJ_ROOT=$(cd "$SCRIPT_DIR/../../.." && pwd)
FW_MAIN="$PROJ_ROOT/firmware/main"
BUILD_DIR="$SCRIPT_DIR/build"
EXE="$BUILD_DIR/test_link.exe"

# ---------- 1. 选编译器（不硬编码本机路径）----------
if [ -n "${GCC:-}" ]; then
    CC="$GCC"
elif command -v gcc >/dev/null 2>&1; then
    CC=$(command -v gcc)
elif command -v clang >/dev/null 2>&1; then
    CC=$(command -v clang)
else
    echo "错误：找不到主机 C 编译器。"
    echo "  Windows：装 MinGW-w64（scoop install mingw / MSYS2），或设 GCC=/path/to/gcc.exe"
    echo "  Linux/macOS：装 gcc 或 clang 即可"
    exit 2
fi
[ -x "$CC" ] || { echo "错误：GCC=$CC 不可执行"; exit 2; }

# ---------- 2. 找 ESP-IDF 的 cJSON ----------
find_cjson() {
    if [ -n "${IDF_PATH:-}" ] && [ -f "$IDF_PATH/components/json/cJSON/cJSON.c" ]; then
        echo "$IDF_PATH/components/json/cJSON"
        return 0
    fi
    local candidate
    for candidate in \
        "D:/ESP32-S3/Espressif/frameworks/esp-idf-v5.5.3" \
        "$HOME/esp/esp-idf" \
        "$HOME/.espressif/frameworks/esp-idf" \
        "/opt/esp-idf"; do
        if [ -f "$candidate/components/json/cJSON/cJSON.c" ]; then
            echo "$candidate/components/json/cJSON"
            return 0
        fi
    done
    return 1
}

CJSON_DIR=$(find_cjson) || {
    echo "错误：找不到 ESP-IDF 的 cJSON（components/json/cJSON/cJSON.c）。"
    echo "  请设置 IDF_PATH 指向你的 esp-idf 目录，例如："
    echo "    IDF_PATH=D:/ESP32-S3/Espressif/frameworks/esp-idf-v5.5.3 bash firmware/test/host_link/run.sh"
    exit 2
}

# ---------- 3. 编译 ----------
mkdir -p "$BUILD_DIR"
echo "compiler : $CC"
echo "esp-idf  : $(dirname "$(dirname "$(dirname "$CJSON_DIR")")")"
echo "build    : $BUILD_DIR"

"$CC" -std=gnu17 -O1 -g -Wall -Wextra -Wno-unused-parameter \
    -include "$SCRIPT_DIR/shim/compat.h" \
    -I "$SCRIPT_DIR/shim" \
    -I "$FW_MAIN" \
    -I "$FW_MAIN/link" \
    -I "$CJSON_DIR" \
    "$SCRIPT_DIR/test_link.c" \
    "$FW_MAIN/link/link.c" \
    "$CJSON_DIR/cJSON.c" \
    -o "$EXE" -lm

# ---------- 4. 跑 ----------
# argv[1] = 老帧（无 dsh，测兼容）；argv[2] = 带 dsh 的帧（测新段解析）
cd "$BUILD_DIR"
exec "./$(basename "$EXE")" "$SCRIPT_DIR/frame.json" "$SCRIPT_DIR/frame_dsh.json"
