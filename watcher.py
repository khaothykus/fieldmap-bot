# watcher.py
import os
import time
import argparse
import logging
from datetime import datetime
from dotenv import load_dotenv

from portal_client import PortalClient
from ocr_utils import extrair_dados_comprovante
from dedupe import file_hash, already_done, mark_done

load_dotenv()

IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

class Watcher:
    def __init__(
        self,
        pasta_comprovantes: str = "comprovantes",
        pasta_processados: str = "processados",
        pasta_falhos: str = "falhos",
        retry_interval_min: int = 0,
        headless_env: str | None = None,
    ):
        self.pasta_in = pasta_comprovantes
        self.pasta_ok = pasta_processados
        self.pasta_fail = pasta_falhos
        self.retry_interval_min = max(0, int(retry_interval_min))

        # garante pastas
        for p in (self.pasta_in, self.pasta_ok, self.pasta_fail):
            os.makedirs(p, exist_ok=True)

        # HEADLESS: se vier no argumento, usa; senão respeita .env (HEADLESS="1" liga headless)
        if headless_env is not None:
            headless = (str(headless_env) == "1")
        else:
            headless = (os.getenv("HEADLESS", "0") == "1")

        self.pc = PortalClient(headless=headless)
        self._vistos = set()  # controla “novos” no ciclo principal

    # ----------------- util -----------------
    def _mover(self, src: str, destino_dir: str):
        try:
            os.makedirs(destino_dir, exist_ok=True)
            base = os.path.basename(src)
            dst = os.path.join(destino_dir, base)
            # evita sobrescrever: anexa sufixo incremental se necessário
            if os.path.exists(dst):
                root, ext = os.path.splitext(base)
                i = 1
                while True:
                    cand = os.path.join(destino_dir, f"{root}__{i}{ext}")
                    if not os.path.exists(cand):
                        dst = cand
                        break
                    i += 1
            os.replace(src, dst)
        except Exception as e:
            logging.warning(f"Falha ao mover '{src}' para '{destino_dir}': {e}")

    def _listar_imagens(self, pasta: str):
        try:
            return [
                os.path.join(pasta, f)
                for f in os.listdir(pasta)
                if f.lower().endswith(IMG_EXTS)
            ]
        except FileNotFoundError:
            os.makedirs(pasta, exist_ok=True)
            return []

    # ------------- regra: só lança se tudo confiável -------------
    def _dados_confiaveis(self, dados) -> tuple[bool, str]:
        """
        Regras:
         - tipo ∈ {'pedagio','estacionamento'}
         - data é datetime (não 'now' de fallback que você não queira)
         - valor_centavos > 0
        """
        if not getattr(dados, "tipo", None) in {"pedagio", "estacionamento"}:
            return False, "tipo OCR indefinido/inesperado"

        if not isinstance(getattr(dados, "data", None), datetime):
            return False, "data OCR ausente/inegível"

        if getattr(dados, "valor_centavos", 0) <= 0:
            return False, "valor OCR <= 0"

        return True, ""

    # ----------------- processamento de um arquivo -----------------
    def processar(self, path: str) -> bool:
        """
        Retorna True se lançou e confirmou, False caso contrário.
        Em caso de falha, o chamador decide se move para 'falhos' ou se será reprocessado depois.
        """
        logging.info(f"Novo arquivo: {path}")
        try:
            # 1) dedupe
            h = file_hash(path)
            if already_done(h):
                logging.info("Arquivo já processado (hash conhecido) — ignorando.")
                # aqui, por consistência, movemos para 'processados' para sair da pasta de entrada
                self._mover(path, self.pasta_ok)
                return True

            # 2) OCR
            dados = extrair_dados_comprovante(path)
            logging.info(
                f"OCR: tipo={dados.tipo} data={dados.data} valor_centavos={dados.valor_centavos}"
            )

            # 3) validações rígidas de OCR
            ok, motivo = self._dados_confiaveis(dados)
            if not ok:
                logging.error(f"OCR inconsistente ({motivo}). Nada foi lançado.")
                return False  # chamador moverá para 'falhos'

            # 4) localizar a linha no mês correto e obter o HREF exato de /Despesa/Index
            href = self.pc.encontrar_linha_por_data_hora(dados.data, dados.tipo)
            if not href:
                logging.error(
                    "Não encontrei linha correspondente ao horário **no mês do comprovante**. Nada foi lançado."
                )
                return False

            # 5) abrir página /Despesa/Index pelo HREF (ancorado por mês e deslocamento)
            if not self.pc.abrir_despesas_por_href(href):
                logging.error("Falha ao abrir /Despesa/Index a partir do HREF. Nada foi lançado.")
                return False

            # 6) preencher, anexar, salvar e confirmar pela grade (sem fallback)
            ok = self.pc.preencher_e_anexar(
                dados.tipo, dados.valor_centavos, path, data_evento=dados.data
            )
            if not ok:
                logging.error("Validação falhou ou não houve confirmação. Nada foi lançado.")
                return False

            # 7) sucesso => grava no ledger e move para processados
            mark_done(
                h,
                tipo=dados.tipo,
                data=dados.data.isoformat(),
                valor_centavos=dados.valor_centavos,
                nome_arquivo=os.path.basename(path),
            )
            logging.info("✔ Despesa lançada e comprovante anexado com sucesso.")
            self._mover(path, self.pasta_ok)
            return True

        except Exception as e:
            logging.exception(f"ERRO ao processar {path}: {e}")
            return False

    # ----------------- loop principal (pasta de entrada) -----------------
    def run_once_inbox(self):
        """Processa apenas os arquivos novos da pasta de entrada."""
        arquivos = self._listar_imagens(self.pasta_in)
        novos = [f for f in arquivos if f not in self._vistos]
        for path in novos:
            self._vistos.add(path)
            ok = self.processar(path)
            if not ok:
                # mover para falhos somente aqui, se processar() retornou False
                self._mover(path, self.pasta_fail)

    # ----------------- auto-retry (pasta de falhos) -----------------
    def run_retry_cycle(self):
        """Tenta reprocessar tudo que está em falhos/. Só chame isso se retry_interval_min > 0."""
        arquivos = self._listar_imagens(self.pasta_fail)
        if not arquivos:
            return

        logging.info(f"[retry] Reprocessando {len(arquivos)} arquivo(s) de '{self.pasta_fail}'...")
        for src in list(arquivos):
            # mover de volta pra entrada para reaproveitar o mesmo fluxo
            base = os.path.basename(src)
            dst = os.path.join(self.pasta_in, base)
            try:
                if os.path.exists(dst):
                    # se já existe na entrada, cria sufixo
                    root, ext = os.path.splitext(base)
                    i = 1
                    while True:
                        cand = os.path.join(self.pasta_in, f"{root}__retry{i}{ext}")
                        if not os.path.exists(cand):
                            dst = cand
                            break
                        i += 1
                os.replace(src, dst)
                # remove do “vistos” para ser pego de novo
                if dst in self._vistos:
                    self._vistos.remove(dst)
            except Exception as e:
                logging.warning(f"[retry] Falha ao retornar '{src}' para a entrada: {e}")

    def main_loop(self):
        logging.info(f"Watcher iniciado. Aguardando comprovantes em {self.pasta_in}")
        last_retry_ts = 0.0
        retry_period = self.retry_interval_min * 60

        try:
            while True:
                # 1) processa novos da entrada
                self.run_once_inbox()

                # 2) se auto-retry estiver habilitado, dispara quando der o tempo
                now = time.time()
                if retry_period > 0 and (now - last_retry_ts) >= retry_period:
                    self.run_retry_cycle()
                    last_retry_ts = now

                time.sleep(2)

        except KeyboardInterrupt:
            logging.info("Encerrado pelo usuário.")
        finally:
            try:
                self.pc.close()
            except Exception:
                pass


def parse_args():
    p = argparse.ArgumentParser(description="Watcher de comprovantes com auto-retry embutido.")
    p.add_argument("--inbox", default="comprovantes", help="Pasta de entrada (default: comprovantes)")
    p.add_argument("--ok", default="processados", help="Pasta de sucesso (default: processados)")
    p.add_argument("--fail", default="falhos", help="Pasta de falha (default: falhos)")
    p.add_argument("--retry-interval", type=int, default=0,
                   help="Minutos entre varreduras de reprocessamento de falhos/ (0 = desliga)")
    p.add_argument("--retry-folder", default=None,
                   help="Pasta para reprocessar (default: falhos). Se passado, sobrescreve --fail só para o retry.")
    p.add_argument("--headless", default=None,
                   help="Força headless 0|1 (se omitido, usa .env HEADLESS)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    retry_folder = args.retry_folder if args.retry_folder else args.fail
    w = Watcher(
        pasta_comprovantes=args.inbox,
        pasta_processados=args.ok,
        pasta_falhos=retry_folder,
        retry_interval_min=args.retry_interval,
        headless_env=args.headless,
    )
    w.main_loop()
