# dedupe.py
import os, sqlite3, hashlib
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "ledger.sqlite3")

def _conn():
    return sqlite3.connect(DB_PATH)

def migrate():
    con = _conn(); c = con.cursor()
    c.execute("""
      CREATE TABLE IF NOT EXISTS processed(
        file_hash TEXT PRIMARY KEY,
        nome_arquivo TEXT,
        tipo TEXT,
        data TEXT,                -- ISO8601 (com hora)
        valor_centavos INTEGER,
        ocr_sig TEXT,             -- NOVO: assinatura semântica
        created_at TEXT
      )
    """)
    # colunas novas em bases antigas
    for col, spec in [("ocr_sig","TEXT"), ("nome_arquivo","TEXT"),
                      ("tipo","TEXT"), ("data","TEXT"),
                      ("valor_centavos","INTEGER"), ("created_at","TEXT")]:
        try: c.execute(f"ALTER TABLE processed ADD COLUMN {col} {spec}")
        except sqlite3.OperationalError: pass
    # índice/unique para assinatura (evita duplicar por conteúdo)
    try: c.execute("CREATE UNIQUE INDEX ux_processed_ocrsig ON processed(ocr_sig)")
    except sqlite3.OperationalError: pass
    con.commit(); con.close()

def file_hash(path:str)->str:
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(65536), b''): h.update(chunk)
    return h.hexdigest()

def make_ocr_sig(tipo:str, dt:datetime, valor_centavos:int)->str:
    # granularidade no minuto é suficiente para Estapar/Veloe
    return f"{tipo}|{dt.strftime('%Y-%m-%d %H:%M')}|{valor_centavos}"

def already_done(file_h:str)->bool:
    migrate()
    con=_conn(); c=con.cursor()
    row=c.execute("SELECT 1 FROM processed WHERE file_hash=?",(file_h,)).fetchone()
    con.close()
    return bool(row)

def already_done_sig(sig:str)->bool:
    migrate()
    con=_conn(); c=con.cursor()
    row=c.execute("SELECT 1 FROM processed WHERE ocr_sig=?",(sig,)).fetchone()
    con.close()
    return bool(row)

def mark_done(file_h:str, *, nome_arquivo:str="", tipo:str="", data:str="",
              valor_centavos:int=0, ocr_sig:str|None=None):
    migrate()
    con=_conn(); c=con.cursor()
    c.execute("""
      INSERT OR IGNORE INTO processed
        (file_hash, nome_arquivo, tipo, data, valor_centavos, ocr_sig, created_at)
      VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
    """, (file_h, nome_arquivo, tipo, data, valor_centavos, ocr_sig))
    con.commit(); con.close()
