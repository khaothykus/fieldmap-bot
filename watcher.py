import os
import time
import argparse
import logging
from typing import Optional

from portal_client import PortalClient
from ocr_utils import extrair_dados_comprovante
from dedupe import file_hash, already_done, mark_done, already_done_semantic
from selenium.common.exceptions import TimeoutException

from pathlib import Path

from dotenv import load_dotenv
load_dotenv(dotenv_path="/home/pi/fieldmap-bot/.env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

COMPROVANTES_DIR = "comprovantes"
PROCESSADOS_DIR = "processados"
FALHOS_DIR = "falhos"

# --- arquivos a ignorar / validações de imagem -------------------------
IGNORED_PREFIXES = ('.', '.syncthing.')
IGNORED_SUFFIXES = ('.tmp',)
ALLOWED_EXTS = {'.jpg', '.jpeg', '.png', '.webp'}

def _should_ignore(p: Path) -> bool:
    """Ignora temporários/ocultos/sincronia e extensões não suportadas."""
    name = p.name
    if name.startswith(IGNORED_PREFIXES):
        return True
    if name.endswith(IGNORED_SUFFIXES):
        return True
    if p.suffix.lower() not in ALLOWED_EXTS:
        return True
    return False

def _wait_until_stable(p: Path, attempts: int = 6, interval: float = 0.5) -> bool:
    """
    Aguarda o arquivo “assentar” (ex.: Syncthing ainda gravando).
    True = estável; False = ainda variando ou sumiu.
    """
    try:
        prev = (p.stat().st_size, p.stat().st_mtime_ns)
    except FileNotFoundError:
        return False
    for _ in range(attempts):
        time.sleep(interval)
        try:
            cur = (p.stat().st_size, p.stat().st_mtime_ns)
        except FileNotFoundError:
            return False
        if cur == prev:
            return True
        prev = cur
    return False


class Watcher:
    def __init__(self, headless: bool, retry_interval: int):
        self.pc = PortalClient(headless=headless)
        self.retry_interval = max(0, retry_interval)
        self._last_retry = time.time() if self.retry_interval > 0 else 0
        self._known = set()  # caminhos já vistos nesta execução

        for d in (COMPROVANTES_DIR, PROCESSADOS_DIR, FALHOS_DIR):
            os.makedirs(d, exist_ok=True)

    # -----------------------
    # util: mover arquivo
    # -----------------------
    def _mover(self, p: str, pasta: str):
        try:
            base = os.path.basename(p)
            dst = os.path.join(pasta, base)
            os.replace(p, dst)  # atômico
        except Exception as e:
            logging.warning(f"Falha ao mover '{p}' para '{pasta}': {e}")

    # -----------------------
    # loop de arquivos
    # -----------------------
    def processar(self, path: str):
        p = Path(path)

        # 0) Filtros de arquivos que não devem ser processados
        if _should_ignore(p):
            logger.info("Ignorando arquivo não-processável: %s", p)
            return

        # 1) Debounce: aguarda estabilizar (evita pegar .tmp do Syncthing)
        if not _wait_until_stable(p):
            logger.info("Arquivo ainda não estável (pode estar sendo gravado): %s", p)
            return
            
        logging.info(f"Novo arquivo: {path}")
        try:
            # dedupe por hash físico (processados)
            h = file_hash(path)
            if already_done(h):
                logging.info("Arquivo já processado (hash conhecido) — ignorando.")
                self._mover(path, PROCESSADOS_DIR)
                return

            # OCR
            dados = extrair_dados_comprovante(path)
            logging.info(f"OCR: tipo={dados.tipo} data={dados.data} valor_centavos={dados.valor_centavos}")

            # >>> BLINDAGEM: se não tiver tipo/data/valor, não segue para o portal
            if (not dados.data) or (not dados.valor_centavos) or (dados.tipo == "desconhecido"):
                logging.error("OCR insuficiente (tipo/data/valor ausentes). Nada foi lançado — 'falhos'.")
                self._mover(path, FALHOS_DIR)
                return

            # dedupe semântico (conteúdo OCR)
            if already_done_semantic(dados.tipo, dados.data, dados.valor_centavos):
                logging.info("Comprovante já lançado (duplicata por conteúdo OCR).")
                self._mover(path, PROCESSADOS_DIR)
                return

            # localizar a linha exata pela janela de horário (sem fallback!)
            href = self.pc.encontrar_linha_por_data_hora(dados.data, dados.tipo)
            if not href:
                logging.error("Não encontrei deslocamento compatível (janela de horário/mês). "
                            "Nada foi lançado — ficará em 'falhos' para reprocesso.")
                self._mover(path, FALHOS_DIR)
                return

            # abrir /Despesa/Index e lançar
            ok = self.pc.abrir_despesas_por_href(href) and self.pc.preencher_e_anexar(
                dados.tipo, dados.valor_centavos, path, data_evento=dados.data
            )

            if not ok:
                logging.error("Validação falhou ou não houve confirmação. Nada foi lançado.")
                self._mover(path, FALHOS_DIR)
                return

            # sucesso
            mark_done(
                h,
                tipo=dados.tipo,
                data=dados.data.isoformat(),
                valor_centavos=dados.valor_centavos,
                nome_arquivo=os.path.basename(path),
            )
            from dedupe import mark_done_semantic
            mark_done_semantic(dados.tipo, dados.data, dados.valor_centavos)

            logging.info("✔ Despesa lançada e comprovante anexado com sucesso.")
            self._mover(path, PROCESSADOS_DIR)

        except Exception as e:
            logging.exception(f"ERRO ao processar {path}: {e}")
            try:
                base = os.path.basename(path)
                os.makedirs(FALHOS_DIR, exist_ok=True)
                destino = os.path.join(FALHOS_DIR, base)
                if os.path.abspath(path) != os.path.abspath(destino):
                    try:
                        os.replace(path, destino)
                    except FileNotFoundError:
                        pass
            except Exception:
                logging.warning(f"Falha ao mover '{path}' para '{FALHOS_DIR}' (talvez já tenha sido movido).")

    # -----------------------
    # watch loop
    # -----------------------
    def run(self):
        logging.info("Watcher iniciado. Aguardando comprovantes em comprovantes")

        while True:
            # varre a pasta
            try:
                for fname in sorted(os.listdir(COMPROVANTES_DIR)):
                    p = os.path.join(COMPROVANTES_DIR, fname)
                    if not os.path.isfile(p):
                        continue
                    if p in self._known:
                        continue  # já visto nesta rodada
                    self._known.add(p)
                    self.processar(p)
            except KeyboardInterrupt:
                logging.info("Encerrado pelo usuário.")
                break
            except Exception:
                logging.exception("Erro no loop de observação")

            # reprocesso periódico (falhos -> comprovantes)
            self._retry_falhos_tick()
            time.sleep(2)

    def _retry_falhos_tick(self):
        if self.retry_interval <= 0:
            return
        # roda, no máximo, a cada retry_interval
        if (time.time() - self._last_retry) < self.retry_interval:
            return

        self._last_retry = time.time()
        falhos = [
            f for f in sorted(os.listdir(FALHOS_DIR))
            if os.path.isfile(os.path.join(FALHOS_DIR, f))
        ]
        if falhos:
            logging.info(f"[retry] Reprocessando {len(falhos)} arquivo(s) de 'falhos'...")

        for f in falhos:
            src = os.path.join(FALHOS_DIR, f)
            dst = os.path.join(COMPROVANTES_DIR, f)
            try:
                os.replace(src, dst)
            except Exception:
                # se não deu para mover, tenta na próxima
                continue

        # IMPORTANTE: limpe o cache de “já vistos” para reprocessar os retornados
        self._known.clear()


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", type=int, default=int(os.getenv("HEADLESS", "0")))
    ap.add_argument("--retry-interval", type=int, default=0, help="segundos entre varreduras de 'falhos'")
    args = ap.parse_args()

    w = Watcher(headless=bool(args.headless), retry_interval=args.retry_interval)
    w.run()


if __name__ == "__main__":
    main()
