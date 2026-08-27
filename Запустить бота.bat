@echo off
title Nebo bot

rem Run from the folder this file sits in, whatever the current directory is.
cd /d "%~dp0"

echo.
echo   Nebo bot - starting the control panel...
echo   It opens in your browser. Keep this window open.
echo.

rem The bot logs Russian text from the game; the console needs UTF-8 for it.
chcp 65001 >nul

python main.py --panel
set RESULT=%errorlevel%

if not "%RESULT%"=="0" (
  echo.
  echo   Failed. Usually this helps:
  echo       pip install -r requirements.txt
  echo.
)
pause
