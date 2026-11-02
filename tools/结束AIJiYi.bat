@echo off
rem 结束 AIJiYi 服务链路；QQ 客户端保持打开。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0lifecycle.ps1" -Action stop
