import os
import hashlib
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "ledger.sqlite3")


def _ensure_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS processed (
            file_hash TEXT PRIMARY KEY,
            nome_arquivo TEXT,
            tipo TEXT,
            data TEXT,
            valor_centavos INTEGER,
            created_at TEXT
        )
    """)
    # garante colunas mesmo em DBs antigos
    for col, spec in [
        ("nome_arquivo", "TEXT"),
        ("tipo", "TEXT"),
        ("data", "TEXT"),
        ("valor_centavos", "INTEGER"),
        ("created_at", "TEXT")
    ]:
        try:
            c.execute(f"ALTER TABLE processed ADD COLUMN {col} {spec}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def already_done(h):
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    row = c.execute("SELECT 1 FROM processed WHERE file_hash = ?", (h,)).fetchone()
    conn.close()
    return bool(row)


def mark_done(h, tipo=None, data=None, valor_centavos=None, nome_arquivo=None):
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("""
        INSERT INTO processed (file_hash, tipo, data, valor_centavos, nome_arquivo, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_hash) DO UPDATE SET
            tipo=excluded.tipo,
            data=excluded.data,
            valor_centavos=excluded.valor_centavos,
            nome_arquivo=excluded.nome_arquivo,
            created_at=excluded.created_at
    """, (h, tipo, data, valor_centavos, nome_arquivo, now))
    conn.commit()
    conn.close()
