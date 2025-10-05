import os
import time
import logging
from watcher import Watcher

FALHOS_DIR = os.path.join(os.path.dirname(__file__), "falhos")

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logging.info("Reprocessador de falhos iniciado.")
    watcher = Watcher()

    while True:
        arquivos = [os.path.join(FALHOS_DIR, f)
                    for f in os.listdir(FALHOS_DIR)
                    if os.path.isfile(os.path.join(FALHOS_DIR, f))]

        for arq in arquivos:
            logging.info(f"Tentando reprocessar: {arq}")
            try:
                watcher.processar(arq)
            except Exception as e:
                logging.error(f"Erro ao reprocessar {arq}: {e}")

        time.sleep(900)  # 15 minutos
