# ocr_utils.py
import os
import re
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Tuple

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
# Debug (fora das pastas vigiadas)
# -------------------------------
_OCR_DEBUG = os.getenv("OCR_DEBUG", "0") not in ("0", "", "false", "False", "no")
_DEBUG_DIR = os.getenv("OCR_DEBUG_DIR", "_ocr_debug")

def _dump_debug(img: Image.Image, texto: str, stem: str):
    if not _OCR_DEBUG:
        return
    os.makedirs(_DEBUG_DIR, exist_ok=True)
    try:
        img.save(os.path.join(_DEBUG_DIR, f"{stem}_norm.png"))
    except Exception:
        pass
    try:
        with open(os.path.join(_DEBUG_DIR, f"{stem}_ocr_debug.txt"), "w", encoding="utf-8") as f:
            f.write(texto)
    except Exception:
        pass


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

# data + hora “bem especificadas”
DATAH_RE = re.compile(
    r"(?P<d>\d{1,2})[\/\-](?P<m>\d{1,2})[\/\-](?P<y>\d{2,4})\s+(?P<h>\d{1,2}):(?P<mm>\d{2})(?::(?P<ss>\d{2}))?"
)

# variação com traço: “03/11/2025 - 14:40” (Mercado Pago)
DATAH_TRACO_RE = re.compile(
    r"(?P<d>\d{1,2})[\/\-](?P<m>\d{1,2})[\/\-](?P<y>\d{2,4})\s*[–\-]\s*(?P<h>\d{1,2}):(?P<mm>\d{2})"
)

# versão FLEX: aceita QUALQUER coisa curta entre a data e a hora (OCR pode “sumir” com o traço/pontos)
DATAH_FLEX_RE = re.compile(
    r"(?P<d>\d{1,2})[\/\-](?P<m>\d{1,2})[\/\-](?P<y>\d{2,4}).{0,8}?(?P<h>\d{1,2}):(?P<mm>\d{2})"
)

# 15/09 às 10:41 (sem ano)
DATAH_SEM_ANO_RE = re.compile(
    r"(?P<d>\d{1,2})[\/\-](?P<m>\d{1,2}).{0,12}?\b(?:às|as|a[s]?)\s*(?P<h>\d{1,2}):(?P<mm>\d{2})",
    re.IGNORECASE,
)

# “Início …” / “Término …” (Sigapay)
SIGAPAY_INICIO_RE  = re.compile(r"(?i)in[ií]cio.*?(\d{1,2}/\d{1,2}/\d{2,4}).{0,8}?(\d{1,2}:\d{2})")
SIGAPAY_TERMINO_RE = re.compile(r"(?i)t[ée]rmino.*?(\d{1,2}/\d{1,2}/\d{2,4}).{0,8}?(\d{1,2}:\d{2})")

# Palavras-chave para classificar
KW_PEDAGIO = ("pedágio", "pedagio", "veloe", "sem parar", "semparar", "tag", "praça", "praca", "autoban", "ccr", "rota das bandeiras", "renovias")
KW_ESTAC  = ("estac", "vaga legal", "zona azul", "zul+", "zul plus", "park", "parquímetro", "parquimetro", "estapar", "sigapay")


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
def _ocr_texto(path_img: str) -> Tuple[Image.Image, str]:
    img = _normalize_img(path_img)
    cfg = "--oem 3 --psm 6 -l por+eng"
    texto = pytesseract.image_to_string(img, config=cfg) or ""
    # dump de debug centralizado
    stem = os.path.splitext(os.path.basename(path_img))[0]
    _dump_debug(img, texto, stem)
    return img, texto


# -------------------------------
# Parsers de valor / data / tipo
# -------------------------------
def _parse_valor(texto: str) -> Optional[int]:
    # normalização leve para erros comuns de OCR em valores
    def _fix_ocr_digits(s: str) -> str:
        tbl = str.maketrans({
            "O": "0", "o": "0",
            "S": "5",
            "I": "1", "l": "1",
        })
        return s.translate(tbl)

    # linhas não vazias
    linhas = [ln.strip() for ln in texto.splitlines() if ln.strip()]

    # prioriza linhas que *parecem* conter o valor
    preferidas = []
    for ln in linhas:
        ln_low = ln.lower()
        if any(k in ln_low for k in ("total", "valor", "pago", "pagamento", "tarifa")):
            preferidas.append(ln)

    candidatos = preferidas + linhas

    for ln in candidatos:
        ln_fix = _fix_ocr_digits(ln)
        m = VALOR_RE.search(ln_fix.replace(" ", "")) or VALOR_RE.search(ln_fix)
        if not m:
            continue
        inteiro = m.group("val").replace(".", "")
        cents = m.group("cents")
        try:
            v = int(inteiro) * 100 + int(cents)
            # Aceita a partir de 50 centavos (ex.: Estapar R$ 0,90)
            if v >= 50:
                return v
        except Exception:
            continue

    return None


def _inferir_ano_para_mes(m: int) -> int:
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


def _to_dt(y: int, m: int, d: int, hh: int, mm: int, ss: int = 0) -> Optional[datetime]:
    try:
        return datetime(y, m, d, hh, mm, ss)
    except ValueError:
        return None


def _collect_all_dates(texto: str) -> List[datetime]:
    """Coleta TODAS as datas possíveis do texto (com ano)."""
    out: List[datetime] = []

    for rx in (DATAH_RE, DATAH_TRACO_RE, DATAH_FLEX_RE):
        for m in rx.finditer(texto):
            d, mth, y = int(m.group("d")), int(m.group("m")), int(m.group("y"))
            if y < 100:
                y += 2000
            hh, mm = int(m.group("h")), int(m.group("mm"))
            ss = int(m.group("ss") or 0) if "ss" in m.groupdict() else 0
            dt = _to_dt(y, mth, d, hh, mm, ss)
            if dt:
                out.append(dt)

    # datas “sem ano” — supõe o ano mais provável
    for m in DATAH_SEM_ANO_RE.finditer(texto):
        d, mth = int(m.group("d")), int(m.group("m"))
        hh, mm = int(m.group("h")), int(m.group("mm"))
        y = _inferir_ano_para_mes(mth)
        dt = _to_dt(y, mth, d, hh, mm, 0)
        if dt:
            out.append(dt)

    return out


def _parse_data(texto: str, tipo: str) -> Optional[datetime]:
    low = texto.lower()

    # 1) Sinalização Mercado Pago: “Data da passagem …”
    if "data da passagem" in low:
        # pega a PRIMEIRA data depois da frase
        try:
            pos = low.index("data da passagem")
            trecho = texto[pos:pos + 120]  # janela curta após a frase
            for rx in (DATAH_TRACO_RE, DATAH_RE, DATAH_FLEX_RE):
                m = rx.search(trecho)
                if m:
                    d, mth, y = int(m.group("d")), int(m.group("m")), int(m.group("y"))
                    if y < 100:
                        y += 2000
                    hh, mm = int(m.group("h")), int(m.group("mm"))
                    ss = int(m.group("ss") or 0) if "ss" in m.groupdict() else 0
                    return _to_dt(y, mth, d, hh, mm, ss)
        except Exception:
            pass

    # 2) SIGAPAY (APP/WEB): preferir “Início …”; se não tiver, cair pro genérico
    ini = None
    m = SIGAPAY_INICIO_RE.search(texto)
    if m:
        d, mth, y = m.group(1).split("/")
        hh, mm = m.group(2).split(":")
        ini = _to_dt(int(y), int(mth), int(d), int(hh), int(mm), 0)

    fim = None
    m2 = SIGAPAY_TERMINO_RE.search(texto)
    if m2:
        d, mth, y = m2.group(1).split("/")
        hh, mm = m2.group(2).split(":")
        fim = _to_dt(int(y), int(mth), int(d), int(hh), int(mm), 0)

    if ini or fim:
        # Se for estacionamento, **sempre usar o Início**
        if "estacion" in (tipo or "").lower() and ini:
            return ini
        # fallback: retorna a primeira que existir
        return ini or fim

    # 3) Genérico: coletar todas e decidir
    todas = _collect_all_dates(texto)
    if not todas:
        return None

    if "estacion" in (tipo or "").lower():
        # Para estacionamento, escolhas conservadoras:
        # - se houver >=2 horários no mesmo dia, usa o MENOR (Início provável)
        # - senão, usa o menor entre todas
        by_day: dict[tuple[int, int, int], List[datetime]] = {}
        for dt in todas:
            by_day.setdefault((dt.year, dt.month, dt.day), []).append(dt)
        candidates = []
        for arr in by_day.values():
            candidates.append(min(arr))
        return min(candidates)

    # para pedágio, usa a primeira/única (ou o menor horário)
    return min(todas)


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
    img, texto = _ocr_texto(path_img)
    tipo = _classifica_tipo(texto)
    valor = _parse_valor(texto)
    data = _validar_janela_meses(_parse_data(texto, tipo))

    logging.debug("[OCR] tipo=%s valor=%s data=%s", tipo, valor, data)

    return DadosComprovante(
        tipo=tipo,
        data=(data if data is not None else None),
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
