#!/usr/bin/env bash
set -euo pipefail

sudo apt update
sudo apt install -y python3-venv python3-pip tesseract-ocr tesseract-ocr-por libtesseract-dev firefox-esr geckodriver ffmpeg libsm6 libxext6

mkdir -p ~/.venvs
python3 -m venv ~/.venvs/ocrbot
source ~/.venvs/ocrbot/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

mkdir -p "$HOME/fieldmap-bot" "$HOME/comprovantes" "$HOME/comprovantes_processados" "$HOME/comprovantes_falhos"

cat >/tmp/ocrwatcher.service <<'UNIT'
[Unit]
Description=Watcher OCR portal (FieldMap)
After=network-online.target

[Service]
Type=simple
User=%i
WorkingDirectory=%h/fieldmap-bot
Environment=HEADLESS=1
Environment=LOG_PATH=%h/fieldmap-bot/ocrbot.log
ExecStart=%h/.venvs/ocrbot/bin/python watcher.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT

echo "Para instalar o serviço systemd manualmente, salve /tmp/ocrwatcher.service em /etc/systemd/system/ocrwatcher.service e ative com:"
echo "  sudo cp /tmp/ocrwatcher.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload && sudo systemctl enable --now ocrwatcher.service"

echo "Pronto. Para validar visualmente, rode:"
echo "  HEADLESS=0 python watcher.py"
