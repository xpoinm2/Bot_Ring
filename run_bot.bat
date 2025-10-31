@echo off
setlocal enabledelayedexpansion

REM Ensure the script works relative to its own directory
cd /d "%~dp0"

REM Detect which Python interpreter the "py" launcher resolves to
for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%i"

if not defined PYTHON_EXE (
    echo Не удалось выполнить команду "py -3".
    echo Установите официальную версию Python с сайта https://www.python.org/downloads/ и убедитесь,
    echo что при установке была отмечена опция "Add Python to PATH".
    echo.
    goto END
)

echo Используется интерпретатор Python: !PYTHON_EXE!
echo.

echo !PYTHON_EXE! ^| findstr /I "inkscape" >nul
if not errorlevel 1 (
    echo Обнаружено, что команда py указывает на Python, встроенный в стороннее приложение.
    echo Такой Python не поставляется с готовыми колёсами для зависимостей вроде aiohttp, поэтому установка завершается ошибкой.
    echo.
    echo Установите официальную версию Python с сайта https://www.python.org/downloads/, отметьте опцию "Add Python to PATH"
    echo и запустите этот скрипт заново. Тогда виртуальное окружение будет создано автоматически.
    echo.
    goto END
)

REM Create a virtual environment if it does not exist
if not exist .venv (
    echo Создаю виртуальное окружение...
    py -3 -m venv .venv
    if errorlevel 1 goto END
)

REM Activate the virtual environment
call .venv\Scripts\activate.bat
if errorlevel 1 goto END

REM Upgrade pip and install dependencies inside the virtual environment
python -m pip install --upgrade pip
if errorlevel 1 goto END

python -m pip install -r requirements.txt
if errorlevel 1 goto END

REM Run the bot
python Main.py

:END
echo.
pause