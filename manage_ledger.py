import sqlite3
import argparse
from tabulate import tabulate
from datetime import datetime
from dedupe import DB_PATH

def migrate():
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


def list_all():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    rows = c.execute("""
        SELECT rowid, file_hash, nome_arquivo, tipo, data, valor_centavos, ocr_sig, created_at
        FROM processed ORDER BY rowid DESC
    """).fetchall()
    conn.close()
    print(tabulate(rows, headers=["id", "file_hash", "nome_arquivo", "tipo", "data", "valor_centavos", "ocr_sig", "created_at"]))


def find(term):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    term_like = f"%{term}%"
    rows = c.execute("""
        SELECT rowid, file_hash, nome_arquivo, tipo, data, valor_centavos, ocr_sig, created_at
        FROM processed
        WHERE file_hash LIKE ? OR nome_arquivo LIKE ? OR ocr_sig LIKE ?
    """, (term, term_like)).fetchall()
    conn.close()
    print(tabulate(rows, headers=["id", "file_hash", "nome_arquivo", "tipo", "data", "valor_centavos", "ocr_sig", "created_at"]))


def delete(term, yes=False):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    term_like = f"%{term}%"
    to_del = c.execute("""
        SELECT rowid, file_hash, nome_arquivo, tipo, data, valor_centavos, ocr_sig, created_at
        FROM processed
        WHERE file_hash LIKE ? OR nome_arquivo LIKE ? OR ocr_sig LIKE ?
    """, (term, term_like)).fetchall()

    if not to_del:
        print("Nada encontrado.")
        conn.close()
        return

    print(tabulate(to_del, headers=["id", "file_hash", "nome_arquivo", "tipo", "data", "valor_centavos", "ocr_sig", "created_at"]))

    if yes or input("Confirma exclusão? (y/N) ").lower().startswith("y"):
        ids = [r[0] for r in to_del]
        c.executemany("DELETE FROM processed WHERE rowid = ?", [(i,) for i in ids])
        conn.commit()
        print(f"{len(ids)} registro(s) removido(s).")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Gerencia o ledger de comprovantes processados")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list")
    f = sub.add_parser("find")
    f.add_argument("term")
    d = sub.add_parser("delete")
    d.add_argument("term")
    d.add_argument("--yes", action="store_true")

    args = parser.parse_args()
    migrate()

    if args.cmd == "list":
        list_all()
    elif args.cmd == "find":
        find(args.term)
    elif args.cmd == "delete":
        delete(args.term, args.yes)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
