# ocr_utils.py
import re
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
    tipo: str                               # "pedagio" | "estacionamento" | "desconhecido"
    data: Optional[datetime]                # None se não encontrado/fora da janela
    valor_centavos: Optional[int]           # None se não encontrado


# -------------------------------
# Regex reutilizáveis
# -------------------------------
VALOR_RE = re.compile(
    r"""
    (?:R\$\s*)?               # opcional "R$"
    (?P<val>
        \d{1,3}(?:\.\d{3})*   # 1.234.567  OU
        |\d+                   # 12345
    )
    [\.,]\s?(?P<cents>\d{2})   # separador decimal + 2 dígitos
    """,
    re.VERBOSE,
)

# 10/09/2025 14:03 (com ou sem segundos)
DATAH_RE = re.compile(
    r"(?P<d>\d{1,2})[\/\-](?P<m>\d{1,2})[\/\-](?P<y>\d{2,4})\s+(?P<h>\d{1,2}):(?P<mm>\d{2})(?::(?P<ss>\d{2}))?"
)

# 15/09 às 10:41  (sem ano; muito comum na Estapar)
DATAH_SEM_ANO_RE = re.compile(
    r"(?P<d>\d{1,2})[\/\-](?P<m>\d{1,2}).{0,12}?\b(?:às|as|a[s]?)\s*(?P<h>\d{1,2}):(?P<mm>\d{2})",
    re.IGNORECASE,
)

# 02/10 - 17:25  (sem ano; muito comum na Veloe)
DATAH_TRACO_SEM_ANO_RE = re.compile(
    r"(?P<d>\d{1,2})[\/\-](?P<m>\d{1,2})\s*[–\-]\s*(?P<h>\d{1,2}):(?P<mm>\d{2})"
)

# Palavras-chave para classificar
KW_PEDAGIO = ("pedágio", "pedagio", "veloe", "sem parar", "semparar", "tag", "praça", "praca", "autoban", "ccr")
KW_ESTAC  = ("estac", "vaga legal", "zona azul", "zul+", "zul plus", "park", "parquímetro", "parquimetro", "estapar")


# -------------------------------
# Pré-processamento da imagem
# -------------------------------
def _normalize_img(path_img: str):
    img = Image.open(path_img).convert("L")  # escala de cinza

    if _HAS_CV2:
        import numpy as np
        npimg = np.array(img)
        th = cv2.adaptiveThreshold(
            npimg, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 31, 11
        )
        return Image.fromarray(th)

    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))
    img = img.point(lambda p: 255 if p > 160 else 0)
    return img


# -------------------------------
# OCR bruto
# -------------------------------
def _ocr_texto(path_img: str) -> str:
    img = _normalize_img(path_img)
    cfg = "--oem 3 --psm 6 -l por+eng"
    texto = pytesseract.image_to_string(img, config=cfg) or ""
    return texto


# -------------------------------
# Parsers de valor / data / tipo
# -------------------------------
def _parse_valor(texto: str) -> Optional[int]:
    linhas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
    preferidas = []
    for ln in linhas:
        ln_low = ln.lower()
        if any(k in ln_low for k in ("total", "valor", "pago", "pagamento", "tarifa")):
            preferidas.append(ln)

    candidatos = preferidas + linhas
    for ln in candidatos:
        m = VALOR_RE.search(ln.replace(" ", ""))
        if not m:
            m = VALOR_RE.search(ln)
        if m:
            inteiro = m.group("val").replace(".", "")
            cents = m.group("cents")
            try:
                v = int(inteiro) * 100 + int(cents)
                if v >= 100:                      # ignora “0,02” etc
                    return v
            except Exception:
                continue
    return None


def _inferir_ano_para_mes(m: int) -> int:
    """
    Infere um ano razoável quando o ticket vem sem ano (ex.: Estapar/Veloe).
    Regra: usa ano corrente; se o mês inferido ficar MUITO à frente do mês atual,
    assume ano anterior (cobre o caso jan lendo nov/dez do ano passado).
    """
    now = datetime.now()
    ano = now.year
    if (m - now.month) >= 3:
        ano -= 1
    return ano


def _validar_janela_meses(dt: datetime | None) -> Optional[datetime]:
    """
    Política combinada com o watcher:
      - NUNCA aceita data no FUTURO.
      - Aceita APENAS mês corrente OU mês anterior.
      - Qualquer outra situação => retorna None (watcher não lança).
    """
    if dt is None:
        return None
    now = datetime.now()
    if dt > now:
        return None

    cur_y, cur_m = now.year, now.month
    prev_y = cur_y if cur_m > 1 else cur_y - 1
    prev_m = cur_m - 1 if cur_m > 1 else 12

    if (dt.year, dt.month) in {(cur_y, cur_m), (prev_y, prev_m)}:
        return dt
    return None


def _parse_data(texto: str) -> Optional[datetime]:
    # 1) data completa
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

    # 2) dd/mm “às” HH:MM  (sem ano)
    m2 = DATAH_SEM_ANO_RE.search(texto)
    if m2:
        d = int(m2.group("d"))
        mth = int(m2.group("m"))
        hh = int(m2.group("h"))
        mm = int(m2.group("mm"))
        y = _inferir_ano_para_mes(mth)
        try:
            return datetime(y, mth, d, hh, mm, 0)
        except ValueError:
            pass

    # 3) dd/mm - HH:MM  (sem ano; recibos Veloe)
    m3 = DATAH_TRACO_SEM_ANO_RE.search(texto)
    if m3:
        d = int(m3.group("d"))
        mth = int(m3.group("m"))
        hh = int(m3.group("h"))
        mm = int(m3.group("mm"))
        y = _inferir_ano_para_mes(mth)
        try:
            return datetime(y, mth, d, hh, mm, 0)
        except ValueError:
            pass

    # nada encontrado
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
      - data (datetime | None) -> None se não achar OU se estiver fora da janela (mês atual/ anterior)
      - valor_centavos (int | None)
    """
    texto = _ocr_texto(path_img)
    tipo = _classifica_tipo(texto)
    valor = _parse_valor(texto)
    data = _validar_janela_meses(_parse_data(texto))

    logging.debug("[OCR] tipo=%s valor=%s data=%s", tipo, valor, data)

    return DadosComprovante(
        tipo=tipo,
        data=data,
        valor_centavos=(int(valor) if valor is not None else None),
    )


# -------------------------------
# CLI de teste rápido
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
