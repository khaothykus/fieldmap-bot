#!/usr/bin/env bash
set -euo pipefail

APP_HOME="$HOME"
APP_DIR="$APP_HOME/fieldmap-bot"
VENV_DIR="$APP_DIR/.venv"
GECKO_VERSION="v0.35.0"
GECKO_PATH="/usr/local/bin/geckodriver"

echo "==> [1/6] Pacotes do sistema..."
sudo DEBIAN_FRONTEND=noninteractive apt update -y
sudo DEBIAN_FRONTEND=noninteractive apt install -y \
  python3 python3-venv python3-pip \
  tesseract-ocr tesseract-ocr-por libtesseract-dev \
  firefox-esr ffmpeg libsm6 libxext6 \
  ca-certificates curl wget tar gzip unzip

echo "==> [2/6] geckodriver..."
# 1) se já tem, usa
if [ -x "$GECKO_PATH" ]; then
  echo "   -> geckodriver já existe em $GECKO_PATH, usando esse."
else
  # 2) tenta via apt
  if sudo apt-get install -y geckodriver >/dev/null 2>&1; then
    echo "   -> geckodriver instalado via apt."
  else
    echo "   -> geckodriver NÃO está no apt, baixando release ${GECKO_VERSION} do GitHub..."
    ARCH="$(uname -m)"
    case "$ARCH" in
      aarch64|arm64) WANT_PAT="linux-aarch64" ;;
      armv7l)        WANT_PAT="linux-arm7hf" ;;
      x86_64|amd64)  WANT_PAT="linux64" ;;
      *)
        echo "Arquitetura $ARCH não suportada automaticamente para geckodriver." >&2
        exit 1
        ;;
    esac

    TMPDIR="$(mktemp -d)"
    pushd "$TMPDIR" >/dev/null
    ASSET="geckodriver-${GECKO_VERSION}-${WANT_PAT}.tar.gz"
    URL="https://github.com/mozilla/geckodriver/releases/download/${GECKO_VERSION}/${ASSET}"
    echo "   -> baixando $URL"
    curl -fsSLO "$URL"
    tar -xzf "$ASSET"
    sudo mv -f geckodriver "$GECKO_PATH"
    sudo chmod +x "$GECKO_PATH"
    popd >/dev/null
    rm -rf "$TMPDIR"
    echo "   -> geckodriver instalado em $GECKO_PATH"
  fi
fi

echo "==> [3/6] Criando venv em $VENV_DIR ..."
mkdir -p "$APP_HOME/.venvs"
python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --upgrade pip

REQ_FILE="$APP_DIR/requirements.txt"
if [ -f "$REQ_FILE" ]; then
  pip install -r "$REQ_FILE"
else
  # fallback mínimo
  pip install python-telegram-bot==21.7
fi
deactivate

echo "==> [4/6] Criando pastas de comprovantes..."
mkdir -p \
  "$APP_DIR" \
  "$APP_DIR/comprovantes" \
  "$APP_DIR/comprovantes_processados" \
  "$APP_DIR/comprovantes_falhos"

echo "==> [5/6] Instalando service systemd..."
sudo tee /etc/systemd/system/fieldmap-bot.service >/dev/null <<'EOF'
[Unit]
Description=Fieldmap Bot (OCR + Selenium)
After=network-online.target
Wants=network-online.target

[Service]
Environment=OCR_DEBUG=0
Environment=OCR_DEBUG_DIR=/home/pi/fieldmap-bot/_ocr_debug
Type=simple
User=pi
Group=pi
WorkingDirectory=/home/pi/fieldmap-bot

# Carrega variáveis do .env (o "-" evita falha se o arquivo não existir)
EnvironmentFile=-/home/pi/fieldmap-bot/.env

# Ambiente
Environment=HEADLESS=1
Environment=PYTHONUNBUFFERED=1
# Garante PATH com geckodriver/firefox padrão
Environment=PATH=/usr/local/bin:/usr/bin:/bin

# Início do bot
ExecStart=/home/pi/fieldmap-bot/.venv/bin/python -u /home/pi/fieldmap-bot/watcher.py

# Reinício resiliente
Restart=on-failure
RestartSec=5
TimeoutStopSec=20
KillMode=process

# Logs: ficam no journal (journalctl). Se quiser arquivo, descomente as 2 linhas abaixo
# StandardOutput=append:/home/pi/fieldmap-bot/fieldmap-bot.log
# StandardError=append:/home/pi/fieldmap-bot/fieldmap-bot.log

[Install]
WantedBy=multi-user.target
EOF

echo "==> [6/6] Habilitando e (opcionalmente) iniciando..."
sudo systemctl daemon-reload
sudo systemctl enable fieldmap-bot.service
# se quiser já subir agora:
sudo systemctl start fieldmap-bot.service || true

echo
echo "✅ fieldmap-bot instalado."
echo "• Código:     $APP_DIR"
echo "• venv:       $VENV_DIR"
echo "• geckodriver: $GECKO_PATH"
echo "• serviço:    fieldmap-bot.service"
echo
echo "Logs em tempo real:"
echo "  sudo journalctl -u fieldmap-bot.service -f"
