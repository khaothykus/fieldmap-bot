@echo off
setlocal
echo [fieldmap-bot] Ativando ambiente...
call .\.venv\Scripts\activate.bat
python -m pip install -r requirements.txt >NUL

REM Ajuste o intervalo em minutos como quiser (ex.: 15)
python watcher.py --retry-interval 15

echo.
echo Finalizado. Pressione qualquer tecla para sair...
pause >NUL
