@echo off
rem 停止监听 8787 的控制台进程（按端口找 PID，只杀 LISTENING 的那一个）。
setlocal enabledelayedexpansion
set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8787" ^| findstr "LISTENING"') do (
  set FOUND=1
  echo stopping console pid %%a
  taskkill /PID %%a /F >nul
)
if "!FOUND!"=="0" echo console is not running on 8787
endlocal
