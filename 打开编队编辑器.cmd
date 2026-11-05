@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "EDITOR_PYTHON="
set "EDITOR_OLD_PYTHON="

if not exist "%~dp0agent\battle_editor\launcher.py" goto missing_entry
if exist "%~dp0python\python.exe" (
    "%~dp0python\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 2)" >nul 2>&1
    if errorlevel 1 goto broken_bundle_python
    set "EDITOR_PYTHON=%~dp0python\python.exe"
    goto launch_python
)
rem A release must not silently borrow a different installation or system Python.
if exist "%~dp0interface.json" goto missing_bundle_python
if not exist "%~dp0assets\interface.json" goto missing_entry

rem Source checkouts may use an existing local virtual environment.
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 2)" >nul 2>&1
    if not errorlevel 1 (
        set "EDITOR_PYTHON=%~dp0.venv\Scripts\python.exe"
        goto launch_python
    )
    if errorlevel 2 if not errorlevel 3 set "EDITOR_OLD_PYTHON=1"
)
if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 2)" >nul 2>&1
    if not errorlevel 1 (
        set "EDITOR_PYTHON=%~dp0venv\Scripts\python.exe"
        goto launch_python
    )
    if errorlevel 2 if not errorlevel 3 set "EDITOR_OLD_PYTHON=1"
)

rem Standard installer locations also work with an old or restricted GUI PATH.
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%ProgramW6432%\Python312\python.exe" "%ProgramFiles%\Python312\python.exe") do (
    if exist "%%~P" (
        "%%~P" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 2)" >nul 2>&1
        if not errorlevel 1 (
            set "EDITOR_PYTHON=%%~P"
            goto launch_python
        )
        if errorlevel 2 if not errorlevel 3 set "EDITOR_OLD_PYTHON=1"
    )
)

rem The Python launcher can locate a registered install even when Explorer's
rem PATH contains only the Microsoft Store execution alias.
py -3.12 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 2)" >nul 2>&1
if not errorlevel 1 goto launch_py312
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 2)" >nul 2>&1
if not errorlevel 1 goto launch_py3
if errorlevel 2 if not errorlevel 3 set "EDITOR_OLD_PYTHON=1"

rem Probe each real executable: a broken Store alias must not mask a later one.
for /f "delims=" %%P in ('"%SystemRoot%\System32\where.exe" python.exe 2^>nul') do (
    "%%P" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 2)" >nul 2>&1
    if not errorlevel 1 (
        set "EDITOR_PYTHON=%%P"
        goto launch_python
    )
    if errorlevel 2 if not errorlevel 3 set "EDITOR_OLD_PYTHON=1"
)
if defined EDITOR_OLD_PYTHON goto old_dev_python

rem Keep each localized error branch on one physical line: cmd.exe can
rem truncate later UTF-8 echo lines after a goto.

echo [编队编辑器] 源码目录未找到可用的 Python 3.12 或更高版本，编辑器尚未启动。 & echo 普通使用请解压完整 MaaFGO MXU 安装包，双击包内的“打开编队编辑器.cmd”。 & echo 开发使用请安装 Python 3.12，安装后重新打开终端；也可在项目的 .venv 中准备 Python 环境后重试。 & goto failed

:old_dev_python
echo [编队编辑器] 检测到的 Python 版本低于 3.12，无法运行当前编辑器。 & echo 请安装 Python 3.12 或更新项目的 .venv 环境后重试；普通使用也可直接打开完整 MXU 包内的启动脚本。 & goto failed

:missing_entry
echo [编队编辑器] 当前目录缺少编辑器入口或工程配置，无法启动。 & echo 请从完整 MaaFGO 目录运行本脚本，不要单独复制 CMD；也可重新解压完整 MXU 安装包。 & goto failed

:missing_bundle_python
echo [编队编辑器] 安装目录缺少 python\python.exe，无法启动随包编辑器。 & echo 请重新解压完整 MaaFGO MXU 安装包到新目录，再双击包内的“打开编队编辑器.cmd”。 & goto failed

:broken_bundle_python
echo [编队编辑器] 随包 Python 未能正常运行，编辑器尚未启动。 & echo 请重新解压完整 MaaFGO MXU 安装包到新目录后重试；不要混用不同版本的 Python 文件。 & goto failed

:launch_python
"%EDITOR_PYTHON%" -X utf8 "%~dp0agent\battle_editor\launcher.py" %*
goto launch_result

:launch_py312
py -3.12 -X utf8 "%~dp0agent\battle_editor\launcher.py" %*
goto launch_result

:launch_py3
py -3 -X utf8 "%~dp0agent\battle_editor\launcher.py" %*

:launch_result
if not errorlevel 1 exit /b 0
echo [编队编辑器] Python 已启动，但编辑器未能成功运行。请先按上方错误处理后重试。 & if exist "%~dp0config\BattleEditor\service.log" (echo 如需反馈，请附上本安装目录的 config\BattleEditor\service.log。) & goto failed

:failed
pause
exit /b 1
