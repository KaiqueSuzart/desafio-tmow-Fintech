/* app.js — Frontend do Extrator de Tabelas HTML */
(function () {
  "use strict";

  const DEFAULT_URL =
    "https://transparencia.e-publica.net/epublica-portal/#/palmeira/portal/compras/contratoTable";
  const PORTAL_HOST = "portaldatransparencia.gov.br";
  /** URLs de detalhe por pedido a /api/scrape-details (evita timeouts no browser). */
  const DETAIL_BATCH = 15;

  /** Chave estável para juntar listagem ↔ detalhe (param `params` JSON no hash). */
  function epublicaParamsKey(url) {
    if (!url) return "";
    const m = String(url).match(/[?&]params=([^&]+)/);
    if (!m) return String(url).trim();
    try {
      return decodeURIComponent(m[1]);
    } catch {
      return m[1];
    }
  }

  function rowMatchesDetail(row, detail) {
    const du = detail._detail_url || "";
    if (row._detail_url && row._detail_url === du) return true;
    const rid = row._detail_id;
    const did = detail._detail_id;
    if (rid && did && String(rid) === String(did)) return true;
    if (row._detail_url && du && epublicaParamsKey(row._detail_url) === epublicaParamsKey(du)) return true;
    return false;
  }

  // ===== DOM refs =====
  const el = {};
  const IDS = [
    "url", "wait-selector", "wait-selector-custom", "wait-detected-hint",
    "table-index", "skip-first-col", "iframe-auto", "iframe-selector",
    "paginacao-portal", "periodo-de", "periodo-ate", "tamanho-pagina",
    "max-paginas", "buscar-todas", "hint-epublica-pages", "btn-scrape", "status-msg",
    "dynamic-params-container", "btn-add-param",
    "wrap-detail-auto-fetch", "detail-auto-fetch",
    "detail-modal", "detail-modal-backdrop", "detail-modal-close", "detail-modal-body",
    "result-area", "table-picker-wrap", "table-picker", "table-checkboxes",
    "columns-list", "save-title", "save-only-filtered", "btn-save", "btn-fetch-details",
    "global-search", "col-filters", "details-hint",
    "preview-thead", "preview-tbody",
    "consultas-list", "saved-detail", "saved-titulo", "saved-info",
    "saved-thead", "saved-tbody", "saved-edit-actions",
    "btn-saved-titulo", "btn-saved-excel", "btn-saved-edit",
    "btn-saved-delete", "btn-saved-add-row", "btn-saved-save-edit",
    "btn-new-consulta",
    "dialog-new-consulta", "new-consulta-titulo", "new-consulta-colunas",
    "btn-new-consulta-ok", "btn-new-consulta-cancel",
  ];

  // ===== State =====
  const state = {
    originalOrder: [],
    order: [],
    visible: {},
    rows: [],
    allTables: [],
    selectedTableIndex: 0,
    colFilters: {},
    rowSaveSelected: new Set(),
    dynamicParams: [],
  };
  let scrapeMeta = {};
  let savedConsultaId = null;
  let savedConsultaData = null;
  let savedEditing = false;

  // ===== Utilities =====
  function isEpublicaUrl(url) {
    return (url || "").toLowerCase().includes("e-publica.net");
  }

  function isPortalUrl(url) {
    return url && url.toLowerCase().includes(PORTAL_HOST);
  }

  function detectWaitSelectorFromUrl(url) {
    if (!url) return { selector: "table", label: "table (genérico)" };
    const low = url.toLowerCase();
    if (low.includes(PORTAL_HOST)) return { selector: "#lista", label: "#lista (Portal)" };
    if (low.includes("w3schools") && low.includes("tryit"))
      return { selector: "table", label: "table (Tryit — iframe auto recomendado)" };
    if (low.includes(".gov.br")) return { selector: "#conteudo", label: "#conteudo (.gov.br)" };
    if (low.includes("e-publica.net"))
      return { selector: "table", label: "table (e-publica SPA)" };
    if (low.includes("receita.fazenda") || low.includes("rfb.gov"))
      return { selector: "main", label: "main (Receita)" };
    return { selector: "table", label: "table (genérico)" };
  }

  function getWaitSelectorValue() {
    const v = el["wait-selector"].value;
    if (v === "__auto__") return detectWaitSelectorFromUrl(el["url"].value).selector;
    if (v === "__custom__") return el["wait-selector-custom"].value.trim() || "table";
    return v;
  }

  function syncWaitCustomVisibility() {
    const isCustom = el["wait-selector"].value === "__custom__";
    el["wait-selector-custom"].classList.toggle("hidden", !isCustom);
  }

  function syncIframeAutoUi() {
    el["iframe-selector"].disabled = el["iframe-auto"].checked;
    if (el["iframe-auto"].checked) el["iframe-selector"].value = "";
  }

  function updateWaitDetectedHint() {
    const detected = detectWaitSelectorFromUrl(el["url"].value);
    const cur = el["wait-selector"].value;
    if (cur === "__auto__") {
      el["wait-detected-hint"].textContent = "Deteção automática: " + detected.label;
    } else {
      el["wait-detected-hint"].textContent = "";
    }
  }

  function syncUrlDerivedOptions() {
    const url = el["url"].value.trim();
    if (!url) return;
    if (isPortalUrl(url)) {
      el["paginacao-portal"].checked = true;
      el["skip-first-col"].checked = true;
      const ws = el["wait-selector"];
      if (ws.value === "__auto__" || ws.value === "table") ws.value = "#lista";
    } else {
      el["paginacao-portal"].checked = false;
      if (el["wait-selector"].value === "#lista") el["wait-selector"].value = "__auto__";
    }
    const low = url.toLowerCase();
    if (low.includes("w3schools") && low.includes("tryit")) {
      el["iframe-auto"].checked = true;
      syncIframeAutoUi();
    }
    updateWaitDetectedHint();
    const hEp = el["hint-epublica-pages"];
    if (hEp) {
      hEp.style.display = isEpublicaUrl(url) ? "block" : "none";
    }
    if (isEpublicaUrl(url)) {
      const m = document.getElementById("detail-mode-merge");
      const modal = document.getElementById("detail-mode-modal");
      if (m && modal) {
        m.checked = true;
        modal.checked = false;
      }
    }
    syncDetailModeUi();
  }

  function getDetailMode() {
    const r = document.querySelector('input[name="detail-mode"]:checked');
    return r && r.value === "modal" ? "modal" : "merge";
  }

  function syncDetailModeUi() {
    const wrap = el["wrap-detail-auto-fetch"];
    if (!wrap) return;
    wrap.style.display = getDetailMode() === "modal" ? "none" : "";
  }

  function formatDateTimeBrPlain(iso) {
    if (!iso) return "";
    try {
      return new Intl.DateTimeFormat("pt-BR", {
        dateStyle: "short", timeStyle: "short", timeZone: "America/Sao_Paulo",
      }).format(new Date(iso));
    } catch { return iso; }
  }

  function setStatus(msg, isError) {
    el["status-msg"].textContent = msg;
    el["status-msg"].style.color = isError ? "var(--danger)" : "var(--muted)";
  }

  function apiErr(detail) {
    if (!detail) return "Erro desconhecido.";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map(d => d.msg || JSON.stringify(d)).join("; ");
    return JSON.stringify(detail);
  }

  // ===== Dynamic Params =====
  function renderDynamicParams() {
    const c = el["dynamic-params-container"];
    c.innerHTML = "";
    state.dynamicParams.forEach((p, i) => {
      const row = document.createElement("div");
      row.className = "dynamic-param-row";
      row.innerHTML =
        `<input type="text" value="${esc(p.key)}" data-idx="${i}" data-field="key" placeholder="Parâmetro">` +
        `<input type="text" value="${esc(p.value)}" data-idx="${i}" data-field="value" placeholder="Valor">` +
        `<button type="button" class="btn btn-danger btn-small" data-remove="${i}">×</button>`;
      c.appendChild(row);
    });
    c.querySelectorAll("input").forEach(inp => {
      inp.addEventListener("input", () => {
        const idx = +inp.dataset.idx;
        state.dynamicParams[idx][inp.dataset.field] = inp.value;
      });
    });
    c.querySelectorAll("[data-remove]").forEach(btn => {
      btn.addEventListener("click", () => {
        state.dynamicParams.splice(+btn.dataset.remove, 1);
        renderDynamicParams();
      });
    });
  }

  function collectExtraQuery() {
    const obj = {};
    state.dynamicParams.forEach(p => {
      const k = p.key.trim(), v = p.value.trim();
      if (k && v) obj[k] = v;
    });
    return obj;
  }

  function mergeDynamicParamKey(key, value) {
    const existing = state.dynamicParams.find(p => p.key === key);
    if (existing) { existing.value = value; }
    else { state.dynamicParams.push({ key, value }); }
  }

  function closeDetailModal() {
    const modal = el["detail-modal"];
    if (!modal) return;
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
  }

  async function openDetailModal(rowIndex) {
    const row = state.rows[rowIndex];
    if (!row || !row._detail_url) return;
    const modal = el["detail-modal"];
    const body = el["detail-modal-body"];
    if (!modal || !body) return;
    body.innerHTML = "<p>A carregar detalhe no servidor…</p>";
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    try {
      const details = await fetchDetailsFromApi([row._detail_url]);
      const d = details[0] || {};
      if (d._erro) {
        body.innerHTML = `<p class="detail-modal-error">${esc(String(d._erro))}</p>`;
        return;
      }
      const keys = Object.keys(d).filter(k => k !== "_detail_url" && k !== "_detail_id").sort();
      let html = "<dl class=\"detail-modal-dl\">";
      keys.forEach(k => {
        html += `<dt>${esc(k)}</dt><dd>${esc(String(d[k] ?? ""))}</dd>`;
      });
      html += "</dl>";
      if (!keys.length) body.innerHTML = "<p>Nenhum campo devolvido.</p>";
      else body.innerHTML = html;
    } catch (err) {
      const msg = err && err.message ? err.message : String(err);
      body.innerHTML = `<p class="detail-modal-error">${esc(msg)}</p>`;
    }
  }

  // ===== Tables & Columns =====
  function initFromResponse(data) {
    state.allTables = data.tables || [];
    state.selectedTableIndex = data.selected_table_index || 0;
    scrapeMeta = {
      url_final: data.url_final,
      periodo_de: data.periodo_de,
      periodo_ate: data.periodo_ate,
      tamanho_pagina: data.tamanho_pagina,
      paginas_buscadas: data.paginas_buscadas,
    };

    if (data.tamanho_pagina) el["tamanho-pagina"].value = data.tamanho_pagina;

    applyTableByIndex(state.selectedTableIndex, data.headers, data.rows);

    if (state.allTables.length > 1) {
      renderTablePicker();
      el["table-picker-wrap"].classList.add("visible");
    } else {
      el["table-picker-wrap"].classList.remove("visible");
    }

    // Ocultar colunas técnicas por defeito
    if (state.visible["_detail_url"] !== undefined) {
      state.visible["_detail_url"] = false;
    }
    if (state.visible["_detail_id"] !== undefined) {
      state.visible["_detail_id"] = false;
    }
    if (state.visible["_detail_url"] !== undefined || state.visible["_detail_id"] !== undefined) {
      renderColumns();
      renderTable();
    }
    syncFetchDetailsButtonVisibility();

    el["result-area"].classList.add("visible");
    updateDetailsHint();
  }

  function syncFetchDetailsButtonVisibility() {
    const btn = el["btn-fetch-details"];
    if (!btn) return;
    const hasDetails = state.rows.some(r => r._detail_url);
    btn.style.display = hasDetails && getDetailMode() === "merge" ? "" : "none";
  }

  function applyTableByIndex(idx, headers, rows) {
    if (!headers) {
      const t = state.allTables.find(t => t.index === idx);
      if (!t) return;
      headers = t.headers;
      rows = t.rows || [];
    }
    state.selectedTableIndex = idx;
    state.originalOrder = [...headers];
    state.order = [...headers];
    state.visible = {};
    headers.forEach(h => state.visible[h] = true);
    state.rows = rows;
    state.rowSaveSelected = new Set(rows.map((_, i) => i));
    state.colFilters = {};
    renderColumns();
    renderColumnFilterInputs();
    renderTable();
  }

  function renderTablePicker() {
    const sel = el["table-picker"];
    sel.innerHTML = "";
    const cbWrap = el["table-checkboxes"];
    cbWrap.innerHTML = "";
    state.allTables.forEach(t => {
      const opt = document.createElement("option");
      opt.value = t.index;
      opt.textContent = `#${t.index} ${t.element_id ? `(${t.element_id})` : ""} — ${t.row_count} linhas`;
      if (t.index === state.selectedTableIndex) opt.selected = true;
      sel.appendChild(opt);

      const lbl = document.createElement("label");
      lbl.innerHTML = `<input type="checkbox" data-tbl-idx="${t.index}" checked> Gravar tabela #${t.index}`;
      cbWrap.appendChild(lbl);
    });
    sel.addEventListener("change", () => {
      applyTableByIndex(+sel.value);
    });
  }

  function renderColumns() {
    const list = el["columns-list"];
    list.innerHTML = "";
    state.order.forEach((col, i) => {
      const chip = document.createElement("div");
      chip.className = "col-chip";
      chip.draggable = true;
      chip.dataset.col = col;
      chip.dataset.idx = i;
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!state.visible[col];
      cb.addEventListener("change", () => {
        state.visible[col] = cb.checked;
        renderTable();
      });
      chip.appendChild(cb);
      chip.appendChild(document.createTextNode(col));

      chip.addEventListener("dragstart", e => {
        e.dataTransfer.setData("text/plain", String(i));
        chip.classList.add("dragging");
      });
      chip.addEventListener("dragend", () => chip.classList.remove("dragging"));
      chip.addEventListener("dragover", e => { e.preventDefault(); chip.classList.add("drag-over"); });
      chip.addEventListener("dragleave", () => chip.classList.remove("drag-over"));
      chip.addEventListener("drop", e => {
        e.preventDefault();
        chip.classList.remove("drag-over");
        const from = +e.dataTransfer.getData("text/plain");
        const to = i;
        if (from === to) return;
        const moved = state.order.splice(from, 1)[0];
        state.order.splice(to, 0, moved);
        renderColumns();
        renderTable();
        renderColumnFilterInputs();
      });

      list.appendChild(chip);
    });
  }

  function renderColumnFilterInputs() {
    const wrap = el["col-filters"];
    wrap.innerHTML = "";
    visibleCols().forEach(col => {
      const inp = document.createElement("input");
      inp.type = "search";
      inp.placeholder = col;
      inp.value = state.colFilters[col] || "";
      inp.addEventListener("input", () => {
        state.colFilters[col] = inp.value;
        renderTable();
      });
      wrap.appendChild(inp);
    });
  }

  function visibleCols() {
    return state.order.filter(c => state.visible[c]);
  }

  function rowsHaveDetailData() {
    return state.rows.some(r => Object.keys(r).some(k => k.startsWith("det_")));
  }

  function rowHasDetColumns(row) {
    return row && Object.keys(row).some(k => k.startsWith("det_"));
  }

  function updateDetailsHint() {
    const hint = el["details-hint"];
    if (!hint) return;
    syncFetchDetailsButtonVisibility();
    const hasLink = state.rows.some(r => r._detail_url);
    if (!hasLink) {
      hint.classList.add("hidden");
      return;
    }
    hint.classList.remove("hidden");
    if (getDetailMode() === "modal") {
      hint.textContent =
        "Modo painel: a tabela mostra só a listagem. Clique numa linha (fora da caixa de gravar) para abrir o detalhe completo no painel. " +
        "Neste modo não são acrescentadas colunas det_* à grelha.";
      hint.classList.remove("details-hint--pending");
      return;
    }
    if (rowsHaveDetailData()) {
      hint.textContent =
        "Colunas det_* vêm da página de detalhe (CNPJ, licitação, responsáveis, etc.), obtidas pelo servidor — não precisa abrir o site. " +
        "Pode usar «Trazer detalhe» numa linha ou «Buscar detalhes» para todas as linhas marcadas.";
      hint.classList.remove("details-hint--pending");
    } else {
      hint.textContent =
        "Clique em «Trazer detalhe» na linha (ou «Buscar detalhes» em cima) para o servidor ir buscar cada contrato e preencher colunas det_* nesta tabela. " +
        "O link «site» só abre o e-publica se quiser ver a página original.";
      hint.classList.add("details-hint--pending");
    }
  }

  function rowPassesFilters(row) {
    const globalQ = (el["global-search"].value || "").toLowerCase();
    if (globalQ) {
      const vals = Object.values(row).join(" ").toLowerCase();
      if (!vals.includes(globalQ)) return false;
    }
    for (const col of visibleCols()) {
      const fq = (state.colFilters[col] || "").toLowerCase();
      if (fq && !(row[col] || "").toLowerCase().includes(fq)) return false;
    }
    return true;
  }

  function renderTable() {
    const cols = visibleCols();
    const thead = el["preview-thead"];
    const tbody = el["preview-tbody"];
    thead.innerHTML = "";
    tbody.innerHTML = "";

    const mergeMode = getDetailMode() === "merge";
    const showPortalCol = mergeMode && state.rows.some(r => r._detail_url);

    const trH = document.createElement("tr");
    // master checkbox
    const thCb = document.createElement("th");
    thCb.className = "col-save-check";
    const masterCb = document.createElement("input");
    masterCb.type = "checkbox";
    masterCb.checked = true;
    masterCb.addEventListener("change", () => {
      state.rows.forEach((_, i) => {
        if (rowPassesFilters(state.rows[i])) {
          if (masterCb.checked) state.rowSaveSelected.add(i);
          else state.rowSaveSelected.delete(i);
        }
      });
      renderTable();
    });
    thCb.appendChild(masterCb);
    trH.appendChild(thCb);

    if (showPortalCol) {
      const thP = document.createElement("th");
      thP.className = "col-detail-link";
      thP.textContent = "Detalhe (servidor)";
      trH.appendChild(thP);
    }

    cols.forEach(c => {
      const th = document.createElement("th");
      th.textContent = c;
      trH.appendChild(th);
    });
    thead.appendChild(trH);

    state.rows.forEach((row, i) => {
      if (!rowPassesFilters(row)) return;
      const tr = document.createElement("tr");
      const tdCb = document.createElement("td");
      tdCb.className = "col-save-check";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = state.rowSaveSelected.has(i);
      cb.addEventListener("change", () => {
        if (cb.checked) state.rowSaveSelected.add(i);
        else state.rowSaveSelected.delete(i);
      });
      tdCb.appendChild(cb);
      tr.appendChild(tdCb);

      if (showPortalCol) {
        const tdP = document.createElement("td");
        tdP.className = "col-detail-link";
        if (row._detail_url) {
          const wrap = document.createElement("div");
          wrap.className = "col-detail-actions";

          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "btn-row-detail";
          btn.textContent = rowHasDetColumns(row) ? "Atualizar" : "Trazer detalhe";
          btn.title =
            "O servidor abre a página de contrato e preenche colunas det_* nesta tabela (não redireciona o browser).";
          btn.addEventListener("click", e => {
            e.preventDefault();
            e.stopPropagation();
            if (btn.disabled) return;
            btn.disabled = true;
            btn.textContent = "A carregar…";
            void loadDetailForRowIndices([i]).catch(() => {});
          });
          wrap.appendChild(btn);

          const a = document.createElement("a");
          a.className = "col-open-portal-subtle";
          a.href = row._detail_url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = "site";
          a.title = "Opcional: abrir a página no e-publica";
          wrap.appendChild(a);

          tdP.appendChild(wrap);
          tr.classList.add("preview-row-with-detail");
          tr.title = "Duplo clique: trazer detalhe para a tabela (mesmo que o botão).";
          tr.addEventListener("dblclick", e => {
            if (e.target.closest(".col-save-check") || e.target.closest(".col-detail-link")) return;
            void loadDetailForRowIndices([i]).catch(() => {});
          });
        }
        tr.appendChild(tdP);
      } else if (row._detail_url) {
        tr.classList.add("preview-row-detail-modal");
        tr.title = "Clique na linha para ver o detalhe no painel.";
        tr.addEventListener("click", e => {
          if (e.target.closest(".col-save-check")) return;
          if (e.target.closest("a") || e.target.closest("button")) return;
          void openDetailModal(i);
        });
      }

      cols.forEach(c => {
        const td = document.createElement("td");
        td.textContent = row[c] || "";
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });

    updateDetailsHint();
  }

  // ===== Save =====
  async function onSaveClick() {
    const titulo = el["save-title"].value.trim() || "Sem título";
    const onlyFiltered = el["save-only-filtered"].checked;
    const checks = el["table-checkboxes"].querySelectorAll("input[type=checkbox]");
    const indicesToSave = [];
    checks.forEach(cb => { if (cb.checked) indicesToSave.push(+cb.dataset.tblIdx); });

    if (!indicesToSave.length) { setStatus("Nenhuma tabela marcada.", true); return; }

    el["btn-save"].disabled = true;
    setStatus("A gravar…");
    let saved = 0;
    for (const idx of indicesToSave) {
      const t = state.allTables.find(t => t.index === idx);
      if (!t) continue;
      let colunas, linhas;
      if (idx === state.selectedTableIndex) {
        colunas = visibleCols();
        let rows = state.rows;
        if (onlyFiltered) {
          rows = rows.filter((r, i) => rowPassesFilters(r) && state.rowSaveSelected.has(i));
        } else {
          rows = rows.filter((_, i) => state.rowSaveSelected.has(i));
        }
        linhas = rows.map(r => {
          const obj = {};
          colunas.forEach(c => obj[c] = r[c] || "");
          return obj;
        });
      } else {
        colunas = t.headers || [];
        linhas = t.rows || [];
      }

      try {
        const resp = await fetch("/api/salvar", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            titulo: indicesToSave.length > 1 ? `${titulo} (#${idx})` : titulo,
            url_final: scrapeMeta.url_final || "",
            periodo_de: scrapeMeta.periodo_de || "",
            periodo_ate: scrapeMeta.periodo_ate || "",
            tamanho_pagina: scrapeMeta.tamanho_pagina || 0,
            paginas_buscadas: scrapeMeta.paginas_buscadas || 0,
            colunas,
            linhas,
          }),
        });
        if (resp.ok) saved++;
      } catch {}
    }
    setStatus(`${saved} tabela(s) gravada(s).`);
    el["btn-save"].disabled = false;
  }

  // ===== Fetch details =====
  async function fetchDetailsFromApi(urls) {
    const resp = await fetch("/api/scrape-details", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls, wait_selector: "body" }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(apiErr(err.detail) || resp.statusText || "Falha no pedido");
    }
    const data = await resp.json();
    return data.details || [];
  }

  /** Junta o JSON devolvido por /api/scrape-details às linhas e às colunas visíveis. */
  function mergeDetailResults(details) {
    const newColKeys = new Set();
    for (const detail of details) {
      for (const key of Object.keys(detail)) {
        if (key !== "_detail_url" && key !== "_detail_id" && key !== "_erro") newColKeys.add(key);
      }
    }
    for (const detail of details) {
      const rowIdx = state.rows.findIndex(r => rowMatchesDetail(r, detail));
      if (rowIdx < 0) continue;
      for (const key of Object.keys(detail)) {
        if (key !== "_detail_url" && key !== "_detail_id") {
          state.rows[rowIdx][key] = detail[key];
        }
      }
    }
    for (const col of newColKeys) {
      if (!state.order.includes(col)) {
        state.order.push(col);
        state.visible[col] = true;
      }
    }
    return newColKeys.size;
  }

  /** Uma ou mais linhas (índices em state.rows). */
  async function loadDetailForRowIndices(indices) {
    const urls = [
      ...new Set(
        indices
          .map(i => state.rows[i])
          .filter(r => r && r._detail_url)
          .map(r => r._detail_url)
      ),
    ];
    if (!urls.length) {
      setStatus("Esta linha não tem URL de detalhe.", true);
      return;
    }
    setStatus(`A buscar ${urls.length} página(s) de detalhe no servidor (preenche colunas det_* nesta tabela)…`);
    try {
      const details = await fetchDetailsFromApi(urls);
      const addedTypes = mergeDetailResults(details);
      renderColumns();
      renderTable();
      updateDetailsHint();
      setStatus(
        urls.length === 1
          ? `Detalhe na tabela: ${addedTypes} tipo(s) de coluna det_* (deslize a tabela para a direita).`
          : `Detalhes obtidos para ${urls.length} contratos.`
      );
    } catch (err) {
      const msg = err && err.message ? err.message : String(err);
      setStatus(`Erro ao buscar detalhe: ${msg}`, true);
      renderColumns();
      renderTable();
      throw err;
    }
  }

  async function onFetchDetailsClick() {
    const hasRowSelection = state.rows.some((_, i) => state.rowSaveSelected.has(i));
    const selectedUrls = [];
    state.rows.forEach((r, i) => {
      const take = hasRowSelection ? state.rowSaveSelected.has(i) : true;
      if (take && r._detail_url) selectedUrls.push(r._detail_url);
    });
    const allUniq = [...new Set(selectedUrls)];
    const BATCH = DETAIL_BATCH;

    if (!allUniq.length) {
      setStatus("Nenhuma linha com link de detalhe. Extraia de novo a tabela de contratos.", true);
      return;
    }
    el["btn-fetch-details"].disabled = true;
    const total = allUniq.length;

    const nBatches = Math.ceil(allUniq.length / BATCH);
    try {
      for (let off = 0; off < allUniq.length; off += BATCH) {
        const uniqUrls = allUniq.slice(off, off + BATCH);
        const done = Math.min(off + uniqUrls.length, allUniq.length);
        const batchNum = Math.floor(off / BATCH) + 1;
        setStatus(
          hasRowSelection
            ? `Detalhes: lote ${batchNum}/${nBatches} — ${done}/${total} contratos (selecionados)…`
            : `Detalhes: lote ${batchNum}/${nBatches} — ${done}/${total} contratos…`
        );

        const details = await fetchDetailsFromApi(uniqUrls);
        mergeDetailResults(details);
      }

      renderColumns();
      renderTable();
      setStatus(`Detalhes obtidos para ${total} contrato(s). Colunas det_* adicionadas à tabela (deslize horizontalmente se necessário).`);
    } catch (err) {
      const msg = err && err.message ? err.message : String(err);
      setStatus(`Erro ao buscar detalhes: ${msg}`, true);
      throw err;
    } finally {
      el["btn-fetch-details"].disabled = false;
    }
  }

  // ===== Submit scrape =====
  async function onScrapeSubmit(e) {
    e.preventDefault();
    const url = el["url"].value.trim();
    if (!url) { setStatus("Informe uma URL.", true); return; }

    let ws = getWaitSelectorValue();
    if (isPortalUrl(url) && (ws === "table" || ws === ".table" || ws === "#table")) {
      ws = "#lista";
    }

    const paginacao = el["paginacao-portal"].checked;
    const maxPag = +el["max-paginas"].value || 1;
    const buscaTodas = el["buscar-todas"].checked;
    const epublicaMulti = isEpublicaUrl(url) && (buscaTodas || maxPag > 1);
    const epublicaContratos = isEpublicaUrl(url) && url.toLowerCase().includes("contratotable");

    if (paginacao && (buscaTodas || maxPag > 1)) {
      setStatus("A carregar várias páginas…");
    } else if (epublicaContratos && !epublicaMulti) {
      setStatus("A carregar listagem no e-publica (1 página; ajuste «Máx. páginas» ou «Buscar todas» para mais)…");
    } else if (epublicaMulti || epublicaContratos) {
      setStatus("A carregar listagem no e-publica (várias páginas; pode demorar)…");
    } else {
      setStatus("A carregar…");
    }
    el["btn-scrape"].disabled = true;

    const payload = {
      url,
      wait_selector: ws,
      iframe_auto: el["iframe-auto"].checked,
      iframe_selector: el["iframe-auto"].checked ? null : (el["iframe-selector"].value.trim() || null),
      table_index: +el["table-index"].value || 0,
      skip_first_column: el["skip-first-col"].checked,
      paginacao_portal: paginacao,
      tamanho_pagina: +el["tamanho-pagina"].value || 5,
      max_paginas: maxPag,
      buscar_todas_paginas: buscaTodas,
      periodo_de: el["periodo-de"].value || null,
      periodo_ate: el["periodo-ate"].value || null,
      extra_query: collectExtraQuery(),
    };

    try {
      const resp = await fetch("/api/scrape", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(apiErr(err.detail));
      }
      const data = await resp.json();
      initFromResponse(data);
      const n = data.count || 0;
      setStatus(`${n} linhas na listagem.`);
      const mode = getDetailMode();
      const autoFetch = el["detail-auto-fetch"] && el["detail-auto-fetch"].checked;
      const nLinks = state.rows.filter(r => r._detail_url).length;
      if (mode === "merge" && autoFetch && nLinks) {
        setStatus(`${n} linhas. A seguir: ${nLinks} páginas de detalhe em lotes de ${DETAIL_BATCH} (vários minutos).`);
        try {
          await onFetchDetailsClick();
        } catch (detailErr) {
          setStatus(
            `Listagem carregada (${n} linhas). Falha ao buscar detalhes: ${detailErr.message}. ` +
              "Clique em «Buscar detalhes» ou «Trazer detalhe» na linha / duplo clique na linha para tentar de novo.",
            true
          );
          updateDetailsHint();
        }
      } else if (mode === "merge" && nLinks) {
        setStatus(
          `${n} linhas na listagem. Para juntar CNPJ e dados do «Ver detalhe», use «Buscar detalhes» ou «Trazer detalhe» na linha.`
        );
        updateDetailsHint();
      } else if (mode === "modal" && nLinks) {
        setStatus(`${n} linhas na listagem. Clique numa linha para abrir o detalhe no painel.`);
        updateDetailsHint();
      }
    } catch (e) {
      setStatus("Erro: " + e.message, true);
      el["result-area"].classList.remove("visible");
    } finally {
      el["btn-scrape"].disabled = false;
    }
  }

  // ===== Saved tab =====
  async function loadConsultas() {
    try {
      const resp = await fetch("/api/consultas");
      const data = await resp.json();
      renderConsultasList(data);
    } catch {}
  }

  function renderConsultasList(list) {
    const wrap = el["consultas-list"];
    wrap.innerHTML = "";
    if (!list.length) { wrap.textContent = "Nenhuma gravação."; return; }
    list.forEach(c => {
      const item = document.createElement("div");
      item.className = "consulta-item";
      item.innerHTML =
        `<span>${esc(c.titulo || "Sem título")} — ${c.total_linhas} linhas</span>` +
        `<span class="meta">${formatDateTimeBrPlain(c.criado_em)}</span>`;
      item.addEventListener("click", () => loadConsultaDetalhe(c.id));
      wrap.appendChild(item);
    });
  }

  async function loadConsultaDetalhe(id) {
    try {
      const resp = await fetch(`/api/consultas/${id}`);
      if (!resp.ok) return;
      const data = await resp.json();
      savedConsultaId = id;
      savedConsultaData = data;
      savedEditing = false;
      renderSavedDetail(data);
      el["saved-detail"].classList.add("visible");
      el["saved-edit-actions"].classList.remove("visible");
    } catch {}
  }

  function renderSavedDetail(d) {
    el["saved-titulo"].value = d.titulo || "";
    el["saved-info"].innerHTML =
      `URL: ${esc(d.url_final || "—")} | Período: ${esc(d.periodo_de || "—")} → ${esc(d.periodo_ate || "—")} | ` +
      `Páginas: ${d.paginas_buscadas || 0} | Linhas: ${d.total_linhas || 0}`;
    renderSavedTableReadonly(d);
  }

  function renderSavedTableReadonly(d) {
    const cols = d.colunas || [];
    const rows = d.rows || [];
    const thead = el["saved-thead"];
    const tbody = el["saved-tbody"];
    thead.innerHTML = "";
    tbody.innerHTML = "";

    const trH = document.createElement("tr");
    cols.forEach(c => {
      const th = document.createElement("th");
      th.textContent = c;
      trH.appendChild(th);
    });
    thead.appendChild(trH);

    rows.forEach(row => {
      const tr = document.createElement("tr");
      cols.forEach(c => {
        const td = document.createElement("td");
        td.textContent = row[c] || "";
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  function renderSavedTableEditable() {
    if (!savedConsultaData) return;
    const cols = savedConsultaData.colunas || [];
    const rows = savedConsultaData.rows || [];
    const thead = el["saved-thead"];
    const tbody = el["saved-tbody"];
    thead.innerHTML = "";
    tbody.innerHTML = "";

    const trH = document.createElement("tr");
    cols.forEach(c => {
      const th = document.createElement("th");
      th.textContent = c;
      trH.appendChild(th);
    });
    const thAct = document.createElement("th");
    thAct.textContent = "";
    trH.appendChild(thAct);
    thead.appendChild(trH);

    rows.forEach((row, ri) => {
      const tr = document.createElement("tr");
      cols.forEach(c => {
        const td = document.createElement("td");
        const inp = document.createElement("input");
        inp.type = "text";
        inp.value = row[c] || "";
        inp.dataset.col = c;
        inp.dataset.row = ri;
        td.appendChild(inp);
        tr.appendChild(td);
      });
      const tdRm = document.createElement("td");
      const btnRm = document.createElement("button");
      btnRm.className = "btn btn-danger btn-small";
      btnRm.textContent = "×";
      btnRm.addEventListener("click", () => {
        savedConsultaData.rows.splice(ri, 1);
        renderSavedTableEditable();
      });
      tdRm.appendChild(btnRm);
      tr.appendChild(tdRm);
      tbody.appendChild(tr);
    });
  }

  function savedAppendEmptyRow() {
    if (!savedConsultaData) return;
    const cols = savedConsultaData.colunas || [];
    const empty = {};
    cols.forEach(c => empty[c] = "");
    savedConsultaData.rows.push(empty);
    renderSavedTableEditable();
  }

  function gatherSavedRowsFromEditDom() {
    if (!savedConsultaData) return [];
    const cols = savedConsultaData.colunas || [];
    const tbody = el["saved-tbody"];
    const trs = tbody.querySelectorAll("tr");
    const rows = [];
    trs.forEach(tr => {
      const row = {};
      tr.querySelectorAll("input[data-col]").forEach(inp => {
        row[inp.dataset.col] = inp.value;
      });
      if (Object.keys(row).length) rows.push(row);
    });
    return rows;
  }

  async function savedGuardarTabelaEditada() {
    if (!savedConsultaId || !savedConsultaData) return;
    const rows = gatherSavedRowsFromEditDom();
    try {
      const resp = await fetch(`/api/consultas/${savedConsultaId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          titulo: el["saved-titulo"].value.trim(),
          colunas: savedConsultaData.colunas,
          linhas: rows,
        }),
      });
      if (resp.ok) {
        savedConsultaData.rows = rows;
        savedEditing = false;
        renderSavedDetail(savedConsultaData);
        el["saved-edit-actions"].classList.remove("visible");
        loadConsultas();
      }
    } catch {}
  }

  async function savedGuardarTitulo() {
    if (!savedConsultaId) return;
    const titulo = el["saved-titulo"].value.trim();
    try {
      await fetch(`/api/consultas/${savedConsultaId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ titulo }),
      });
      loadConsultas();
    } catch {}
  }

  async function savedExportarExcel() {
    if (!savedConsultaId) return;
    try {
      const resp = await fetch(`/api/consultas/${savedConsultaId}/excel`);
      const blob = await resp.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `consulta_${savedConsultaId}.xlsx`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch {}
  }

  async function excluirConsultaPorId(id) {
    if (!confirm("Eliminar esta gravação?")) return;
    try {
      await fetch(`/api/consultas/${id}`, { method: "DELETE" });
      savedConsultaId = null;
      savedConsultaData = null;
      el["saved-detail"].classList.remove("visible");
      loadConsultas();
    } catch {}
  }

  // ===== New empty consulta =====
  function openNewConsultaDialog() {
    el["new-consulta-titulo"].value = "";
    el["new-consulta-colunas"].value = "";
    el["dialog-new-consulta"].showModal();
  }

  async function createNewConsulta() {
    const titulo = el["new-consulta-titulo"].value.trim() || "Nova gravação";
    const colsStr = el["new-consulta-colunas"].value.trim();
    const colunas = colsStr ? colsStr.split(",").map(s => s.trim()).filter(Boolean) : ["Coluna1"];
    try {
      const resp = await fetch("/api/consultas", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ titulo, colunas, linhas: [] }),
      });
      if (resp.ok) {
        el["dialog-new-consulta"].close();
        loadConsultas();
      }
    } catch {}
  }

  // ===== Tabs =====
  function setupTabs() {
    document.querySelectorAll(".tab-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        document.querySelectorAll(".tab-panel").forEach(p => p.classList.add("hidden"));
        document.getElementById(btn.dataset.tab).classList.remove("hidden");
        if (btn.dataset.tab === "tab-gravadas") loadConsultas();
      });
    });
  }

  // ===== Escape HTML =====
  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  // ===== Bind =====
  function bind() {
    IDS.forEach(id => { el[id] = document.getElementById(id); });

    el["url"].value = DEFAULT_URL;

    el["url"].addEventListener("blur", syncUrlDerivedOptions);
    el["url"].addEventListener("change", syncUrlDerivedOptions);
    el["wait-selector"].addEventListener("change", () => {
      syncWaitCustomVisibility();
      updateWaitDetectedHint();
    });
    el["iframe-auto"].addEventListener("change", syncIframeAutoUi);

    el["btn-add-param"].addEventListener("click", () => {
      state.dynamicParams.push({ key: "", value: "" });
      renderDynamicParams();
    });
    document.querySelectorAll('input[name="detail-mode"]').forEach(radio => {
      radio.addEventListener("change", () => {
        syncDetailModeUi();
        syncFetchDetailsButtonVisibility();
        renderTable();
        updateDetailsHint();
      });
    });
    const dmClose = el["detail-modal-close"];
    const dmBackdrop = el["detail-modal-backdrop"];
    if (dmClose) dmClose.addEventListener("click", closeDetailModal);
    if (dmBackdrop) dmBackdrop.addEventListener("click", closeDetailModal);
    document.addEventListener("keydown", e => {
      if (e.key !== "Escape") return;
      const modal = el["detail-modal"];
      if (modal && !modal.classList.contains("hidden")) closeDetailModal();
    });

    document.getElementById("form-scrape").addEventListener("submit", onScrapeSubmit);
    el["btn-save"].addEventListener("click", onSaveClick);
    el["btn-fetch-details"].addEventListener("click", () => {
      void onFetchDetailsClick().catch(() => {});
    });
    el["global-search"].addEventListener("input", renderTable);

    el["btn-saved-titulo"].addEventListener("click", savedGuardarTitulo);
    el["btn-saved-excel"].addEventListener("click", savedExportarExcel);
    el["btn-saved-edit"].addEventListener("click", () => {
      savedEditing = !savedEditing;
      if (savedEditing) {
        renderSavedTableEditable();
        el["saved-edit-actions"].classList.add("visible");
      } else {
        if (savedConsultaData) renderSavedDetail(savedConsultaData);
        el["saved-edit-actions"].classList.remove("visible");
      }
    });
    el["btn-saved-delete"].addEventListener("click", () => {
      if (savedConsultaId) excluirConsultaPorId(savedConsultaId);
    });
    el["btn-saved-add-row"].addEventListener("click", savedAppendEmptyRow);
    el["btn-saved-save-edit"].addEventListener("click", savedGuardarTabelaEditada);
    el["btn-new-consulta"].addEventListener("click", openNewConsultaDialog);
    el["btn-new-consulta-ok"].addEventListener("click", createNewConsulta);
    el["btn-new-consulta-cancel"].addEventListener("click", () => el["dialog-new-consulta"].close());

    setupTabs();
    syncWaitCustomVisibility();
    syncIframeAutoUi();
    syncUrlDerivedOptions();
    updateWaitDetectedHint();
  }

  document.addEventListener("DOMContentLoaded", bind);
})();
