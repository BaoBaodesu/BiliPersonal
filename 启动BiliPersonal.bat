@echo off
chcp 65001 >nul
rem 始终使用脚本所在的项目根目录，不依赖当前工作目录。
cd /d "%~dp0"
if errorlevel 1 (
    echo [错误] 无法进入项目根目录。
    goto failed
)
if not exist ".venv\Scripts\python.exe" (
    echo [错误] 缺少项目虚拟环境，请按 README 的环境配置步骤创建 .venv。
    goto failed
)
where node >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Node.js，请安装后重新打开启动脚本。
    goto failed
)
where npm >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 npm，请检查 Node.js 安装与 PATH。
    goto failed
)
if not exist "frontend\node_modules" (
    echo [错误] 缺少前端依赖，请在 frontend 目录执行 npm ci。
    goto failed
)
rem 只检查端口，不结束其他进程，也不初始化后端数据。
".venv\Scripts\python.exe" -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', 8345)); s.close()" 2>nul
if errorlevel 1 (
    echo [错误] 无法使用本地端口 8345，请确认已有服务已停止且端口可用。
    goto failed
)
echo 正在构建 BiliPersonal 前端，请稍候……
pushd "frontend"
if errorlevel 1 (
    echo [错误] 无法进入 frontend 目录。
    goto failed
)
call npm run build
if errorlevel 1 (
    popd
    echo [错误] 前端构建失败，未启动后端，请查看上方错误。
    goto failed
)
popd
echo.
echo BiliPersonal：http://127.0.0.1:8345
echo 打开以上地址开始使用，按 Ctrl+C 停止服务。
".venv\Scripts\python.exe" run.py
if errorlevel 1 (
    echo [错误] 后端异常退出，请查看上方错误与 README 环境说明。
    goto failed
)
echo 服务已停止。
pause
exit /b 0

:failed
pause
exit /b 1
