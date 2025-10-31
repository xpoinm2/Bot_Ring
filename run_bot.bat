@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM === Find a working Python ===
set "PYCMD="

REM 1) Prefer 'python' if present (your 3.13 should be here)
where python >nul 2>nul
if %errorlevel%==0 (
  set "PYCMD=python"
) else (
  REM 2) Try py launcher with specific versions
  for %%V in (3.13 3.12 3.11 3) do (
    py -%%V -V >nul 2>nul
    if !errorlevel! EQU 0 (
      set "PYCMD=py -%%V"
      goto :HAVE_PY
    )
  )
)

:HAVE_PY
if not defined PYCMD (
  echo [ERROR] Python не найден. Установи Python 3.11+ и добавь его в PATH.
  echo         Или установи py-launcher и нормальную привязку версий.
  pause
  exit /b 1
)

REM === Create venv if needed ===
if not exist ".venv" (
  echo [INFO] Создаю виртуальное окружение .venv ...
  %PYCMD% -m venv .venv
  if %errorlevel% NEQ 0 (
    echo [ERROR] Не удалось создать виртуальное окружение с помощью: %PYCMD%
    echo         Проверь, что этот Python установлен корректно.
    pause
    exit /b 1
  )
)

REM === Activate venv ===
call ".venv\Scripts\activate.bat"
if %errorlevel% NEQ 0 (
  echo [ERROR] Не удалось активировать .venv
  pause
  exit /b 1
)

REM === Upgrade pip and install deps ===
python -m pip install --upgrade pip
if not exist requirements.txt (
  echo aiogram^>=3.4,^<4> requirements.txt
)
pip install -r requirements.txt
if %errorlevel% NEQ 0 (
  echo [ERROR] Не удалось установить зависимости из requirements.txt
  pause
  exit /b 1
)

REM === Check ffmpeg ===
where ffmpeg >nul 2>nul
if %errorlevel% NEQ 0 (
  echo [WARN] ffmpeg не найден в PATH. Установи ffmpeg и перезапусти.
  pause
  exit /b 1
)

REM === Set bot token (your token) ===
set "BOT_TOKEN=7964488864:AAEVEbs9zWzipTgNR3HMIKAw1pR6Hpg8qyM"

REM === Run bot ===
echo [INFO] Запускаю бота...
python bot.py

echo.
echo [INFO] Бот завершил работу. Нажмите любую клавишу для выхода.
pause
