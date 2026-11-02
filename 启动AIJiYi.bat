@echo off
rem 启动大肥鱼深海控制台（无控制台窗口，后台常驻）。
rem 地址：http://127.0.0.1:8787/    日志：data\console.out.log / console.err.log
rem 关闭：任务管理器结束对应 python.exe，或运行 tools\stop_console.bat。
rem
rem 同时拉起「定制语音」引擎（Seed-VC 9880）：开机/重启后不用再手动点。
rem 只影响当前声线需要本地转换器的情况；已在跑则直接跳过。
cd /d "%~dp0"
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath 'D:\Python313\python.exe' -ArgumentList '-m','aiji.cli','serve' -WorkingDirectory '%~dp0' -WindowStyle Hidden -RedirectStandardOutput '%~dp0data\console.out.log' -RedirectStandardError '%~dp0data\console.err.log'"
rem 语音引擎单独后台跑，加载模型要几十秒，不挡页面打开。
start "" /min powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "& '%~dp0tools\ensure_voice_engine.ps1' *>> '%~dp0data\voice-engine.log'"
timeout /t 2 >nul
start "" "http://127.0.0.1:8787/"
