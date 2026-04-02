"""app.py — API FastAPI + servir estáticos para o Extrator de Tabelas HTML."""

from __future__ import annotations

import asyncio
import io
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import ai_suggest
import db_local
import scrape_lista

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = FastAPI(title="Extrator de Tabelas HTML")

WEB_DIR = BASE_DIR / "web"
STATIC_DIR = WEB_DIR / "static"

# ---------------------------------------------------------------------------
# DB — inicialização
# ---------------------------------------------------------------------------
_db_path = db_local.default_db_path(BASE_DIR)

def _get_conn() -> sqlite3.Connection:
    conn = db_local._connect(_db_path)
    db_local.init_db(conn)
    return conn

# ---------------------------------------------------------------------------
# Modelos Pydantic
# ---------------------------------------------------------------------------

class ScrapeBody(BaseModel):
    url: str
    wait_selector: str = "table"
    iframe_auto: bool = False
    iframe_selector: Optional[str] = None
    table_index: int = 0
    skip_first_column: bool = False
    prefer_table_id: Optional[str] = None
    paginacao_portal: bool = False
    tamanho_pagina: int = Field(default=5, ge=1, le=500)
    max_paginas: int = Field(default=1, ge=1, le=500)
    buscar_todas_paginas: bool = False
    periodo_de: Optional[str] = None
    periodo_ate: Optional[str] = None
    extra_query: Optional[dict[str, str]] = None


class AiSuggestBody(BaseModel):
    url: str
    intent: str
    form_fields: list[dict] = []


class SalvarBody(BaseModel):
    titulo: str = ""
    url_final: str = ""
    periodo_de: str = ""
    periodo_ate: str = ""
    tamanho_pagina: int = 0
    paginas_buscadas: int = 0
    colunas: list[str] = []
    linhas: list[dict] = []


class ConsultaTituloPatch(BaseModel):
    titulo: str


class ConsultaPutBody(BaseModel):
    titulo: str = ""
    colunas: list[str] = []
    linhas: list[dict] = []


class ConsultaPostBody(BaseModel):
    titulo: str = ""
    colunas: list[str] = []
    linhas: list[dict] = []


class ScrapeDetailsBody(BaseModel):
    urls: list[str]
    wait_selector: str = "body"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_portal_transparencia_url(url: str) -> bool:
    return "portaldatransparencia.gov.br" in url.lower()


def _normalize_iframe_selector(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    if raw == "__auto__":
        return "__auto__"
    if raw[0] in ("#", ".", "["):
        return raw
    if raw.startswith("iframe"):
        return raw
    if re.match(r"^[a-zA-Z][\w-]*$", raw):
        return f"#{raw}"
    return raw


def _sanitize_extra_query(extra: dict | None) -> dict[str, str]:
    if not extra:
        return {}
    _null = {"none", "null", "undefined", "nan", ""}
    return {k: str(v) for k, v in extra.items() if v is not None and str(v).strip().lower() not in _null}


def _preparar_url_scrape(body: ScrapeBody) -> tuple[str, int]:
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=422, detail="URL não pode ser vazia.")

    if body.periodo_de and body.periodo_ate:
        if body.periodo_de > body.periodo_ate:
            raise HTTPException(status_code=422, detail="Período 'de' deve ser <= 'até'.")
        de_str, ate_str = scrape_lista.mes_para_de_ate(body.periodo_de, body.periodo_ate)
        url = scrape_lista.merge_query_params(url, {"de": de_str, "ate": ate_str})
    elif body.periodo_de or body.periodo_ate:
        raise HTTPException(status_code=422, detail="Informe ambos os campos de período ou nenhum.")

    extra = _sanitize_extra_query(body.extra_query)

    tam_efetivo = body.tamanho_pagina
    for key in ("tamanhoPagina", "tamanho_pagina"):
        if key in extra:
            try:
                tam_efetivo = int(extra[key])
            except ValueError:
                pass
            break

    if extra:
        url = scrape_lista.merge_query_params(url, extra)

    if _is_portal_transparencia_url(url) and body.paginacao_portal:
        url = scrape_lista.merge_query_params(url, {
            "paginacaoSimples": "true",
            "tamanhoPagina": str(tam_efetivo),
        })

    return url, tam_efetivo


def _prefer_id(body: ScrapeBody) -> str:
    if body.prefer_table_id:
        return body.prefer_table_id
    if _is_portal_transparencia_url(body.url):
        return "lista"
    return ""


def _resolved_table_index(todas: list[dict], table_index: int, prefer_id: str) -> int:
    if prefer_id:
        for t in todas:
            if t.get("element_id") == prefer_id:
                return t["index"]
    if table_index < len(todas):
        return table_index
    return 0


def _headers_com_meta(headers: list[str], has_pagination: bool) -> list[str]:
    if has_pagination and "_pagina" not in headers:
        return headers + ["_pagina", "_linha"]
    return headers


def _tables_response_payload(
    todas: list[dict], selected_idx: int
) -> list[dict]:
    result = []
    for t in todas:
        entry: dict[str, Any] = {
            "index": t["index"],
            "element_id": t.get("element_id", ""),
            "class": t.get("class", ""),
            "headers": t["headers"],
            "row_count": t["row_count"],
        }
        if t["index"] == selected_idx:
            entry["rows"] = t.get("rows", [])
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/ai/suggest-filters")
async def api_ai_suggest(body: AiSuggestBody):
    result = await asyncio.to_thread(ai_suggest.suggest_filters_ai, body.url, body.intent, body.form_fields)
    return result


@app.post("/api/scrape")
async def api_scrape(body: ScrapeBody):
    try:
        url, tam_efetivo = _preparar_url_scrape(body)

        iframe_sel: str | None = None
        if body.iframe_auto:
            iframe_sel = "__auto__"
        elif body.iframe_selector:
            iframe_sel = _normalize_iframe_selector(body.iframe_selector)
            if iframe_sel == "__auto__":
                pass

        ws = body.wait_selector or "table"
        pid = _prefer_id(body)
        is_portal = _is_portal_transparencia_url(body.url)
        is_epublica = scrape_lista._is_epublica_url(body.url)

        use_paginada = (
            is_portal
            and body.paginacao_portal
            and (body.buscar_todas_paginas or body.max_paginas > 1)
        )

        # e-publica — listagem de contratos: sem isto fica só 1 página (~25 linhas) e o utilizador não vê o pedido cumprido
        url_low = body.url.lower()
        is_epublica_contrato_lista = is_epublica and "contratotable" in url_low.replace("_", "")

        use_epublica_paginada = False
        max_epublica_pages = 1
        if is_epublica_contrato_lista:
            use_epublica_paginada = True
            if body.buscar_todas_paginas:
                max_epublica_pages = 500
            else:
                # Respeitar sempre o número no formulário (ex.: 1 = só a 1.ª página).
                max_epublica_pages = max(1, min(500, body.max_paginas))
        elif is_epublica and (body.buscar_todas_paginas or body.max_paginas > 1):
            use_epublica_paginada = True
            max_epublica_pages = body.max_paginas if not body.buscar_todas_paginas else 500

        if is_portal and ws in ("table", ".table", "#table"):
            ws = "#lista"

        if use_paginada:
            max_pag = body.max_paginas if not body.buscar_todas_paginas else 500
            todas, headers, rows, ti = await asyncio.to_thread(
                scrape_lista.buscar_tabelas_url_paginada,
                url,
                wait_selector=ws,
                table_index=body.table_index,
                prefer_table_id=pid,
                skip_first_column=body.skip_first_column,
                tamanho_pagina=tam_efetivo,
                max_paginas=max_pag,
                iframe_selector=iframe_sel,
            )
            paginas_buscadas = max(1, len(set(r.get("_pagina", "1") for r in rows)))
            headers = _headers_com_meta(headers, True)
        elif use_epublica_paginada:
            todas, headers, rows, ti = await asyncio.to_thread(
                scrape_lista.buscar_tabelas_epublica_paginada,
                url,
                wait_selector=ws,
                table_index=body.table_index,
                prefer_table_id=pid or "",
                skip_first_column=body.skip_first_column,
                iframe_selector=iframe_sel,
                max_paginas=max_epublica_pages,
                tamanho_pagina=tam_efetivo,
            )
            paginas_buscadas = max(1, len(set(r.get("_pagina", "1") for r in rows)))
            headers = _headers_com_meta(headers, True)
        else:
            todas, headers, rows, ti = await asyncio.to_thread(
                scrape_lista.buscar_tabelas_url,
                url,
                wait_selector=ws,
                table_index=body.table_index,
                prefer_table_id=pid,
                skip_first_column=body.skip_first_column,
                iframe_selector=iframe_sel,
                epublica_tamanho_pagina=tam_efetivo if is_epublica else None,
            )
            paginas_buscadas = 1

        tables_payload = _tables_response_payload(todas, ti)

        return {
            "tables": tables_payload,
            "selected_table_index": ti,
            "headers": headers,
            "rows": rows,
            "count": len(rows),
            "url_final": url,
            "paginas_buscadas": paginas_buscadas,
            "tamanho_pagina": tam_efetivo,
            "periodo_de": body.periodo_de or "",
            "periodo_ate": body.periodo_ate or "",
        }
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erro na extração: {exc}")


@app.post("/api/scrape-details")
async def api_scrape_details(body: ScrapeDetailsBody):
    if not body.urls:
        raise HTTPException(status_code=422, detail="Lista de URLs vazia.")
    if len(body.urls) > 200:
        raise HTTPException(status_code=422, detail="Máximo 200 URLs por pedido.")
    try:
        details = await asyncio.to_thread(
            scrape_lista.buscar_detalhes_urls, body.urls, body.wait_selector
        )
        return {"ok": True, "details": details, "count": len(details)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar detalhes: {exc}")


@app.post("/api/salvar")
async def api_salvar(body: SalvarBody):
    conn = _get_conn()
    try:
        cid = db_local.inserir_consulta(
            conn,
            titulo=body.titulo,
            url_final=body.url_final,
            periodo_de=body.periodo_de,
            periodo_ate=body.periodo_ate,
            tamanho_pagina=body.tamanho_pagina,
            paginas_buscadas=body.paginas_buscadas,
            colunas=body.colunas,
            linhas=body.linhas,
        )
        return {"ok": True, "id": cid}
    finally:
        conn.close()


@app.get("/api/consultas")
async def api_listar_consultas(limite: int = 50):
    conn = _get_conn()
    try:
        return db_local.listar_consultas(conn, limite)
    finally:
        conn.close()


@app.get("/api/consultas/{cid}")
async def api_consulta_detalhe(cid: int):
    conn = _get_conn()
    try:
        data = db_local.obter_consulta_com_linhas(conn, cid)
        if not data:
            raise HTTPException(status_code=404, detail="Consulta não encontrada.")
        return data
    finally:
        conn.close()


@app.post("/api/consultas")
async def api_criar_consulta(body: ConsultaPostBody):
    conn = _get_conn()
    try:
        cid = db_local.inserir_consulta(
            conn, titulo=body.titulo, colunas=body.colunas, linhas=body.linhas,
        )
        return {"ok": True, "id": cid}
    finally:
        conn.close()


@app.patch("/api/consultas/{cid}")
async def api_patch_titulo(cid: int, body: ConsultaTituloPatch):
    conn = _get_conn()
    try:
        ok = db_local.atualizar_titulo_consulta(conn, cid, body.titulo)
        if not ok:
            raise HTTPException(status_code=404, detail="Consulta não encontrada.")
        return {"ok": True}
    finally:
        conn.close()


@app.put("/api/consultas/{cid}")
async def api_put_consulta(cid: int, body: ConsultaPutBody):
    conn = _get_conn()
    try:
        ok = db_local.substituir_dados_consulta(conn, cid, body.titulo, body.colunas, body.linhas)
        if not ok:
            raise HTTPException(status_code=404, detail="Consulta não encontrada.")
        return {"ok": True}
    finally:
        conn.close()


@app.delete("/api/consultas/{cid}")
async def api_delete_consulta(cid: int):
    conn = _get_conn()
    try:
        ok = db_local.eliminar_consulta(conn, cid)
        if not ok:
            raise HTTPException(status_code=404, detail="Consulta não encontrada.")
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/consultas/{cid}/excel")
async def api_export_excel(cid: int):
    conn = _get_conn()
    try:
        data = db_local.obter_consulta_com_linhas(conn, cid)
        if not data:
            raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    finally:
        conn.close()

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = (data.get("titulo") or "Dados")[:31]

    colunas = data.get("colunas", [])
    ws.append(colunas)

    for row in data.get("rows", []):
        ws.append([row.get(c, "") for c in colunas])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"consulta_{cid}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Servir frontend
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR)), name="assets")
