@echo off
rem Запуск дашборда KMARKET. Ярлык на рабочем столе указывает сюда.
rem Окно консоли намеренно остаётся открытым: в нём видно адрес и ошибки,
rem а закрытие окна останавливает сервер — это и есть кнопка «выключить».
title KMARKET
cd /d "%~dp0"
python -m kmarket.web
if errorlevel 1 (
  echo.
  echo Что-то пошло не так. Проверь, что зависимости стоят:
  echo     pip install -r requirements.txt
  echo.
  pause
)
