@echo off
chcp 65001 >nul
cd /d D:\AIJiYi
set PYTHONIOENCODING=utf-8
echo === 抖音扫码登录 ===
echo 会弹出一个 Edge 窗口（抖音首页 + 登录二维码），用手机抖音扫码。
echo 二维码截图同时落在 data\temp\douyin-login.png。
echo 登录成功后本窗口会打印 {"is_login": true}，之后 aiji social douyin feed 就能用了。
echo.
.venv\Scripts\python.exe -m aiji.senses.cli social douyin login --wait 300
echo.
echo 退出码 %ERRORLEVEL%
pause
