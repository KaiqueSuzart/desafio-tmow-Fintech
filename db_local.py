"""db_local.py — Persistência SQLite para o Extrator de Tabelas HTML."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def default_db_path(base_dir: Path | None = None) -> Path:
    base = base_dir or Path(__file__).resolve().parent
    data_dir = base / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "portal_lista.db"


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or default_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS consultas (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            criado_em       TEXT    NOT NULL,
            titulo          TEXT    NOT NULL DEFAULT '',
            url_final       TEXT    NOT NULL DEFAULT '',
            periodo_de      TEXT    NOT NULL DEFAULT '',
            periodo_ate     TEXT    NOT NULL DEFAULT '',
            tamanho_pagina  INTEGER NOT NULL DEFAULT 0,
            paginas_buscadas INTEGER NOT NULL DEFAULT 0,
            total_linhas    INTEGER NOT NULL DEFAULT 0,
            colunas_json    TEXT    NOT NULL DEFAULT '[]',
            opcoes_json     TEXT    NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS linhas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            consulta_id INTEGER NOT NULL,
            ordem       INTEGER NOT NULL DEFAULT 0,
            dados_json  TEXT    NOT NULL DEFAULT '{}',
            FOREIGN KEY (consulta_id) REFERENCES consultas(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_linhas_consulta ON linhas(consulta_id, ordem);
    """)
    conn.commit()


def agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def inserir_consulta(
    conn: sqlite3.Connection,
    *,
    titulo: str = "",
    url_final: str = "",
    periodo_de: str = "",
    periodo_ate: str = "",
    tamanho_pagina: int = 0,
    paginas_buscadas: int = 0,
    colunas: list[str],
    linhas: list[dict[str, Any]],
    opcoes: dict | None = None,
) -> int:
    criado = agora_iso()
    cur = conn.execute(
        """INSERT INTO consultas
           (criado_em, titulo, url_final, periodo_de, periodo_ate,
            tamanho_pagina, paginas_buscadas, total_linhas,
            colunas_json, opcoes_json)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            criado,
            titulo,
            url_final,
            periodo_de,
            periodo_ate,
            tamanho_pagina,
            paginas_buscadas,
            len(linhas),
            json.dumps(colunas, ensure_ascii=False),
            json.dumps(opcoes or {}, ensure_ascii=False),
        ),
    )
    cid = cur.lastrowid
    for idx, row in enumerate(linhas):
        conn.execute(
            "INSERT INTO linhas (consulta_id, ordem, dados_json) VALUES (?,?,?)",
            (cid, idx, json.dumps(row, ensure_ascii=False)),
        )
    conn.commit()
    return cid  # type: ignore[return-value]


def listar_consultas(conn: sqlite3.Connection, limite: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM consultas ORDER BY id DESC LIMIT ?", (limite,)
    ).fetchall()
    return [dict(r) for r in rows]


def obter_consulta_com_linhas(conn: sqlite3.Connection, cid: int) -> dict | None:
    row = conn.execute("SELECT * FROM consultas WHERE id=?", (cid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["colunas"] = json.loads(d.pop("colunas_json", "[]"))
    d["opcoes"] = json.loads(d.pop("opcoes_json", "{}"))
    linhas = conn.execute(
        "SELECT dados_json FROM linhas WHERE consulta_id=? ORDER BY ordem",
        (cid,),
    ).fetchall()
    d["rows"] = [json.loads(r["dados_json"]) for r in linhas]
    return d


def eliminar_consulta(conn: sqlite3.Connection, cid: int) -> bool:
    cur = conn.execute("DELETE FROM consultas WHERE id=?", (cid,))
    conn.commit()
    return cur.rowcount > 0


def atualizar_titulo_consulta(conn: sqlite3.Connection, cid: int, titulo: str) -> bool:
    cur = conn.execute("UPDATE consultas SET titulo=? WHERE id=?", (titulo, cid))
    conn.commit()
    return cur.rowcount > 0


def substituir_dados_consulta(
    conn: sqlite3.Connection,
    cid: int,
    titulo: str,
    colunas: list[str],
    linhas: list[dict[str, Any]],
) -> bool:
    existing = conn.execute("SELECT id FROM consultas WHERE id=?", (cid,)).fetchone()
    if not existing:
        return False
    conn.execute(
        """UPDATE consultas
           SET titulo=?, colunas_json=?, total_linhas=?
           WHERE id=?""",
        (titulo, json.dumps(colunas, ensure_ascii=False), len(linhas), cid),
    )
    conn.execute("DELETE FROM linhas WHERE consulta_id=?", (cid,))
    for idx, row in enumerate(linhas):
        conn.execute(
            "INSERT INTO linhas (consulta_id, ordem, dados_json) VALUES (?,?,?)",
            (cid, idx, json.dumps(row, ensure_ascii=False)),
        )
    conn.commit()
    return True
