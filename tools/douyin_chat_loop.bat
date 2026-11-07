@echo off
chcp 65001 >nul
cd /d D:\AIJiYi
set PYTHONIOENCODING=utf-8
rem 抖音群聊的"主动聊天"循环（和 QQ 那边 AstrBot 插件同一个 aiji 服务、同一个人格/记忆）。
rem 用法：双击运行；或 tools\douyin_chat_loop.bat "玖玖大王万岁" 45
set GROUP=%~1
if "%GROUP%"=="" set GROUP=玖玖大王万岁
set INTERVAL=%~2
if "%INTERVAL%"=="" set INTERVAL=45
echo [douyin] 群=%GROUP% 每 %INTERVAL% 秒看一次；Ctrl+C 结束
:loop
.venv\Scripts\python.exe -m aiji.senses.cli social douyin watch --peer "%GROUP%" --once --yes
timeout /t %INTERVAL% /nobreak >nul
goto loop
