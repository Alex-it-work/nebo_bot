@echo off
title Nebo bot

rem Run from the folder this file sits in, whatever the current directory is.
cd /d "%~dp0"

echo.
echo   Запускаю панель управления...
echo   Она откроется в браузере сама. Это окно не закрывайте.
echo.

rem The bot logs Russian text from the game; the console needs UTF-8 for it.
chcp 65001 >nul
python main.py --panel
set RESULT=%errorlevel%
chcp 866 >nul

if not "%RESULT%"=="0" (
  echo.
  echo   Не получилось. Обычно помогает установить зависимости:
  echo       pip install -r requirements.txt
  echo.
)
pause
