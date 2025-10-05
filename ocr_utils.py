# ocr_utils.py
import re
import os
import math
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from PIL import Image, ImageOps, ImageFilter
import pytesseract

try:
    import cv2  # opcional
    _HAS_CV2 = True
except Exception:
    _HAS_CV2 = False


# -------------------------------
# Modelo de retorno
# -------------------------------
@dataclass
class DadosComprovante:
    tipo: str                 # "pedagio" | "estacionamento" | "desconhecido"
    data: datetime
    valor_centavos: int


# -------------------------------
# Regex reutilizáveis
# -------------------------------
# R$ 12,34 / R$12,34 / 12,34 / 12.34 (aceita variações)
VALOR_RE = re.compile(
    r"""
    (?:R\$\s*)?               # opcional "R$"
    (?P<val>
        \d{1,3}(?:\.\d{3})*   # 1.234.567 (com milhares)  - OU -
        |\d+                   # 12345
    )
    [\.,]\s?(?P<cents>\d{2})   # separador decimal + 2 dígitos
    """,
    re.VERBOSE
)

# 10/09/2025 14:03 (com ou sem segundos)
DATAH_RE = re.compile(
    r"(?P<d>\d{1,2})[\/\-](?P<m>\d{1,2})[\/\-](?P<y>\d{2,4})\s+(?P<h>\d{1,2}):(?P<mm>\d{2})(?::(?P<ss>\d{2}))?"
)

# "Ativação às 10:23" / "Início às 10:23"
ATIV_RE = re.compile(
    r"(ativ|in[ií]cio).{0,15}?\b(?P<h>\d{1,2}):(?P<mm>\d{2})",
    re.IGNORECASE
)

# Palavras-chave para classificar
KW_PEDAGIO = ("pedágio", "pedagio", "veloe", "sem parar", "semparar", "tag", "praça", "praca")
KW_ESTAC  = ("estac", "vaga legal", "zona azul", "zul+", "zul plus", "park", "parquímetro", "parquimetro")

# -------------------------------
# Pré-processamento da imagem
# -------------------------------
def _normalize_img(path_img: str) -> Image.Image:
    """
    Retorna uma PIL.Image já melhorada para OCR.
    Se OpenCV estiver disponível, usa limiar adaptativo; senão, cai em PIL.
    """
    img = Image.open(path_img).convert("L")  # escala de cinza

    if _HAS_CV2:
        # OpenCV: adaptive threshold ajuda MUITO em prints de apps
        import numpy as np
        npimg = np.array(img)
        th = cv2.adaptiveThreshold(
            npimg, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 31, 11
        )
        return Image.fromarray(th)

    # Fallback com PIL: aumenta contraste + binariza simples
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))
    # binarização simples
    img = img.point(lambda p: 255 if p > 160 else 0)
    return img

# -------------------------------
# OCR bruto
# -------------------------------
def _ocr_texto(path_img: str) -> str:
    """
    Extração de texto via Tesseract com config amigável a comprovantes.
    """
    img = _normalize_img(path_img)
    cfg = "--oem 3 --psm 6 -l por+eng"
    texto = pytesseract.image_to_string(img, config=cfg) or ""
    # normalização soft (lower sem acento pode atrapalhar valores); mantemos como está
    return texto

# -------------------------------
# Parsers de valor / data / tipo
# -------------------------------
def _parse_valor(texto: str) -> Optional[int]:
    """
    Procura o primeiro valor que pareça total de pagamento.
    Retorna em centavos.
    """
    # Heurística: priorizar linhas que contém palavras típicas de total/pagamento
    linhas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
    preferidas = []
    for ln in linhas:
        ln_low = ln.lower()
        if any(k in ln_low for k in ("total", "valor", "pago", "pagamento", "tarifa")):
            preferidas.append(ln)

    candidatos = preferidas + linhas  # preferidas primeiro
    for ln in candidatos:
        m = VALOR_RE.search(ln.replace(" ", ""))
        if not m:
            m = VALOR_RE.search(ln)
        if m:
            inteiro = m.group("val")
            cents = m.group("cents")
            inteiro = inteiro.replace(".", "")
            try:
                v = int(inteiro) * 100 + int(cents)
                # filtro sanidade: valores muito pequenos (ex: 2 centavos) costumam ser falso positivo
                if v >= 100:
                    return v
            except Exception:
                continue
    return None

def _parse_data(texto: str) -> Optional[datetime]:
    """
    Tenta pegar data/hora:
    1) dd/mm/yyyy hh:mm(:ss)
    2) “Ativação/Início às HH:MM” -> usa a data de HOJE (melhor do que nulo)
       (Obs: se o ticket trouxer a data do dia e só a hora em “Ativação”, o (1) já pega)
    """
    m = DATAH_RE.search(texto)
    if m:
        d = int(m.group("d"))
        mth = int(m.group("m"))
        y = int(m.group("y"))
        if y < 100:
            y += 2000
        hh = int(m.group("h"))
        mm = int(m.group("mm"))
        ss = int(m.group("ss") or 0)
        try:
            return datetime(y, mth, d, hh, mm, ss)
        except ValueError:
            pass

    m2 = ATIV_RE.search(texto)
    if m2:
        hh = int(m2.group("h"))
        mm = int(m2.group("mm"))
        now = datetime.now()
        try:
            return datetime(now.year, now.month, now.day, hh, mm, 0)
        except ValueError:
            pass

    return None

def _classifica_tipo(texto: str) -> str:
    low = texto.lower()
    if any(k in low for k in KW_PEDAGIO):
        return "pedagio"
    if any(k in low for k in KW_ESTAC):
        return "estacionamento"
    return "desconhecido"

# -------------------------------
# Função principal (API)
# -------------------------------
def extrair_dados_comprovante(path_img: str) -> DadosComprovante:
    """
    Lê SOMENTE o conteúdo do arquivo (sem olhar nome) e retorna:
      - tipo ("pedagio"|"estacionamento"|"desconhecido")
      - data (datetime) -> se não achar, usa agora()
      - valor_centavos (int) -> se não achar, 0
    """
    texto = _ocr_texto(path_img)
    tipo = _classifica_tipo(texto)
    valor = _parse_valor(texto)
    data = _parse_data(texto)

    # logs auxiliares (úteis p/ debug)
    logging.debug("[OCR] tipo=%s valor=%s data=%s", tipo, valor, data)

    if data is None:
        # fallback seguro: hora atual (o watcher/portal garantem o mês correto antes de lançar)
        data = datetime.now()
    if valor is None:
        valor = 0

    return DadosComprovante(
        tipo=tipo,
        data=data,
        valor_centavos=int(valor)
    )


# -------------------------------
# Execução direta para teste rápido
# -------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) < 2:
        print("Uso: python ocr_utils.py <caminho_da_imagem>")
        sys.exit(1)
    img = sys.argv[1]
    dados = extrair_dados_comprovante(img)
    print(dados)
