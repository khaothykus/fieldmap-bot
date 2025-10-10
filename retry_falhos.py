#!/usr/bin/env python3
# retry_falhos.py
import os
import json
import time
import argparse
import logging
import random
from datetime import datetime, timedelta
from typing import Dict, Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE_DIR, "retry_state.json")
FALHOS_DIR = os.path.join(BASE_DIR, "falhos")
COMPROVANTES_DIR = os.path.join(BASE_DIR, "comprovantes")
LOCK_PATH = os.path.join("/tmp", "retry_falhos.lock")

def _load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logging.warning("[retry] Estado ilegível; iniciando limpo.")
    return {}

def _save_state(st: Dict[str, Any]) -> None:
    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_PATH)
        logging.debug(f"[retry] Estado salvo em {STATE_PATH}")
    except Exception as e:
        logging.warning(f"[retry] Falha ao salvar estado: {e}")

def _next_delay_minutes(attempt: int) -> int:
    # 1ª e 2ª tentativas rápidas, depois alarga um pouco
    return 2 if attempt <= 1 else 5 if attempt == 2 else 10

def _take_lock() -> bool:
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return True
    except FileExistsError:
        return False
    except Exception:
        return False

def _release_lock():
    try:
        if os.path.exists(LOCK_PATH):
            os.remove(LOCK_PATH)
    except Exception:
        pass

def run_retry(headless: bool = True) -> None:
    """Uma passada sobre 'falhos/' com backoff por arquivo."""
    # import lazy para evitar import circular
    from watcher import Watcher

    if not _take_lock():
        logging.info("[retry] Já existe uma execução em andamento (lock). Abortando esta rodada.")
        return

    try:
        os.makedirs(FALHOS_DIR, exist_ok=True)
        os.makedirs(COMPROVANTES_DIR, exist_ok=True)

        state = _load_state()
        w = Watcher(headless=headless, retry_interval=0)

        files = [
            os.path.join(FALHOS_DIR, f)
            for f in os.listdir(FALHOS_DIR)
            if os.path.isfile(os.path.join(FALHOS_DIR, f))
        ]
        if not files:
            logging.info("[retry] Nenhum arquivo em 'falhos'.")
            # limpa estado de chaves órfãs
            if state:
                _save_state({})
            return

        # Limpa entradas órfãs
        known = {os.path.basename(p) for p in files}
        for k in list(state.keys()):
            if k not in known:
                state.pop(k, None)
        _save_state(state)

        logging.info(f"[retry] Reprocessando {len(files)} arquivo(s) de 'falhos'...")

        for fpath in files:
            base = os.path.basename(fpath)
            st = state.get(base, {"attempts": 0, "next_due": None})

            # respeita janela de backoff
            now = datetime.utcnow()
            if st.get("next_due"):
                try:
                    if now < datetime.fromisoformat(st["next_due"]):
                        logging.debug(f"[retry] Aguardando janela de '{base}' (next_due={st['next_due']}).")
                        continue
                except Exception:
                    # se o campo estiver sujo, ignora e prossegue
                    pass

            # atualiza tentativa e agenda próximo horário (com pequeno jitter)
            st["attempts"] = int(st.get("attempts", 0)) + 1
            delay_min = _next_delay_minutes(st["attempts"])
            jitter_s = random.randint(0, 20)  # até 20s
            st["next_due"] = (now + timedelta(minutes=delay_min, seconds=jitter_s)).isoformat(timespec="seconds")
            state[base] = st
            _save_state(state)

            destino = os.path.join(COMPROVANTES_DIR, base)
            try:
                if os.path.exists(destino):
                    os.remove(destino)
            except Exception:
                pass

            try:
                os.replace(fpath, destino)
            except FileNotFoundError:
                # alguém mexeu — segue pro próximo
                continue

            logging.info(f"[retry] Tentativa {st['attempts']} para '{base}' (próxima em {delay_min} min).")

            failed = False
            try:
                w.processar(destino)
                # Se processou com sucesso, o arquivo não estará mais em comprovantes/
                # Se falhou, o watcher move de volta para falhos/ (e não fica em comprovantes/)
                if os.path.exists(destino):
                    # ficou “preso” em comprovantes — consideramos falha e devolvemos
                    failed = True
            except Exception as e:
                logging.exception(f"[retry] Exceção durante reprocessamento de {base}: {e}")
                failed = True
            finally:
                if failed:
                    # devolve para falhos se ainda estiver em comprovantes
                    if os.path.exists(destino):
                        try:
                            os.replace(destino, fpath)
                        except Exception as e:
                            logging.warning(f"[retry] Não consegui devolver '{base}' para 'falhos': {e}")
                    _save_state(state)
                else:
                    # sucesso: remove do estado
                    if base in state:
                        state.pop(base, None)
                        _save_state(state)

    finally:
        _release_lock()

def _setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def main():
    _setup_logging()
    ap = argparse.ArgumentParser(description="Reprocessa arquivos de 'falhos/' com backoff.")
    ap.add_argument("--headless", type=int, default=1, help="1=headless, 0=janela")
    ap.add_argument("--once", action="store_true", help="Executa apenas uma passada em 'falhos/'.")
    ap.add_argument("--watch", type=int, default=0, help="Loop a cada N segundos (mín. 15). 0=desliga loop.")
    args = ap.parse_args()

    if args.once or args.watch == 0:
        run_retry(headless=bool(args.headless))
        return

    interval = max(15, args.watch)
    logging.info(f"[retry] Loop a cada {interval}s (headless={bool(args.headless)})")
    while True:
        run_retry(headless=bool(args.headless))
        time.sleep(interval)

if __name__ == "__main__":
    main()
