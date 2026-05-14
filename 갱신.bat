@echo off
chcp 65001 > nul
title 동두천시 재난안전지도 — 시트 → 지도 갱신

cd /d "%~dp0"

echo.
echo  ============================================================
echo    동두천시 재난안전지도 — 데이터 갱신
echo  ============================================================
echo.
echo   구글 시트에서 데이터를 받아 data.js 를 새로 만들고,
echo   GitHub 에 자동으로 올립니다. (Pages 가 1~2분 뒤 반영)
echo.

python tools\update_places.py
if errorlevel 1 (
  echo.
  echo  [오류] 스크립트 실행 실패. .env 와 시트 권한을 확인하세요.
  pause
  exit /b 1
)

echo.
echo  ------------------------------------------------------------
echo   GitHub 에 업로드 중...
echo  ------------------------------------------------------------

git add data.js
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "데이터 갱신 %DATE% %TIME:~0,5%"
  git push
  echo.
  echo   ✓ 업로드 완료. GitHub Pages 가 1~2분 뒤 반영됩니다.
) else (
  echo   · 시트에 변경사항이 없어 업로드 생략.
)

echo.
pause
