@echo off
setlocal
chcp 65001 >nul

:: Executa a partir da pasta do script
cd /d "%~dp0"

echo [fieldmap-bot] Ativando ambiente...
if not exist .venv\Scripts\python.exe (
  echo Criando venv local...
  py -3 -m venv .venv || python -m venv .venv
)

call .venv\Scripts\activate
python -m pip install --upgrade pip >nul
pip install -r requirements.txt

if not exist .env (
  echo ATENCAO: .env nao encontrado. Copiando .env.example para .env...
  copy /Y .env.example .env >nul
)

:: HEADLESS=0 para acompanhar no navegador
set HEADLESS=0

:: Opcional: se Tesseract nao estiver no PATH, o extractor.py ja aponta para
::  C:\Program Files\Tesseract-OCR\tesseract.exe automaticamente no Windows.

python watcher.py

echo.
echo Finalizado. Pressione qualquer tecla para sair...
pause >nul
