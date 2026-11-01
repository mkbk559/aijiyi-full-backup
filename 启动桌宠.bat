@echo off
rem 启动大肥鱼桌宠（无控制台窗口）。关掉：右键鱼 -> 退出。
cd /d "%~dp0"
start "" "D:\Python313\pythonw.exe" -m aiji.pet
