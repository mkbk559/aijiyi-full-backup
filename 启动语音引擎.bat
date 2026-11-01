@echo off
rem 检查/启动「定制语音」的本地转换器（自动识别 CLI 与 HTTP 两种接法）。
rem 幂等：已经就绪就直接返回；缺失只提示"会回退 edge"，不算失败。
rem 开机/重启后跑一次即可，或由「启动控制台.bat」自动调用。
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\ensure_voice_engine.ps1"
if errorlevel 1 (
  echo.
  echo 定制语音转换器没有就绪，语音会回退到 edge 音色。
  echo 配置见：data\voices\dafeiyu\profile.json 的 postprocess
  pause
)
