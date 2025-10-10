# 🧾 FieldMap Bot (OCR + Selenium)

Automação completa para lançamento automático de **pedágios** e **estacionamentos** no portal **FieldMap Mobile** a partir de comprovantes (imagens ou PDFs) via OCR.

Desenvolvido para rodar de forma **headless** (sem interface gráfica) em **Raspberry Pi 5** com **Firefox ESR**, mas compatível com qualquer Linux moderno.

---

## ⚙️ Funcionalidades principais

- 📂 **Monitoramento automático** da pasta `comprovantes/`
- 🔎 **OCR inteligente** (via `ocr_utils.py`) detecta tipo, valor e data
- 🧠 **Dedupe físico e semântico**:
  - Evita reprocessar o mesmo arquivo (hash SHA256)
  - Evita duplicar lançamentos com mesmo tipo/data/valor
- 🌐 **Integração Selenium + FieldMap**:
  - Login automático
  - Localiza deslocamento correto conforme tipo:
    - `pedágio` → dentro da janela [início, fim]
    - `estacionamento` → entre [fim atual, início próximo]
  - Preenche e anexa comprovante automaticamente
- 🔁 **Retry inteligente**:
  - Reprocessa falhas de forma segura e incremental (`retry_falhos.py`)
- 🧰 **Gerenciamento do ledger**:
  - `manage_ledger.py` para listar, buscar, limpar e auditar lançamentos
- 🧱 **Sistema de logs detalhado** para diagnóstico

---

## 📁 Estrutura de pastas

fieldmap-bot/
├── watcher.py # Loop principal (monitoramento + OCR + upload)
├── portal_client.py # Lógica Selenium para o portal
├── ocr_utils.py # Extração OCR (tipo/data/valor)
├── dedupe.py # Banco SQLite de deduplicação
├── retry_falhos.py # Reprocesso de falhas com backoff
├── manage_ledger.py # Utilitário CLI para manutenção do ledger
├── config.yaml # Seletor CSS e URLs do portal
├── comprovantes/ # Entrada de novos comprovantes
├── processados/ # Sucessos
├── falhos/ # Pendentes/reprocesso
└── ledger.sqlite3 # Banco de dedupe (gerado automaticamente)

---

## 🧩 Dependências

### Pacotes de sistema (exemplo Fedora/Debian)

```bash
sudo dnf install firefox-esr geckodriver tesseract tesseract-langpack-por
# ou
sudo apt install firefox-esr geckodriver tesseract-ocr tesseract-ocr-por


Python 3.11+ (virtualenv recomendado)
python -m venv .venv
source .venv/bin/activate
pip install selenium Pillow pytesseract tabulate pyyaml

🔑 Variáveis de ambiente (.env)
PORTAL_USER=usuario.fieldmap
PORTAL_PASS=senha.fieldmap
FIREFOX_BIN=/usr/bin/firefox-esr
HEADLESS=1

🚀 Execução manual
source .venv/bin/activate
python watcher.py --headless 1 --retry-interval 300
--headless 1 → roda sem abrir janela gráfica
--retry-interval 300 → tenta reprocessar falhos/ a cada 5 min

🧭 Serviço systemd (exemplo)
/etc/systemd/system/fieldmap-bot.service:

[Unit]
Description=Fieldmap Bot (OCR + Selenium) - headless
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/fieldmap-bot
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=/home/pi/fieldmap-bot/.env
Environment="HEADLESS=1"
Environment="FIREFOX_BIN=/usr/bin/firefox-esr"
ExecStart=/home/pi/fieldmap-bot/.venv/bin/python watcher.py --headless 1 --retry-interval 300
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
sudo systemctl enable --now fieldmap-bot.service
sudo journalctl -fu fieldmap-bot.service

🔄 Reprocesso manual de falhas
# Executa uma varredura única de 'falhos/'
python retry_falhos.py --once

# Ou roda em loop (a cada 5 min)
python retry_falhos.py --watch 300

🧮 Gerenciamento do ledger
python manage_ledger.py stats           # mostra contagem e últimas datas
python manage_ledger.py list --limit 20 # lista últimos registros
python manage_ledger.py find Foxit      # busca por nome/termo
python manage_ledger.py delete Foxit    # apaga registros específicos
python manage_ledger.py purge --days 180 --which all  # limpa antigos
python manage_ledger.py vacuum          # compacta o banco

🧠 Estrutura do banco (ledger.sqlite3)
processed_files: 1 registro por arquivo físico (hash SHA256)
processed_semantic: 1 registro por combinação (tipo + minuto + valor)

campo	descrição
hash	hash SHA256 do arquivo
nome_arquivo	nome original
tipo	pedágio / estacionamento
data_iso	data/hora ISO completa
valor_centavos	valor bruto
created_at	data do registro

🧰 Diagnóstico rápido
Arquivos não processados → ver falhos/

Logs completos → journalctl -u fieldmap-bot.service -f

Snapshot de erro Selenium → debug_*.html + debug_*.png

Banco → sqlite3 ledger.sqlite3 "SELECT * FROM processed_files LIMIT 5;"

🧡 Créditos
Desenvolvido por Rodrigo Pinheiro

Automação: Python + Selenium + Tesseract OCR

Compatível com FieldMap Mobile via Firefox ESR

Otimizado para Raspberry Pi 5 / Linux ARM64

📜 Licença
MIT License © 2025 Rodrigo Pinheiro
Sinta-se livre para adaptar e contribuir!