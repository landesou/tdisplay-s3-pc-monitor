@echo off
rem =====================================================================
rem  Fetch firmware dependency: LVGL v9.2.2 (pinned), trimmed copy.
rem  KEEP THIS FILE ASCII-ONLY (cmd.exe parses .cmd as cp936 and garbles UTF-8).
rem
rem  Why not the IDF component manager (idf_component.yml)?
rem    On this machine idf.py's component manager cannot reach
rem    components-file.espressif.com (the same URL works from python), so a
rem    pinned source copy under components/lvgl builds offline and reproduces.
rem
rem  Why tarball first, git clone second?
rem    GitHub HTTPS clones on this network die with
rem    "RPC failed; curl 56 schannel: server closed abruptly" often enough to be
rem    useless in a setup script. A single tarball through a mirror is smaller,
rem    faster and resumable by simple retry. Order tried:
rem      1) tarball via gh-proxy.com mirror
rem      2) tarball direct from codeload.github.com
rem      3) git clone via gh-proxy.com
rem      4) git clone direct
rem
rem  Usage: tools\fetch_deps.cmd
rem =====================================================================
setlocal
set "DEST=%~dp0..\firmware\components\lvgl"
set "TAG=v9.2.2"
set "TARBALL=lvgl-%TAG%.tar.gz"
set "MIRROR=https://gh-proxy.com/"
set "WORK=%TEMP%\lvgl_fetch_%RANDOM%%RANDOM%"
set "PACK=%WORK%\lvgl.tar.gz"

if not defined TEMP set "TEMP=%TMP%"
mkdir "%WORK%" 2>nul

echo [fetch_deps] target LVGL %TAG% -^> %DEST%

rem ---------- 1) tarball via mirror ----------
set "URL=%MIRROR%https://github.com/lvgl/lvgl/archive/refs/tags/%TARBALL%"
echo [fetch_deps] try 1: %URL%
curl.exe -L --fail --connect-timeout 15 --max-time 300 -o "%PACK%" "%URL%"
if not errorlevel 1 goto have_pack

rem ---------- 2) tarball direct ----------
set "URL=https://codeload.github.com/lvgl/lvgl/tar.gz/refs/tags/%TAG%"
echo [fetch_deps] try 2: %URL%
curl.exe -L --fail --connect-timeout 15 --max-time 300 -o "%PACK%" "%URL%"
if not errorlevel 1 goto have_pack

rem ---------- 3) git clone via mirror ----------
set "CLONE_URL=%MIRROR%https://github.com/lvgl/lvgl.git"
echo [fetch_deps] try 3: git clone %CLONE_URL%
git clone --depth 1 --branch %TAG% "%CLONE_URL%" "%WORK%\src_clone" >nul 2>&1
if not errorlevel 1 goto have_clone

rem ---------- 4) git clone direct ----------
echo [fetch_deps] try 4: git clone https://github.com/lvgl/lvgl.git
git clone --depth 1 --branch %TAG% https://github.com/lvgl/lvgl.git "%WORK%\src_clone" >nul 2>&1
if not errorlevel 1 goto have_clone

echo [fetch_deps] FATAL: all 4 sources failed. Network to GitHub is down.
echo   Manual fallback: download https://github.com/lvgl/lvgl/archive/refs/tags/%TARBALL%
echo   and extract its contents into %DEST%
rmdir /s /q "%WORK%" 2>nul
exit /b 2

:have_pack
echo [fetch_deps] extracting %TARBALL% ...
tar -xzf "%PACK%" -C "%WORK%"
if errorlevel 1 (
    echo [fetch_deps] FATAL: tar failed to extract %PACK%
    rmdir /s /q "%WORK%" 2>nul
    exit /b 3
)
rem the archive unpacks into a single directory lvgl-9.2.2/
for /d %%D in ("%WORK%\lvgl-*") do set "SRC=%%D"
if not defined SRC (
    echo [fetch_deps] FATAL: unexpected archive layout under %WORK%
    rmdir /s /q "%WORK%" 2>nul
    exit /b 3
)
goto assemble

:have_clone
set "SRC=%WORK%\src_clone"

:assemble
if exist "%DEST%" rmdir /s /q "%DEST%"
mkdir "%DEST%"
xcopy /e /i /q /y "%SRC%\src" "%DEST%\src" >nul
mkdir "%DEST%\env_support" 2>nul
xcopy /e /i /q /y "%SRC%\env_support\cmake" "%DEST%\env_support\cmake" >nul
rem examples/ and demos/ must EXIST but may be empty: lvgl's esp.cmake adds both
rem directories to the include path unconditionally.
mkdir "%DEST%\examples" 2>nul
mkdir "%DEST%\demos" 2>nul
for %%F in (CMakeLists.txt Kconfig lvgl.h lv_version.h lv_conf_template.h component.mk LICENCE.txt) do (
    copy /y "%SRC%\%%F" "%DEST%\%%F" >nul
)
rmdir /s /q "%WORK%" 2>nul

rem ---------- sanity check ----------
if not exist "%DEST%\lvgl.h" (
    echo [fetch_deps] FATAL: lvgl.h missing after assembly
    exit /b 4
)
if not exist "%DEST%\src\lv_init.c" (
    echo [fetch_deps] FATAL: src/lv_init.c missing after assembly
    exit /b 4
)
echo [fetch_deps] done: %DEST%
exit /b 0
