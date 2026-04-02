"""scrape_lista.py — Scraper de tabelas HTML com Playwright + BeautifulSoup."""

from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import sys
from itertools import zip_longest
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
IFRAME_SELECTOR_AUTO = "__auto__"

CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _nav_timeout_ms() -> int:
    raw = os.getenv("PLAYWRIGHT_NAV_TIMEOUT_MS", "120000")
    try:
        val = int(raw)
    except ValueError:
        val = 120_000
    return max(30_000, min(300_000, val))


NAV_TIMEOUT_MS: int = _nav_timeout_ms()

# ---------------------------------------------------------------------------
# Utilitários de URL / datas
# ---------------------------------------------------------------------------

def merge_query_params(url: str, updates: dict[str, str | None]) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    for k, v in updates.items():
        if v is None:
            qs.pop(k, None)
        else:
            qs[k] = [v]
    new_query = urlencode({k: v[0] for k, v in qs.items()}, doseq=False)
    return urlunparse(parsed._replace(query=new_query))


def mes_para_de_ate(periodo_de: str, periodo_ate: str) -> tuple[str, str]:
    """Converte 'YYYY-MM' → ('dd/mm/aaaa', 'dd/mm/aaaa') em formato BR."""
    y1, m1 = map(int, periodo_de.split("-"))
    y2, m2 = map(int, periodo_ate.split("-"))
    ultimo_dia = calendar.monthrange(y2, m2)[1]
    de_str = f"01/{m1:02d}/{y1:04d}"
    ate_str = f"{ultimo_dia:02d}/{m2:02d}/{y2:04d}"
    return de_str, ate_str


# ---------------------------------------------------------------------------
# Extração BeautifulSoup (parsing puro, sem browser)
# ---------------------------------------------------------------------------

def _row_cells(tr: Tag) -> list[str]:
    return [cell.get_text(strip=True) for cell in tr.find_all(["th", "td"], recursive=False)]


def _unique_headers(labels: list[str]) -> list[str]:
    result: list[str] = []
    seen: dict[str, int] = {}
    for raw in labels:
        name = raw if raw else "coluna"
        if name in seen:
            seen[name] += 1
            name = f"{name} #{seen[name]}"
        else:
            seen[name] = 1
        result.append(name)
    return result


def _is_nested_table(table: Tag) -> bool:
    parent = table.parent
    while parent:
        if parent.name == "table":
            return True
        parent = parent.parent
    return False


def extrair_uma_tabela_generica(
    table: Tag, skip_first_column: bool = False
) -> tuple[list[str], list[dict[str, str]]]:
    thead = table.find("thead")
    tbody = table.find("tbody") or table

    if thead:
        header_row = thead.find("tr")
        raw_headers = _row_cells(header_row) if header_row else []
        data_trs = tbody.find_all("tr", recursive=False) if table.find("tbody") else [
            tr for tr in table.find_all("tr", recursive=False)
            if tr.parent != thead
        ]
    else:
        all_trs = table.find_all("tr", recursive=False)
        if not all_trs:
            return [], []
        raw_headers = _row_cells(all_trs[0])
        data_trs = all_trs[1:]

    if skip_first_column and raw_headers:
        raw_headers = raw_headers[1:]

    headers = _unique_headers(raw_headers)
    rows: list[dict[str, str]] = []
    for tr in data_trs:
        vals = _row_cells(tr)
        if skip_first_column and vals:
            vals = vals[1:]
        row = dict(zip_longest(headers, vals, fillvalue=""))
        rows.append(row)
    return headers, rows


def extrair_todas_tabelas_top_level(
    html: str, skip_first_column: bool = False
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    result: list[dict[str, Any]] = []
    idx = 0
    for tbl in tables:
        if _is_nested_table(tbl):
            continue
        headers, rows = extrair_uma_tabela_generica(tbl, skip_first_column)
        result.append({
            "index": idx,
            "element_id": tbl.get("id", ""),
            "class": " ".join(tbl.get("class", [])),
            "headers": headers,
            "rows": rows,
            "row_count": len(rows),
        })
        idx += 1
    return result


# ---------------------------------------------------------------------------
# Navegação Playwright
# ---------------------------------------------------------------------------

def _apply_timeouts_page(page: Any) -> None:
    page.set_default_timeout(NAV_TIMEOUT_MS)
    page.set_default_navigation_timeout(NAV_TIMEOUT_MS)


def _log_default(msg: str) -> None:
    print(f"  [scraper] {msg}")


def _is_tryit_url(url: str) -> bool:
    low = url.lower()
    return "w3schools" in low and "tryit" in low


def _is_portal_url(url: str) -> bool:
    return "portaldatransparencia.gov.br" in url.lower()


def _is_epublica_url(url: str) -> bool:
    return "e-publica.net" in url.lower()


# --- Iframe manual ---

def _root_page_or_iframe(page: Any, iframe_selector: str, log: Callable) -> Any:
    log(f"Iframe manual: esperando seletor '{iframe_selector}'")
    page.wait_for_selector(iframe_selector, timeout=60_000)
    el = page.query_selector(iframe_selector)
    if not el:
        raise ValueError(f"Iframe '{iframe_selector}' não encontrado")
    frame = el.content_frame()
    if not frame:
        raise ValueError(f"content_frame() retornou None para '{iframe_selector}'")
    return frame


def _search_subtree_for_selector(root_frame: Any, ws: str, timeout_ms: int, log: Callable) -> Any | None:
    """BFS nos child_frames a partir de root_frame até encontrar ws."""
    queue = [root_frame]
    while queue:
        frame = queue.pop(0)
        try:
            frame.wait_for_selector(ws, timeout=timeout_ms)
            log(f"Seletor '{ws}' encontrado num sub-frame")
            return frame
        except Exception:
            pass
        queue.extend(frame.child_frames)
    return None


# --- Detecção automática de iframe ---

def _enumerate_frames_in_subtree(page: Any, subtree_root: Any | None = None) -> list:
    all_frames = page.frames
    if subtree_root is None or subtree_root == page:
        return all_frames
    result = []
    queue = [subtree_root]
    while queue:
        f = queue.pop(0)
        result.append(f)
        queue.extend(f.child_frames)
    return result


def _try_frames_for_selector(frames: list, ws: str, timeout_ms: int, log: Callable) -> Any | None:
    for frame in frames:
        try:
            frame.wait_for_selector(ws, timeout=timeout_ms)
            log(f"Seletor '{ws}' encontrado num frame")
            return frame
        except Exception:
            continue
    return None


def _root_page_auto_iframe(page: Any, ws: str, log: Callable, prefer_iframes_first: bool = False) -> Any:
    all_frames = page.frames
    n = len(all_frames)
    timeout_per = max(10_000, min(25_000, 180_000 // max(n, 1)))
    main_frame = all_frames[0] if all_frames else page
    other_frames = all_frames[1:] if len(all_frames) > 1 else []

    if prefer_iframes_first and other_frames:
        log(f"Auto-iframe (iframe-first): testando {len(other_frames)} iframes")
        found = _try_frames_for_selector(other_frames, ws, timeout_per, log)
        if found:
            return found
        log("Nenhum iframe teve o seletor, tentando frame principal")
        found = _try_frames_for_selector([main_frame], ws, timeout_per, log)
        if found:
            return found
    else:
        log(f"Auto-iframe: testando frame principal + {len(other_frames)} iframes")
        found = _try_frames_for_selector([main_frame], ws, timeout_per, log)
        if found:
            return found
        if other_frames:
            found = _try_frames_for_selector(other_frames, ws, timeout_per, log)
            if found:
                return found

    return page


# --- Atalho W3Schools Tryit ---

def _try_w3schools_tryit_iframe_result(page: Any, ws: str, log: Callable) -> tuple[Any | None, bool]:
    try:
        log("Tryit W3Schools: esperando #iframeResult (60s)")
        page.wait_for_selector("#iframeResult", timeout=60_000)
        log("Executando editor.save() + submitTryit(1)")
        page.evaluate("""() => {
            try { window.editor.save(); } catch(e) {}
            try { submitTryit(1); } catch(e) {}
        }""")
        page.wait_for_timeout(2000)
        el = page.query_selector("#iframeResult")
        if not el:
            return None, False
        frame = el.content_frame()
        if not frame:
            return None, False
        log(f"Esperando '{ws}' dentro do iframe Tryit (90s)")
        frame.wait_for_selector(ws, timeout=90_000)
        return frame, True
    except Exception as exc:
        log(f"Tryit fallback: {exc}")
        return None, False


# --- Orquestrador ---

def _resolve_scrape_root(
    page: Any, iframe_selector: str | None, ws: str, log: Callable,
    prefer_iframes_first: bool = False,
) -> tuple[Any, bool]:
    """Retorna (root_document, já_encontrou_ws)."""
    if iframe_selector == IFRAME_SELECTOR_AUTO:
        root = _root_page_auto_iframe(page, ws, log, prefer_iframes_first)
        return root, False
    if iframe_selector:
        root = _root_page_or_iframe(page, iframe_selector, log)
        found = _search_subtree_for_selector(root, ws, 15_000, log)
        if found:
            return found, True
        return root, False
    return page, False


def _goto_pagina_e_html(
    page: Any, url: str, wait_selector: str, *,
    iframe_selector: str | None = None,
    log: Callable = _log_default,
) -> str:
    _apply_timeouts_page(page)

    is_tryit = _is_tryit_url(url)
    is_portal = _is_portal_url(url)
    is_epublica = _is_epublica_url(url)
    prefer_iframes_first = is_tryit

    if is_tryit:
        wait_until = "load"
    elif is_epublica:
        wait_until = "networkidle"
    else:
        wait_until = "domcontentloaded"

    log(f"Navegando (wait_until={wait_until}): {url[:120]}")
    page.goto(url, wait_until=wait_until, timeout=NAV_TIMEOUT_MS)

    root = page
    ws_found = False

    if is_tryit:
        tryit_frame, ok = _try_w3schools_tryit_iframe_result(page, wait_selector, log)
        if ok and tryit_frame:
            root = tryit_frame
            ws_found = True

    if not ws_found:
        root, ws_found = _resolve_scrape_root(
            page, iframe_selector, wait_selector, log, prefer_iframes_first
        )

    if not ws_found:
        frames = _enumerate_frames_in_subtree(page, root if root != page else None)
        other = [f for f in frames if f != root]
        if other:
            found = _try_frames_for_selector(other, wait_selector, 10_000, log)
            if found:
                root = found
                ws_found = True

    if not ws_found:
        try:
            log(f"Esperando seletor '{wait_selector}' no root (90s)")
            root.wait_for_selector(wait_selector, timeout=90_000)
        except Exception as exc:
            raise ValueError(
                f"Seletor '{wait_selector}' não encontrado após 90s. "
                f"Verifique a URL e o seletor. Detalhe: {exc}"
            )

    if wait_selector == "#lista" or is_portal:
        try:
            log("Estabilização Portal: networkidle + linhas no tbody")
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            pass
        try:
            root.wait_for_selector("#lista tbody tr", timeout=30_000)
        except Exception:
            pass
    elif is_epublica:
        try:
            log("Estabilização SPA e-publica: networkidle + tempo extra")
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            pass
        page.wait_for_timeout(2500)
        try:
            root.wait_for_selector("table tbody tr, table tr", timeout=15_000)
        except Exception:
            pass
    else:
        try:
            root.wait_for_selector("table", timeout=15_000)
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

    return root.content()


# ---------------------------------------------------------------------------
# Extração via JS para tabelas SPA (Angular / e-publica)
# ---------------------------------------------------------------------------

def _extract_tables_via_js(
    page: Any, skip_first_column: bool = False, log: Callable = _log_default
) -> list[dict[str, Any]]:
    """Extrai tabelas usando page.evaluate() — lê só elementos visíveis do DOM renderizado."""
    raw = page.evaluate("""() => {
        const allTables = document.querySelectorAll('table');
        const results = [];

        for (const table of allTables) {
            let p = table.parentElement;
            let nested = false;
            while (p) {
                if (p.tagName === 'TABLE') { nested = true; break; }
                p = p.parentElement;
            }
            if (nested) continue;

            let headers = [];
            const thead = table.querySelector('thead');
            if (thead) {
                let bestRow = [];
                for (const tr of thead.querySelectorAll('tr')) {
                    const st = window.getComputedStyle(tr);
                    if (st.display === 'none' || tr.classList.contains('ng-hide')) continue;
                    const rowCells = [];
                    for (const cell of tr.querySelectorAll('th, td')) {
                        const cs = window.getComputedStyle(cell);
                        if (cs.display === 'none' || cell.classList.contains('ng-hide')) continue;
                        rowCells.push(cell.textContent.trim());
                    }
                    if (rowCells.length > bestRow.length) {
                        bestRow = rowCells;
                    }
                }
                headers = bestRow;
            }

            const dataRows = [];
            let dataTitleHeaders = [];
            const tbody = table.querySelector('tbody') || table;
            for (const tr of tbody.querySelectorAll(':scope > tr')) {
                const st = window.getComputedStyle(tr);
                if (st.display === 'none' || tr.classList.contains('ng-hide')) continue;

                const cells = [];
                const rowDt = [];
                for (const cell of tr.querySelectorAll(':scope > td')) {
                    const cs = window.getComputedStyle(cell);
                    if (cs.display === 'none' || cell.classList.contains('ng-hide')) continue;
                    cells.push(cell.textContent.trim());
                    const raw = cell.getAttribute('data-title') || '';
                    if (raw) {
                        const tmp = document.createElement('div');
                        tmp.innerHTML = raw;
                        rowDt.push(tmp.textContent.trim());
                    } else {
                        rowDt.push('');
                    }
                }
                if (cells.length === 0) continue;
                dataRows.push(cells);
                if (dataTitleHeaders.length === 0 && rowDt.some(d => d)) {
                    dataTitleHeaders = rowDt;
                }
            }

            if (headers.length === 0 && dataTitleHeaders.length > 0) {
                headers = dataTitleHeaders;
            } else if (dataTitleHeaders.length > 0) {
                const dtNonEmpty = dataTitleHeaders.filter(h => h).length;
                const thNonEmpty = headers.filter(h => h).length;
                if (dtNonEmpty > thNonEmpty) {
                    headers = dataTitleHeaders;
                }
            }

            if (headers.length === 0) {
                const firstRow = (table.querySelector('tbody') || table).querySelector('tr');
                if (firstRow) {
                    for (const cell of firstRow.querySelectorAll('td')) {
                        const cs = window.getComputedStyle(cell);
                        if (cs.display === 'none' || cell.classList.contains('ng-hide')) continue;
                        headers.push('coluna');
                    }
                }
            }

            results.push({
                id: table.id || '',
                className: table.className || '',
                headers: headers,
                dataRows: dataRows
            });
        }
        return results;
    }""")

    result: list[dict[str, Any]] = []
    idx = 0
    for tbl in raw:
        headers = tbl["headers"]
        if skip_first_column and headers:
            headers = headers[1:]

        # Remove trailing empty headers (action columns like "Visualizar")
        while headers and not headers[-1]:
            headers.pop()

        headers = _unique_headers([h for h in headers if h])

        n_hdr = len(headers)
        rows: list[dict[str, str]] = []
        for cells in tbl["dataRows"]:
            if skip_first_column and cells:
                cells = cells[1:]

            # Strip trailing cells beyond header count (action/icon columns)
            effective_cells = cells[:n_hdr] if n_hdr > 0 else cells

            if n_hdr > 0 and len(cells) >= n_hdr - 1:
                row = dict(zip_longest(headers, effective_cells, fillvalue=""))
                rows.append(row)
            elif n_hdr > 0 and len(cells) < n_hdr - 1 and rows:
                desc = " ".join(c for c in cells if c)
                if desc:
                    rows[-1]["Objeto"] = desc

        # Strip duplicated header labels from cell values (e-publica responsive tables
        # render data-title as visible text inside each <td>)
        for row in rows:
            for hdr in headers:
                if not hdr:
                    continue
                val = row.get(hdr, "")
                if not val:
                    continue
                if val == hdr:
                    row[hdr] = ""
                elif val.startswith(hdr) and len(val) > len(hdr):
                    stripped = val[len(hdr):].strip()
                    row[hdr] = stripped

        final_headers = list(headers)
        if any("Objeto" in r for r in rows) and "Objeto" not in final_headers:
            final_headers.append("Objeto")

        result.append({
            "index": idx,
            "element_id": tbl["id"],
            "class": tbl["className"],
            "headers": final_headers,
            "rows": rows,
            "row_count": len(rows),
        })
        idx += 1

    log(f"Extração JS: {len(result)} tabela(s), "
        + ", ".join(f"#{t['index']}={t['row_count']} linhas" for t in result))
    return result


# ---------------------------------------------------------------------------
# Extração de links de detalhe das linhas da tabela
# ---------------------------------------------------------------------------

def _epublica_contract_id_from_url(url: str) -> str:
    """Extrai o campo JSON `id` do query param `params` (hash Angular e-publica)."""
    if not url or "params=" not in url:
        return ""
    try:
        frag = url.split("#", 1)[-1] if "#" in url else url
        if "?" not in frag:
            return ""
        q = frag.split("?", 1)[1]
        for pair in q.split("&"):
            if not pair.startswith("params="):
                continue
            enc = pair.split("=", 1)[1]
            raw = unquote(enc)
            j = json.loads(raw)
            return str(j.get("id", "")).strip()
    except Exception:
        return ""
    return ""


def _epublica_apply_tamanho_pagina(page: Any, n: int, log: Callable = _log_default) -> bool:
    """Tenta definir «itens por página» no select da listagem (Angular)."""
    n = max(1, min(500, int(n)))

    def _wait_after_change() -> None:
        page.wait_for_timeout(900)
        try:
            page.wait_for_load_state("networkidle", timeout=35_000)
        except Exception:
            pass
        page.wait_for_timeout(1600)

    try:
        ok = page.evaluate(
            """wantN => {
                const wantStr = String(wantN);
                const selects = Array.from(document.querySelectorAll('select'));
                function findOption(sel) {
                    const opts = Array.from(sel.options || []);
                    for (const o of opts) {
                        const v = (o.value || '').trim();
                        const t = (o.textContent || '').trim();
                        if (v === wantStr || t === wantStr) return o;
                        const vi = parseInt(v, 10);
                        if (Number.isFinite(vi) && vi === wantN && String(vi) === v) return o;
                        const m = t.match(/^\\s*(\\d+)/);
                        if (m && parseInt(m[1], 10) === wantN) {
                            const rest = t.slice(m[0].length).trim();
                            if (rest === '' || /^[^\\d]/.test(rest)) return o;
                        }
                    }
                    return null;
                }
                const hits = [];
                for (const sel of selects) {
                    const hit = findOption(sel);
                    if (hit) hits.push({ sel, hit });
                }
                if (!hits.length) return false;
                hits.sort((a, b) => {
                    const near = s => {
                        let el = s;
                        for (let i = 0; i < 8 && el; i++) {
                            if (el.querySelector && el.querySelector('.pagination, ul.pagination, [class*="pagination"]'))
                                return 0;
                            el = el.parentElement;
                        }
                        return 1;
                    };
                    return near(a.sel) - near(b.sel);
                });
                const { sel, hit } = hits[0];
                sel.value = hit.value;
                sel.dispatchEvent(new Event('input', { bubbles: true }));
                sel.dispatchEvent(new Event('change', { bubbles: true }));
                try {
                    const w = window;
                    if (w.angular && sel) w.angular.element(sel).triggerHandler('change');
                } catch (e) {}
                return true;
            }""",
            n,
        )
        if ok:
            log(f"e-publica: itens por página ajustados para {n}.")
            _wait_after_change()
            return True
    except Exception as exc:
        log(f"e-publica tamanho página (JS): {exc}")

    log("e-publica: não foi possível ajustar itens por página no browser; aplicando limite na extração.")
    return False


def _epublica_limit_rows(
    rows: list[dict],
    tamanho_pagina: int,
    log: Callable = _log_default,
    label: str = "listagem",
) -> list[dict]:
    """Garante no máximo `tamanho_pagina` linhas de dados (o DOM pode trazer mais)."""
    lim = max(1, min(500, int(tamanho_pagina)))
    if len(rows) <= lim:
        return rows
    log(f"e-publica: {label} tinha {len(rows)} linhas no DOM; a usar só as primeiras {lim} (Linhas por página).")
    return rows[:lim]


def _extract_detail_links_from_page(page: Any, log: Callable = _log_default) -> list[str]:
    """Extrai URLs de detalhe (View/detalhe) de cada linha da tabela visível.
    Retorna só os links das linhas de dados (ignora linhas de descrição/Objeto).
    Suporta <a href>, ng-href, ui-sref e ícones com URL só no HTML (Angular)."""
    try:
        links = page.evaluate("""() => {
            function toAbsolute(href) {
                if (!href || typeof href !== 'string') return '';
                const t = href.trim();
                if (t.startsWith('http://') || t.startsWith('https://')) return t;
                if (t.startsWith('#')) {
                    return window.location.origin + window.location.pathname + t;
                }
                try {
                    return new URL(t, window.location.href).href;
                } catch (e) {
                    return t;
                }
            }

            function pickFromUiSref(a) {
                const sref = a.getAttribute('ui-sref') || a.getAttribute('data-ui-sref') || '';
                if (!sref || !sref.includes('View')) return '';
                const m = sref.match(/params\\s*:\\s*['"]([^'"]+)['"]/i)
                    || sref.match(/params\\s*:\\s*([^,)\\]]+)/i);
                if (!m) return '';
                let raw = (m[1] || '').trim().replace(/^['"]|['"]$/g, '');
                if (!raw) return '';
                if (!raw.startsWith('{')) {
                    try {
                        if (/^[A-Za-z0-9+/=_-]+$/.test(raw) && raw.length > 4) {
                            let b = raw.replace(/-/g, '+').replace(/_/g, '/');
                            while (b.length % 4) b += '=';
                            raw = atob(b);
                        }
                    } catch (e) {}
                }
                const enc = encodeURIComponent(raw);
                let viewHash = (window.location.hash || '').split('?')[0]
                    .replace(/contratoTable/i, 'contratoView');
                if (!viewHash.includes('contratoView')) {
                    viewHash = '#/palmeira/portal/compras/contratoView';
                }
                return window.location.origin + window.location.pathname + viewHash + '?params=' + enc;
            }

            function pickFromAnchors(row) {
                for (const a of row.querySelectorAll('a[ui-sref*="contratoView"], a[ui-sref*="View"], a.btn.epublica-button-null')) {
                    const h1 = (a.href || '').trim();
                    if (h1 && h1.includes('contratoView')) return toAbsolute(a.getAttribute('href') || h1);
                    const built = pickFromUiSref(a);
                    if (built) return built;
                }
                const selectors = 'a[href], a[ng-href], a[data-ng-href]';
                for (const a of row.querySelectorAll(selectors)) {
                    const raw = a.getAttribute('href') || a.getAttribute('ng-href')
                        || a.getAttribute('data-ng-href') || a.href || '';
                    const h = (raw || '').trim();
                    if (!h || h === '#' || h === 'javascript:void(0)') continue;
                    if (h.includes('contratoView') || h.includes('/compras/contratoView')) {
                        return toAbsolute(h);
                    }
                }
                for (const a of row.querySelectorAll('a[href], a')) {
                    const h = (a.href || a.getAttribute('href') || '').trim();
                    if (h.includes('params=') && (h.includes('View') || h.includes('view'))) {
                        return toAbsolute(a.getAttribute('href') || h);
                    }
                }
                for (const a of row.querySelectorAll('a[ui-sref]')) {
                    const built = pickFromUiSref(a);
                    if (built) return built;
                }
                return '';
            }

            function pickFromHtml(row) {
                const html = row.innerHTML || '';
                const patterns = [
                    /href=["']([^"']*contratoView[^"']*)["']/i,
                    /ng-href=["']([^"']*contratoView[^"']*)["']/i,
                    /#\\/[\\w\\/-]+\\/compras\\/contratoView\\?[^"'\\s<]+/i,
                    /contratoView\\?params=[^"'\\s<]+/i,
                ];
                for (const re of patterns) {
                    const m = html.match(re);
                    if (!m) continue;
                    const u = m[1] || m[0];
                    if (u && (u.includes('contratoView') || u.includes('params='))) {
                        return toAbsolute(u);
                    }
                }
                return '';
            }

            function pickFromClickable(row) {
                for (const el of row.querySelectorAll('[ng-click], [ui-sref], button, .btn, [role="button"]')) {
                    const oc = el.getAttribute('ng-click') || el.getAttribute('ui-sref') || '';
                    const m = oc.match(/["']([^"']*contratoView[^"']*)["']/i)
                        || oc.match(/params[:\\s]*['"]([^'"]+)['"]/i);
                    if (m && m[1]) {
                        const p = m[1];
                        if (p.startsWith('#') || p.includes('contratoView')) return toAbsolute(p);
                    }
                }
                return '';
            }

            const results = [];
            const tbody = document.querySelector('#compublicaportalcontratoPortalContratoTableService tbody')
                || document.querySelector('table.epublica-table tbody')
                || document.querySelector('table tbody');
            if (!tbody) return results;

            const rows = tbody.querySelectorAll(':scope > tr');
            for (const row of rows) {
                const st = window.getComputedStyle(row);
                if (st.display === 'none' || row.classList.contains('ng-hide')) continue;

                let nVisible = 0;
                for (const cell of row.querySelectorAll(':scope > td')) {
                    const cs = window.getComputedStyle(cell);
                    if (cs.display === 'none' || cell.classList.contains('ng-hide')) continue;
                    nVisible++;
                }
                if (nVisible < 3) continue;

                let detailHref = pickFromAnchors(row) || pickFromHtml(row) || pickFromClickable(row);
                if (!detailHref) {
                    for (const a of row.querySelectorAll('a[href]')) {
                        const h = (a.getAttribute('href') || '').trim();
                        if (h && (h.includes('View') || h.includes('view')
                            || h.includes('detalhe') || h.includes('detail')
                            || h.includes('params='))) {
                            detailHref = toAbsolute(h);
                            break;
                        }
                    }
                }
                results.push(detailHref || '');
            }
            return results;
        }""")
        found = sum(1 for l in links if l)
        log(f"Links de detalhe extraídos: {found}/{len(links)} linhas")
        return links
    except Exception as exc:
        log(f"Não foi possível extrair links de detalhe: {exc}")
        return []


def _inject_detail_urls(headers: list[str], rows: list[dict], detail_links: list[str]) -> tuple[list[str], list[dict]]:
    """Adiciona _detail_url e _detail_id (id do JSON em params) a cada linha, alinhando por índice."""
    if not detail_links:
        return headers, rows
    new_headers = list(headers)
    if "_detail_url" not in new_headers:
        new_headers.append("_detail_url")
    if "_detail_id" not in new_headers:
        new_headers.append("_detail_id")
    for i, row in enumerate(rows):
        u = detail_links[i] if i < len(detail_links) else ""
        row["_detail_url"] = u
        row["_detail_id"] = _epublica_contract_id_from_url(u) if u else ""
    return new_headers, rows


# ---------------------------------------------------------------------------
# Funções principais de extração
# ---------------------------------------------------------------------------

def buscar_tabelas_url(
    url: str,
    wait_selector: str = "table",
    table_index: int = 0,
    prefer_table_id: str = "",
    skip_first_column: bool = False,
    headed: bool = False,
    wait_network_idle: bool = False,
    log: Callable = _log_default,
    iframe_selector: str | None = None,
    epublica_tamanho_pagina: int | None = None,
) -> tuple[list[dict], list[str], list[dict], int]:
    from playwright.sync_api import sync_playwright

    detail_links: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headed,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=CHROME_USER_AGENT,
            locale="pt-BR",
            viewport={"width": 1365, "height": 900},
            extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5"},
        )
        context.set_default_timeout(NAV_TIMEOUT_MS)
        context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        page = context.new_page()
        _apply_timeouts_page(page)

        try:
            html = _goto_pagina_e_html(
                page, url, wait_selector,
                iframe_selector=iframe_selector, log=log,
            )
            if _is_epublica_url(url):
                if epublica_tamanho_pagina is not None:
                    _epublica_apply_tamanho_pagina(page, epublica_tamanho_pagina, log)
                todas = _extract_tables_via_js(page, skip_first_column, log)
            else:
                todas = None
            detail_links = _extract_detail_links_from_page(page, log)
        finally:
            browser.close()

    if todas is None:
        todas = extrair_todas_tabelas_top_level(html, skip_first_column)
    if not todas:
        raise ValueError("Nenhuma tabela encontrada na página.")

    ti = table_index
    if prefer_table_id:
        for t in todas:
            if t["element_id"] == prefer_table_id:
                ti = t["index"]
                break

    if ti >= len(todas):
        ti = 0

    if todas[ti]["row_count"] == 0:
        for t in todas:
            if t["row_count"] > 0:
                ti = t["index"]
                break

    chosen = todas[ti]
    headers, rows = _inject_detail_urls(chosen["headers"], chosen["rows"], detail_links)
    if _is_epublica_url(url) and epublica_tamanho_pagina is not None:
        rows = _epublica_limit_rows(rows, epublica_tamanho_pagina, log, label="listagem (1 página)")
    return todas, headers, rows, ti


def _epublica_first_data_row_fingerprint(page: Any) -> str:
    return page.evaluate("""() => {
        const tbody = document.querySelector('#compublicaportalcontratoPortalContratoTableService tbody')
            || document.querySelector('table.epublica-table tbody');
        if (!tbody) return '';
        for (const tr of tbody.querySelectorAll(':scope > tr')) {
            if (window.getComputedStyle(tr).display === 'none' || tr.classList.contains('ng-hide')) continue;
            let n = 0;
            for (const td of tr.querySelectorAll(':scope > td')) {
                const cs = window.getComputedStyle(td);
                if (cs.display === 'none' || td.classList.contains('ng-hide')) continue;
                n++;
            }
            if (n >= 3) return tr.innerText.replace(/\\s+/g, ' ').trim().slice(0, 160);
        }
        return '';
    }""")


def _epublica_click_next_page(page: Any, log: Callable = _log_default) -> bool:
    """Clica «próxima» na paginação (uib-pagination). Retorna False se não houver ou for a última página."""
    before = _epublica_first_data_row_fingerprint(page)
    selectors = (
        "ul.pagination li.pagination-next:not(.disabled) a",
        "li.pagination-next:not(.disabled) a",
        ".pagination li.next:not(.disabled) a",
        "a.pagination-next:not(.disabled)",
    )
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if loc.count() == 0:
                continue
            loc.click(timeout=12_000)
            page.wait_for_timeout(1500)
            try:
                page.wait_for_load_state("networkidle", timeout=35_000)
            except Exception:
                pass
            page.wait_for_timeout(2000)
            after = _epublica_first_data_row_fingerprint(page)
            if after == before or not after:
                log("Paginação e-publica: conteúdo não mudou (última página ou bloqueio).")
                return False
            log("Paginação e-publica: avançou para a página seguinte.")
            return True
        except Exception as exc:
            log(f"Paginação e-publica ({sel}): {exc}")
            continue
    log("Paginação e-publica: controlo 'próxima' não disponível.")
    return False


def buscar_tabelas_epublica_paginada(
    url: str,
    wait_selector: str = "table",
    table_index: int = 0,
    prefer_table_id: str = "",
    skip_first_column: bool = False,
    headed: bool = False,
    iframe_selector: str | None = None,
    max_paginas: int = 50,
    tamanho_pagina: int = 5,
    log: Callable = _log_default,
) -> tuple[list[dict], list[str], list[dict], int]:
    """Lista contratos e-publica com várias páginas (clique em «próxima» no browser)."""
    from playwright.sync_api import sync_playwright

    max_paginas = max(1, min(500, int(max_paginas)))
    all_rows: list[dict] = []
    final_headers: list[str] = []
    final_todas: list[dict] = []
    final_ti = table_index
    row_counter = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headed,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=CHROME_USER_AGENT,
            locale="pt-BR",
            viewport={"width": 1365, "height": 900},
            extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5"},
        )
        context.set_default_timeout(NAV_TIMEOUT_MS)
        context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        page = context.new_page()
        _apply_timeouts_page(page)

        try:
            _goto_pagina_e_html(
                page, url, wait_selector,
                iframe_selector=iframe_selector, log=log,
            )
            _epublica_apply_tamanho_pagina(page, tamanho_pagina, log)
            for pag_num in range(1, max_paginas + 1):
                log(f"e-publica página {pag_num}/{max_paginas}")
                todas = _extract_tables_via_js(page, skip_first_column, log)
                detail_links = _extract_detail_links_from_page(page, log)

                ti = table_index
                if prefer_table_id:
                    for t in todas:
                        if t["element_id"] == prefer_table_id:
                            ti = t["index"]
                            break
                if ti >= len(todas):
                    ti = 0
                if todas[ti]["row_count"] == 0:
                    for t in todas:
                        if t["row_count"] > 0:
                            ti = t["index"]
                            break

                chosen = todas[ti]
                rows_copy = [dict(r) for r in chosen["rows"]]
                headers, rows = _inject_detail_urls(list(chosen["headers"]), rows_copy, detail_links)
                rows = _epublica_limit_rows(rows, tamanho_pagina, log, label=f"página {pag_num}")

                for r in rows:
                    row_counter += 1
                    r["_pagina"] = str(pag_num)
                    r["_linha"] = str(row_counter)
                    all_rows.append(r)

                if pag_num == 1:
                    final_headers = headers
                    final_todas = []
                    for t in todas:
                        entry = {
                            "index": t["index"],
                            "element_id": t.get("element_id", ""),
                            "class": t.get("class", ""),
                            "headers": t["headers"],
                            "row_count": t["row_count"],
                            "rows": t.get("rows", []),
                        }
                        final_todas.append(entry)
                    final_ti = ti

                if pag_num >= max_paginas:
                    break
                if not _epublica_click_next_page(page, log):
                    break

            if final_todas and final_ti < len(final_todas):
                final_todas[final_ti]["rows"] = all_rows
                final_todas[final_ti]["row_count"] = len(all_rows)
        finally:
            browser.close()

    if not all_rows:
        raise ValueError("Nenhuma linha encontrada na listagem e-publica (paginada).")

    return final_todas, final_headers, all_rows, final_ti


def buscar_tabelas_url_paginada(
    url_base: str,
    wait_selector: str = "table",
    table_index: int = 0,
    prefer_table_id: str = "",
    skip_first_column: bool = False,
    headed: bool = False,
    log: Callable = _log_default,
    tamanho_pagina: int = 5,
    max_paginas: int = 1,
    iframe_selector: str | None = None,
) -> tuple[list[dict], list[str], list[dict], int]:
    from playwright.sync_api import sync_playwright

    is_portal = _is_portal_url(url_base)
    all_rows: list[dict] = []
    final_headers: list[str] = []
    final_todas: list[dict] = []
    final_ti = table_index
    paginas_buscadas = 0
    row_counter = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headed,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=CHROME_USER_AGENT,
            locale="pt-BR",
            viewport={"width": 1365, "height": 900},
            extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5"},
        )
        context.set_default_timeout(NAV_TIMEOUT_MS)
        context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        page = context.new_page()
        _apply_timeouts_page(page)

        try:
            for pag_num in range(1, max_paginas + 1):
                if is_portal:
                    pag_url = merge_query_params(url_base, {
                        "pagina": str(pag_num),
                        "offset": None,
                    })
                else:
                    offset = (pag_num - 1) * tamanho_pagina
                    pag_url = merge_query_params(url_base, {"offset": str(offset)})

                log(f"Página {pag_num}/{max_paginas}: {pag_url[:120]}")
                html = _goto_pagina_e_html(
                    page, pag_url, wait_selector,
                    iframe_selector=iframe_selector, log=log,
                )
                todas = extrair_todas_tabelas_top_level(html, skip_first_column)
                if not todas:
                    log(f"Nenhuma tabela na página {pag_num}, encerrando")
                    break

                ti = table_index
                if prefer_table_id:
                    for t in todas:
                        if t["element_id"] == prefer_table_id:
                            ti = t["index"]
                            break
                if ti >= len(todas):
                    ti = 0

                chosen = todas[ti]
                rows = chosen["rows"]

                if not rows:
                    log(f"Página {pag_num} vazia, encerrando paginação")
                    break

                if pag_num == 1:
                    final_headers = chosen["headers"]
                    final_todas = todas
                    final_ti = ti

                for r in rows:
                    row_counter += 1
                    r["_pagina"] = str(pag_num)
                    r["_linha"] = str(row_counter)
                    all_rows.append(r)

                paginas_buscadas = pag_num
        finally:
            browser.close()

    if not final_headers and not all_rows:
        raise ValueError("Nenhuma tabela encontrada em nenhuma página.")

    return final_todas, final_headers, all_rows, final_ti


def _extract_tables_summary(html: str) -> list[dict]:
    todas = extrair_todas_tabelas_top_level(html, skip_first_column=False)
    summary = []
    for t in todas:
        summary.append({
            "index": t["index"],
            "element_id": t["element_id"],
            "class": t["class"],
            "headers": t["headers"],
            "row_count": t["row_count"],
        })
    return summary


def _js_tables_to_summary(js_tables: list[dict]) -> list[dict]:
    return [{
        "index": t["index"],
        "element_id": t.get("element_id", ""),
        "class": t.get("class", ""),
        "headers": t["headers"],
        "row_count": t["row_count"],
    } for t in js_tables]


# ---------------------------------------------------------------------------
# Extração de detalhes de páginas individuais
# ---------------------------------------------------------------------------

def _extrair_detalhe_linhas_texto(page: Any) -> dict[str, str]:
    """Extrai pares 'Rótulo: valor' a partir do texto visível.
    Suporta dois formatos comuns no e-publica:
      1) Rótulo: valor  (mesma linha)
      2) Rótulo:        (rótulo numa linha, valor na seguinte)
    """
    try:
        return page.evaluate("""() => {
            const out = {};
            const root = document.querySelector('main') || document.querySelector('.container')
                || document.querySelector('[class*="epublica"]') || document.body;
            if (!root) return out;
            const text = root.innerText || '';
            const lines = text.split(/\\r?\\n/);
            for (let i = 0; i < lines.length; i++) {
                const t = lines[i].trim();
                if (t.length < 3 || t.length > 5000) continue;

                // Formato 1: "Rótulo: valor" na mesma linha
                const m = t.match(/^([^:\\n]{1,80}):\\s*(.+)$/);
                if (m) {
                    const k = m[1].trim();
                    const v = m[2].trim();
                    if (k && v && k.length <= 80 && !k.startsWith('http')) {
                        if (!out[k] || out[k].length < v.length) out[k] = v;
                    }
                    continue;
                }

                // Formato 2: "Rótulo:" (só rótulo) e valor na linha seguinte
                const m2 = t.match(/^([^:\\n]{1,80}):\\s*$/);
                if (m2 && i + 1 < lines.length) {
                    const k = m2[1].trim();
                    const nextLine = lines[i + 1].trim();
                    if (k && nextLine && nextLine.length < 2000
                        && !k.startsWith('http') && k.length <= 80) {
                        if (!out[k] || out[k].length < nextLine.length) {
                            out[k] = nextLine;
                        }
                        i++;
                    }
                }
            }
            return out;
        }""")
    except Exception:
        return {}


def _extrair_detalhe_pagina(page: Any, log: Callable = _log_default) -> dict[str, str]:
    """Extrai campos estruturados de uma página de detalhe (SPA cards/labels)."""
    data: dict[str, str] = {}

    try:
        kv_pairs = page.evaluate("""() => {
            const result = {};

            // 1. <strong>Label:</strong> value (or <b>Label:</b> value)
            const strongs = document.querySelectorAll('strong, b');
            for (const s of strongs) {
                const label = (s.textContent || '').trim().replace(/:$/, '').trim();
                if (!label || label.length > 60) continue;
                const parent = s.parentElement;
                if (!parent) continue;
                const fullText = parent.textContent || '';
                const afterLabel = fullText.substring(fullText.indexOf(s.textContent) + s.textContent.length).trim();
                const value = afterLabel.replace(/^[:\\s]+/, '').trim();
                if (value && value.length < 500) {
                    result[label] = value;
                }
            }

            // 2. <dt>/<dd> pairs
            const dts = document.querySelectorAll('dt');
            for (const dt of dts) {
                const dd = dt.nextElementSibling;
                if (dd && dd.tagName === 'DD') {
                    const label = (dt.textContent || '').trim().replace(/:$/, '').trim();
                    const value = (dd.textContent || '').trim();
                    if (label && value && label.length < 60) {
                        result[label] = value;
                    }
                }
            }

            // 3. labeled spans: <label>X</label><span>Y</span> or similar
            const labels = document.querySelectorAll('label');
            for (const lbl of labels) {
                const next = lbl.nextElementSibling;
                if (next) {
                    const label = (lbl.textContent || '').trim().replace(/:$/, '').trim();
                    const value = (next.textContent || '').trim();
                    if (label && value && label.length < 60 && value.length < 500) {
                        result[label] = value;
                    }
                }
            }

            // 4. Main heading / title (h2, h3 with contract info)
            const headings = document.querySelectorAll('h2, h3, h4');
            for (const h of headings) {
                const text = (h.textContent || '').trim();
                if (text.match(/contrato|licitaç|empenho/i) && text.length < 200) {
                    result['_titulo_detalhe'] = text;
                    break;
                }
            }

            // 5. Objeto (typically a longer description)
            const bodyText = document.body ? document.body.innerText : '';
            const objetoMatch = bodyText.match(/Objeto:\\s*\\n?(.+)/i);
            if (objetoMatch && objetoMatch[1]) {
                const obj = objetoMatch[1].trim().substring(0, 500);
                if (obj && !result['Objeto']) result['Objeto'] = obj;
            }

            return result;
        }""")

        for k, v in kv_pairs.items():
            safe_key = f"det_{k.strip()}"
            data[safe_key] = str(v).strip()

    except Exception as exc:
        log(f"Erro ao extrair campos do detalhe: {exc}")

    # Also extract any tables present (e.g. "Responsáveis Jurídicos") via JS
    _DETAIL_TABLE_IGNORE = {"?", "Show / hide this help menu"}
    try:
        detail_tables = _extract_tables_via_js(page, skip_first_column=False, log=log)
        for tbl in detail_tables:
            if not tbl["rows"]:
                continue
            raw_id = tbl.get("element_id") or ""
            # Shorten e-publica table IDs: extract meaningful part
            table_label = raw_id
            if "responsaveljuridico" in raw_id.lower():
                table_label = "RespJuridico"
            elif "gestor" in raw_id.lower():
                table_label = "Gestor"
            elif "fiscal" in raw_id.lower():
                table_label = "Fiscal"
            elif "item" in raw_id.lower() and "contrato" in raw_id.lower():
                table_label = "Item"
            elif "texto" in raw_id.lower() and "contrato" in raw_id.lower():
                table_label = "Texto"
            elif not raw_id:
                table_label = f"tabela{tbl['index']}"
            for ri, row in enumerate(tbl["rows"], 1):
                skip_row = False
                for val in row.values():
                    if val in _DETAIL_TABLE_IGNORE:
                        skip_row = True
                        break
                if skip_row:
                    continue
                for col, val in row.items():
                    if col == "Objeto" or not val:
                        continue
                    key = f"det_{table_label}_R{ri}_{col}"
                    data[key] = val
    except Exception as exc:
        log(f"Erro ao extrair tabelas do detalhe: {exc}")

    _GARBAGE_PREFIXES = (
        "Avenida ", "Rua ", "Desenvolvido por", "Versão",
        "Portal da Transparência", "Endereço",
        "Informações atualizadas em",
    )
    try:
        line_kv = _extrair_detalhe_linhas_texto(page)
        for k, v in line_kv.items():
            k_stripped = k.strip()
            if any(k_stripped.startswith(p) for p in _GARBAGE_PREFIXES):
                continue
            safe_key = f"det_{k_stripped}"
            val = str(v).strip()
            if not val:
                continue
            if safe_key not in data or not data[safe_key]:
                data[safe_key] = val
            elif len(val) > len(data.get(safe_key, "")):
                data[safe_key] = val
    except Exception as exc:
        log(f"Aviso extração linhas detalhe: {exc}")

    return data


def buscar_detalhes_urls(
    urls: list[str],
    wait_selector: str = "body",
    log: Callable = _log_default,
) -> list[dict[str, str]]:
    """Visita cada URL de detalhe e extrai campos estruturados."""
    from playwright.sync_api import sync_playwright

    results: list[dict[str, str]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=CHROME_USER_AGENT,
            locale="pt-BR",
            viewport={"width": 1365, "height": 900},
            extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5"},
        )
        context.set_default_timeout(NAV_TIMEOUT_MS)
        context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        page = context.new_page()
        _apply_timeouts_page(page)

        try:
            for i, url in enumerate(urls):
                log(f"Detalhe {i + 1}/{len(urls)}: {url[:100]}")
                try:
                    page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
                    page.wait_for_timeout(2500)
                    try:
                        page.wait_for_load_state("networkidle", timeout=20_000)
                    except Exception:
                        pass
                    if _is_epublica_url(url):
                        page.wait_for_timeout(2000)
                        try:
                            page.get_by_text("Dados do contrato", exact=False).first.wait_for(
                                timeout=25_000, state="visible"
                            )
                        except Exception:
                            try:
                                page.wait_for_selector(
                                    "table, .panel-body, [class*='epublica']",
                                    timeout=12_000,
                                )
                            except Exception:
                                pass
                        try:
                            page.evaluate("""() => {
                                const els = Array.from(document.querySelectorAll('a, button, [role="tab"], li'));
                                const t = els.find(el =>
                                    /dados\\s+do\\s+contrato/i.test((el.textContent || '').trim()));
                                if (t) { try { t.click(); } catch (e) {} }
                            }""")
                            page.wait_for_timeout(1200)
                        except Exception:
                            pass
                        page.wait_for_timeout(800)
                    elif wait_selector and wait_selector.strip() and wait_selector != "body":
                        try:
                            page.wait_for_selector(wait_selector.strip(), timeout=15_000)
                        except Exception:
                            pass
                    data = _extrair_detalhe_pagina(page, log)
                    data["_detail_url"] = url
                    did = _epublica_contract_id_from_url(url)
                    if did:
                        data["_detail_id"] = did
                    results.append(data)
                except Exception as exc:
                    log(f"Erro no detalhe {i + 1}: {exc}")
                    results.append({"_detail_url": url, "_erro": str(exc)})
        finally:
            browser.close()

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Extrator de tabelas HTML")
    parser.add_argument("url", help="URL da página com tabela(s)")
    parser.add_argument("--wait-selector", default="table", help="Seletor CSS a esperar")
    parser.add_argument("--table-index", type=int, default=0, help="Índice da tabela (0=primeira)")
    parser.add_argument("--prefer-table-id", default="", help="ID preferido da tabela")
    parser.add_argument("--skip-first-column", action="store_true", help="Ignorar 1.ª coluna")
    parser.add_argument("--headed", action="store_true", help="Modo headed (com janela)")
    parser.add_argument("--iframe-selector", default=None, help="Seletor CSS do iframe (ou __auto__)")
    parser.add_argument("--paginas", type=int, default=1, help="Máx. páginas (paginação)")
    parser.add_argument("--tamanho-pagina", type=int, default=5, help="Linhas por página")
    parser.add_argument("--output", default=None, help="Ficheiro JSON de saída")

    args = parser.parse_args()

    if args.paginas > 1:
        todas, headers, rows, ti = buscar_tabelas_url_paginada(
            args.url,
            wait_selector=args.wait_selector,
            table_index=args.table_index,
            prefer_table_id=args.prefer_table_id,
            skip_first_column=args.skip_first_column,
            headed=args.headed,
            tamanho_pagina=args.tamanho_pagina,
            max_paginas=args.paginas,
            iframe_selector=args.iframe_selector,
        )
    else:
        todas, headers, rows, ti = buscar_tabelas_url(
            args.url,
            wait_selector=args.wait_selector,
            table_index=args.table_index,
            prefer_table_id=args.prefer_table_id,
            skip_first_column=args.skip_first_column,
            headed=args.headed,
            iframe_selector=args.iframe_selector,
            epublica_tamanho_pagina=args.tamanho_pagina if _is_epublica_url(args.url) else None,
        )

    result = {
        "table_count": len(todas),
        "selected_table_index": ti,
        "headers": headers,
        "row_count": len(rows),
        "rows": rows,
    }

    if args.output:
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Resultado gravado em {args.output}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
