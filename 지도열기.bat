@echo off
chcp 65001 > nul

REM 동두천시 재난안전지도를 브라우저로 바로 엽니다.
REM 서버가 켜져 있으면 localhost:8001 로, 아니면 로컬 파일로 엽니다.

cd /d "%~dp0"

REM 서버 살아있는지 빠르게 체크 (1초 타임아웃)
powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://localhost:8001/' -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop; exit 0 } catch { exit 1 }" > nul 2>&1

if %errorlevel% == 0 (
  start "" "http://localhost:8001/"
) else (
  start "" "index.html"
)
