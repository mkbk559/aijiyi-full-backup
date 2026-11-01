@echo off
rem 启动「定制语音」引擎（Seed-VC，127.0.0.1:9880）。
rem 幂等：已经在跑就直接返回。开机/重启后跑一次即可，或由「启动控制台.bat」自动调用。
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\ensure_voice_engine.ps1"
if errorlevel 1 (
  echo.
  echo 定制语音引擎没有起来。请查看：
  echo   voice-engine\seed-vc\aiji-seed-vc.err.log
  pause
)
