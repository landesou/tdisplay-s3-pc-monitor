#!/usr/bin/env bash
# =====================================================================
#  在 bash（Git Bash / MSYS2）里安全调用 cmd 构建脚本。
#
#  为什么需要这层包装：MSYS 会自动改写以 "/" 开头的参数、并对环境变量里的
#  路径做转换，直接 `cmd //c xxx.cmd` 会被吃掉字符。见 docs/journal.md。
#
#  用法：
#    bash tools/build.sh              # 构建
#    bash tools/build.sh menuconfig   # 其它 idf.py 动作
#    bash tools/build.sh flash COM5   # 烧录
#    BUILD_DIR=build-foo bash tools/build.sh   # 独立 build 目录（多人并行时用）
# =====================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_WIN="$(cygpath -w "$HERE/idf_build.cmd")"

export MSYS_NO_PATHCONV=1
exec cmd.exe /c "$SCRIPT_WIN" "$@"
