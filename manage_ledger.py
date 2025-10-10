#!/usr/bin/env python3
# manage_ledger.py
import argparse
from datetime import datetime
from typing import Optional

from dedupe import _conn, purge_old_files, purge_old_semantic  # usa a conexão do módulo

try:
    from tabulate import tabulate
    _TAB = True
except Exception:
    _TAB = False


def _print(rows, headers):
    if _TAB:
        print(tabulate(rows, headers=headers, tablefmt="github"))
    else:
        print(headers)
        for r in rows:
            print(r)


# -----------------------------
# Listagens
# -----------------------------
def list_files(limit: Optional[int] = None):
    with _conn() as con:
        sql = """
          SELECT hash, nome_arquivo, tipo, data_iso, valor_centavos, created_at
            FROM processed_files
           ORDER BY datetime(created_at) DESC
        """
        if limit:
            sql += " LIMIT ?"
            rows = con.execute(sql, (int(limit),)).fetchall()
        else:
            rows = con.execute(sql).fetchall()
    _print(rows, ["hash", "nome_arquivo", "tipo", "data_iso", "valor_centavos", "created_at"])


def list_semantic(limit: Optional[int] = None):
    with _conn() as con:
        sql = """
          SELECT tipo, data_iso_min, valor_centavos, created_at
            FROM processed_semantic
           ORDER BY datetime(created_at) DESC
        """
        if limit:
            sql += " LIMIT ?"
            rows = con.execute(sql, (int(limit),)).fetchall()
        else:
            rows = con.execute(sql).fetchall()
    _print(rows, ["tipo", "data_iso_min", "valor_centavos", "created_at"])


# -----------------------------
# Busca (LIKE)
# -----------------------------
def find_files(term: str):
    like = f"%{term}%"
    with _conn() as con:
        rows = con.execute(
            """
            SELECT hash, nome_arquivo, tipo, data_iso, valor_centavos, created_at
              FROM processed_files
             WHERE hash LIKE ?
                OR IFNULL(nome_arquivo,'') LIKE ?
                OR IFNULL(tipo,'') LIKE ?
                OR IFNULL(data_iso,'') LIKE ?
            ORDER BY datetime(created_at) DESC
            """,
            (like, like, like, like),
        ).fetchall()
    _print(rows, ["hash", "nome_arquivo", "tipo", "data_iso", "valor_centavos", "created_at"])


def find_semantic(term: str):
    like = f"%{term}%"
    with _conn() as con:
        rows = con.execute(
            """
            SELECT tipo, data_iso_min, valor_centavos, created_at
              FROM processed_semantic
             WHERE IFNULL(tipo,'') LIKE ?
                OR IFNULL(data_iso_min,'') LIKE ?
                OR CAST(valor_centavos AS TEXT) LIKE ?
            ORDER BY datetime(created_at) DESC
            """,
            (like, like, like),
        ).fetchall()
    _print(rows, ["tipo", "data_iso_min", "valor_centavos", "created_at"])


# -----------------------------
# Exclusão
# -----------------------------
def delete_files(term: str, yes: bool = False):
    like = f"%{term}%"
    with _conn() as con:
        to_del = con.execute(
            """
            SELECT rowid, hash, nome_arquivo, tipo, data_iso, valor_centavos, created_at
              FROM processed_files
             WHERE hash LIKE ?
                OR IFNULL(nome_arquivo,'') LIKE ?
                OR IFNULL(tipo,'') LIKE ?
                OR IFNULL(data_iso,'') LIKE ?
            """,
            (like, like, like, like),
        ).fetchall()

        if not to_del:
            print("Nada encontrado em processed_files.")
            return

        _print(to_del, ["rowid", "hash", "nome_arquivo", "tipo", "data_iso", "valor_centavos", "created_at"])

        if yes or input("Confirma exclusão destes registros? (y/N) ").lower().startswith("y"):
            ids = [(r[0],) for r in to_del]
            con.executemany("DELETE FROM processed_files WHERE rowid = ?", ids)
            print(f"{len(ids)} registro(s) removido(s) de processed_files.")


def delete_semantic(term: str, yes: bool = False):
    like = f"%{term}%"
    with _conn() as con:
        to_del = con.execute(
            """
            SELECT rowid, tipo, data_iso_min, valor_centavos, created_at
              FROM processed_semantic
             WHERE IFNULL(tipo,'') LIKE ?
                OR IFNULL(data_iso_min,'') LIKE ?
                OR CAST(valor_centavos AS TEXT) LIKE ?
            """,
            (like, like, like),
        ).fetchall()

        if not to_del:
            print("Nada encontrado em processed_semantic.")
            return

        _print(to_del, ["rowid", "tipo", "data_iso_min", "valor_centavos", "created_at"])

        if yes or input("Confirma exclusão destes registros? (y/N) ").lower().startswith("y"):
            ids = [(r[0],) for r in to_del]
            con.executemany("DELETE FROM processed_semantic WHERE rowid = ?", ids)
            print(f"{len(ids)} registro(s) removido(s) de processed_semantic.")


# -----------------------------
# Stats / manutenção
# -----------------------------
def stats():
    with _conn() as con:
        f = con.execute("SELECT COUNT(1) FROM processed_files").fetchone()[0]
        s = con.execute("SELECT COUNT(1) FROM processed_semantic").fetchone()[0]
        last_f = con.execute(
            "SELECT IFNULL(MAX(datetime(created_at)), '-') FROM processed_files"
        ).fetchone()[0]
        last_s = con.execute(
            "SELECT IFNULL(MAX(datetime(created_at)), '-') FROM processed_semantic"
        ).fetchone()[0]
    print("processed_files:", f, "| last:", last_f)
    print("processed_semantic:", s, "| last:", last_s)


def vacuum():
    with _conn() as con:
        con.execute("VACUUM;")
    print("VACUUM concluído.")


def purge(days: int, which: str):
    if which in ("files", "all"):
        n = purge_old_files(days)
        print(f"processed_files: {n} registro(s) antigos removidos (> {days}d).")
    if which in ("semantic", "all"):
        n = purge_old_semantic(days)
        print(f"processed_semantic: {n} registro(s) antigos removidos (> {days}d).")


# -----------------------------
# CLI
# -----------------------------
def main():
    ap = argparse.ArgumentParser(description="Gerencia o ledger (processed_files / processed_semantic)")
    sub = ap.add_subparsers(dest="cmd")

    # listagens
    p_list = sub.add_parser("list", help="Lista processed_files")
    p_list.add_argument("--limit", type=int, default=None)

    p_list_sem = sub.add_parser("list-sem", help="Lista processed_semantic")
    p_list_sem.add_argument("--limit", type=int, default=None)

    # buscas
    p_find = sub.add_parser("find", help="Busca em processed_files (LIKE)")
    p_find.add_argument("term")

    p_find_sem = sub.add_parser("find-sem", help="Busca em processed_semantic (LIKE)")
    p_find_sem.add_argument("term")

    # deletes
    p_del = sub.add_parser("delete", help="Apaga de processed_files por termo (LIKE)")
    p_del.add_argument("term")
    p_del.add_argument("--yes", action="store_true")

    p_del_sem = sub.add_parser("delete-sem", help="Apaga de processed_semantic por termo (LIKE)")
    p_del_sem.add_argument("term")
    p_del_sem.add_argument("--yes", action="store_true")

    # manutenção
    sub.add_parser("stats", help="Mostra contagens e últimos registros")
    sub.add_parser("vacuum", help="Executa VACUUM")

    p_purge = sub.add_parser("purge", help="Remove registros antigos")
    p_purge.add_argument("--days", type=int, default=180)
    p_purge.add_argument("--which", choices=["files", "semantic", "all"], default="all")

    args = ap.parse_args()

    if args.cmd == "list":
        list_files(args.limit)
    elif args.cmd == "list-sem":
        list_semantic(args.limit)
    elif args.cmd == "find":
        find_files(args.term)
    elif args.cmd == "find-sem":
        find_semantic(args.term)
    elif args.cmd == "delete":
        delete_files(args.term, args.yes)
    elif args.cmd == "delete-sem":
        delete_semantic(args.term, args.yes)
    elif args.cmd == "stats":
        stats()
    elif args.cmd == "vacuum":
        vacuum()
    elif args.cmd == "purge":
        purge(args.days, args.which)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
