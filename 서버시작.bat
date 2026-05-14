@echo off
chcp 65001 > nul
title 동두천시 재난안전지도 — 사내망 서버

cd /d "%~dp0"

echo.
echo  동두천시 재난안전지도 서버를 시작합니다...
echo.

python tools\server.py

if errorlevel 1 (
  echo.
  echo  [오류] 서버 실행에 실패했습니다.
  echo        Python이 설치되어 있는지 확인하세요.
  pause
)
