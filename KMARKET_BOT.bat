@echo off
rem ---------------------------------------------------------------------
rem  KMARKET Telegram bot launcher (@kmarketwowbot).
rem  PURE ASCII on purpose (see KMARKET.bat for the why). All human text
rem  is printed by Python, which is code-page independent.
rem
rem  Answers /price /verdict /season /events from your phone while this
rem  window is open. Close the window to stop the bot.
rem ---------------------------------------------------------------------
title KMARKET bot
cd /d "%~dp0"

python -m kmarket.bot
set EXITCODE=%ERRORLEVEL%

echo.
if not "%EXITCODE%"=="0" (
    echo   Exit code %EXITCODE%. If Python complains about missing modules:
    echo       pip install -r requirements.txt
    echo.
)
pause
