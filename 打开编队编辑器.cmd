@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
if exist "%~dp0python\python.exe" (
    "%~dp0python\python.exe" -X utf8 "%~dp0agent\battle_editor\launcher.py" %*
) else (
    if exist "%~dp0assets\interface.json" (
        python -X utf8 "%~dp0agent\battle_editor\launcher.py" %*
    ) else (
        echo [编队编辑器] 缺少随包 Python，请重新解压完整的 MaaFGO MXU 安装包。
        pause
        exit /b 1
    )
)
if errorlevel 1 (
    echo 启动失败，请查看上面的错误或 config\BattleEditor\service.log。
    pause
    exit /b 1
)
exit /b 0
