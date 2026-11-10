@echo off
rem =====================================================================
rem  Fetch firmware dependency: LVGL 9.2.2 (pinned tag, shallow clone,
rem  docs/tests/demos trimmed). KEEP THIS FILE ASCII-ONLY (cp936 parsing).
rem
rem  Why not the IDF component manager (idf_component.yml)?
rem    On this machine idf.py's component manager cannot reach
rem    components-file.espressif.com (the same URL works from python, so it is
rem    a client/network flake). A pinned source copy under components/lvgl
rem    builds offline and reproduces exactly. See docs/journal.md.
rem
rem  Usage: tools\fetch_deps.cmd
rem =====================================================================
setlocal
set "DEST=%~dp0..\firmware\components\lvgl"
set "TAG=v9.2.2"
set "REPO=https://github.com/lvgl/lvgl.git"
set "TMPD=%TEMP%\lvgl_fetch_%RANDOM%%RANDOM%"

echo [fetch_deps] cloning %REPO% %TAG% ...
git clone --depth 1 --branch %TAG% %REPO% "%TMPD%"
if errorlevel 1 (
    echo [fetch_deps] git clone failed. Download %TAG% manually and put it in %DEST%
    exit /b 2
)

if exist "%DEST%" rmdir /s /q "%DEST%"
mkdir "%DEST%"
xcopy /e /i /q /y "%TMPD%\src" "%DEST%\src" >nul
mkdir "%DEST%\env_support" 2>nul
xcopy /e /i /q /y "%TMPD%\env_support\cmake" "%DEST%\env_support\cmake" >nul
rem examples/ and demos/ must EXIST but may be empty: lvgl's esp.cmake adds
rem both directories to the include path unconditionally.
mkdir "%DEST%\examples" 2>nul
mkdir "%DEST%\demos" 2>nul
for %%F in (CMakeLists.txt Kconfig lvgl.h lv_version.h lv_conf_template.h component.mk LICENCE.txt) do (
    copy /y "%TMPD%\%%F" "%DEST%\%%F" >nul
)
rmdir /s /q "%TMPD%"

echo [fetch_deps] done -^> %DEST%
