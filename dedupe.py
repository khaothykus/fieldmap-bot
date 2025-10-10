# dedupe.py
import os
import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from typing import Optional

# ------------------------------------------------------------
# Paths / DB
# ------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DB = os.path.join(BASE_DIR, "ledger.sqlite3")


# ------------------------------------------------------------
# Conexão + schema
# ------------------------------------------------------------
def _conn() -> sqlite3.Connection:
    """
    Abre conexão com pragmas razoáveis para uso em 1-2 processos (watcher + retry).
    WAL melhora concorrência; synchronous=NORMAL dá bom equilíbrio durabilidade x velocidade.
    """
    con = sqlite3.connect(_DB, timeout=10, isolation_level=None)  # autocommit
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute("PRAGMA foreign_keys=ON;")
    con.execute("PRAGMA temp_store=MEMORY;")
    _ensure_schema(con)
    return con


def _ensure_schema(con: sqlite3.Connection) -> None:
    # processed_files: hash único por arquivo físico
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_files (
            hash TEXT PRIMARY KEY,
            nome_arquivo TEXT,
            tipo TEXT,
            data_iso TEXT,
            valor_centavos INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    # processed_semantic: (tipo, data_min, valor) único
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_semantic (
            tipo TEXT NOT NULL,
            data_iso_min TEXT NOT NULL,
            valor_centavos INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (tipo, data_iso_min, valor_centavos)
        );
        """
    )
    # Índices úteis (no-ops se já existirem)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_files_created_at ON processed_files(created_at);"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_semantic_created_at ON processed_semantic(created_at);"
    )


# ------------------------------------------------------------
# Utils
# ------------------------------------------------------------
def _norm_tipo(tipo: Optional[str]) -> str:
    return (tipo or "").strip().lower()


def _to_iso_min(dt: datetime) -> str:
    """Normaliza para resolução de minuto (YYYY-MM-DDTHH:MM)."""
    return dt.replace(second=0, microsecond=0).isoformat(timespec="minutes")


# ------------------------------------------------------------
# Hash físico do arquivo
# ------------------------------------------------------------
def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def already_done(hash_hex: str) -> bool:
    with closing(_conn()) as con, con:  # context manager commita/fecha
        cur = con.execute(
            "SELECT 1 FROM processed_files WHERE hash = ? LIMIT 1",
            (hash_hex,),
        )
        return cur.fetchone() is not None


def mark_done(
    hash_hex: str,
    tipo: str = "",
    data: str = "",
    valor_centavos: int = 0,
    nome_arquivo: str = "",
) -> None:
    with closing(_conn()) as con, con:
        con.execute(
            """
            INSERT INTO processed_files (hash, nome_arquivo, tipo, data_iso, valor_centavos)
            VALUES (?,?,?,?,?)
            ON CONFLICT(hash) DO UPDATE SET
              nome_arquivo=excluded.nome_arquivo,
              tipo=excluded.tipo,
              data_iso=excluded.data_iso,
              valor_centavos=excluded.valor_centavos
            """,
            (
                hash_hex,
                nome_arquivo,
                _norm_tipo(tipo),
                data,
                int(valor_centavos or 0),
            ),
        )


# ------------------------------------------------------------
# Dedupe semântico (tipo + data(min) + valor)
# ------------------------------------------------------------
def already_done_semantic(tipo: str, data_dt: datetime, valor_centavos: int) -> bool:
    iso_min = _to_iso_min(data_dt)
    with closing(_conn()) as con, con:
        cur = con.execute(
            """
            SELECT 1
              FROM processed_semantic
             WHERE tipo = ? AND data_iso_min = ? AND valor_centavos = ?
             LIMIT 1
            """,
            (_norm_tipo(tipo), iso_min, int(valor_centavos or 0)),
        )
        return cur.fetchone() is not None


def mark_done_semantic(tipo: str, data_dt: datetime, valor_centavos: int) -> None:
    iso_min = _to_iso_min(data_dt)
    with closing(_conn()) as con, con:
        con.execute(
            """
            INSERT OR IGNORE INTO processed_semantic (tipo, data_iso_min, valor_centavos)
            VALUES (?,?,?)
            """,
            (_norm_tipo(tipo), iso_min, int(valor_centavos or 0)),
        )


# ------------------------------------------------------------
# Manutenção / inspeção (opcional)
# ------------------------------------------------------------
def purge_old_files(days: int = 120) -> int:
    """Apaga registros antigos de processed_files (por created_at). Retorna qtd deletada."""
    with closing(_conn()) as con, con:
        cur = con.execute(
            """
            DELETE FROM processed_files
             WHERE datetime(created_at) < datetime('now', ?)
            """,
            (f"-{int(days)} days",),
        )
        return cur.rowcount or 0


def purge_old_semantic(days: int = 120) -> int:
    """Apaga registros antigos de processed_semantic (por created_at). Retorna qtd deletada."""
    with closing(_conn()) as con, con:
        cur = con.execute(
            """
            DELETE FROM processed_semantic
             WHERE datetime(created_at) < datetime('now', ?)
            """,
            (f"-{int(days)} days",),
        )
        return cur.rowcount or 0


def count_files() -> int:
    with closing(_conn()) as con, con:
        cur = con.execute("SELECT COUNT(1) FROM processed_files")
        (n,) = cur.fetchone()
        return int(n)


def count_semantic() -> int:
    with closing(_conn()) as con, con:
        cur = con.execute("SELECT COUNT(1) FROM processed_semantic")
        (n,) = cur.fetchone()
        return int(n)
