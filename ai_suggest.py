"""ai_suggest.py — Assistente IA de filtros (GPT-4o-mini + heurísticas locais)."""

from __future__ import annotations

import datetime
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

# ---------------------------------------------------------------------------
# Constantes — meses em PT
# ---------------------------------------------------------------------------
MES_PT: dict[str, int] = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3,
    "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}

# ---------------------------------------------------------------------------
# UF — siglas e nomes
# ---------------------------------------------------------------------------
_VALID_UF: frozenset[str] = frozenset([
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO",
    "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI",
    "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
])

_BR_UF_NAME_PAIRS: list[tuple[str, str]] = sorted([
    ("acre", "AC"), ("alagoas", "AL"), ("amapa", "AP"),
    ("amazonas", "AM"), ("bahia", "BA"), ("ceara", "CE"),
    ("distrito federal", "DF"), ("espirito santo", "ES"),
    ("goias", "GO"), ("maranhao", "MA"), ("mato grosso", "MT"),
    ("mato grosso do sul", "MS"), ("minas gerais", "MG"),
    ("para", "PA"), ("paraiba", "PB"), ("parana", "PR"),
    ("pernambuco", "PE"), ("piaui", "PI"),
    ("rio de janeiro", "RJ"), ("rio grande do norte", "RN"),
    ("rio grande do sul", "RS"), ("rondonia", "RO"),
    ("roraima", "RR"), ("santa catarina", "SC"),
    ("sao paulo", "SP"), ("sergipe", "SE"), ("tocantins", "TO"),
    # variantes com acento normalizado
    ("são paulo", "SP"), ("paraná", "PR"), ("piauí", "PI"),
    ("ceará", "CE"), ("goiás", "GO"), ("maranhão", "MA"),
    ("amapá", "AP"), ("pará", "PA"), ("paraíba", "PB"),
    ("rondônia", "RO"), ("espírito santo", "ES"),
], key=lambda x: -len(x[0]))

# ---------------------------------------------------------------------------
# Funções de período
# ---------------------------------------------------------------------------

def _month_pattern() -> re.Pattern:
    names = sorted(MES_PT.keys(), key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(names) + r")\b", re.IGNORECASE)


_MONTH_RE = _month_pattern()


def parse_periodo_meses_pt(intent: str) -> tuple[str | None, str | None]:
    matches = _MONTH_RE.findall(intent.lower())
    if not matches:
        return None, None

    year_match = re.search(r"\b(20\d{2})\b", intent)
    year = int(year_match.group(1)) if year_match else datetime.date.today().year

    months = [MES_PT[m.lower()] for m in matches]
    months = list(dict.fromkeys(months))

    if len(months) >= 2:
        m_de, m_ate = min(months), max(months)
    else:
        m_de = m_ate = months[0]

    return f"{year:04d}-{m_de:02d}", f"{year:04d}-{m_ate:02d}"


def _valid_iso_mes(s: Any) -> str | None:
    if not isinstance(s, str):
        return None
    if re.match(r"^\d{4}-\d{2}$", s):
        return s
    return None


def _intent_has_explicit_year(text: str) -> bool:
    return bool(re.search(r"\b20\d{2}\b", text))


def _apply_current_year_if_no_explicit_year_in_intent(
    intent: str, pde: str | None, pat: str | None
) -> tuple[str | None, str | None]:
    if _intent_has_explicit_year(intent):
        return pde, pat
    current_year = str(datetime.date.today().year)
    if pde and re.match(r"^\d{4}-\d{2}$", pde):
        pde = current_year + pde[4:]
    if pat and re.match(r"^\d{4}-\d{2}$", pat):
        pat = current_year + pat[4:]
    return pde, pat


# ---------------------------------------------------------------------------
# UF / estado brasileiro
# ---------------------------------------------------------------------------

def _fold_for_uf(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = re.sub(r"[\u0300-\u036f]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _uf_sigla_from_intent(intent: str) -> str | None:
    text_upper = intent.upper()
    for word in re.findall(r"\b[A-Z]{2}\b", text_upper):
        if word in _VALID_UF:
            return word

    folded = _fold_for_uf(intent)
    for name, sigla in _BR_UF_NAME_PAIRS:
        name_folded = _fold_for_uf(name)
        if name_folded in folded:
            return sigla
    return None


def _sigla_from_localidade_value(val: str) -> str | None:
    folded = _fold_for_uf(val)
    for name, sigla in _BR_UF_NAME_PAIRS:
        name_folded = _fold_for_uf(name)
        if name_folded == folded or folded.startswith(name_folded):
            return sigla
    return None


def _form_field_keys_lower(form_fields: list[dict]) -> dict[str, str]:
    return {f["name"].lower(): f["name"] for f in form_fields if f.get("name")}


def apply_brazil_uf_rules(
    params: dict[str, str], intent: str, form_fields: list[dict]
) -> dict[str, str]:
    keys_lower = _form_field_keys_lower(form_fields)
    sigla = _uf_sigla_from_intent(intent)

    if not sigla:
        loc_key = keys_lower.get("localidadegasto")
        if loc_key and loc_key in params:
            sigla = _sigla_from_localidade_value(params[loc_key])

    if not sigla:
        return params

    uf_real_key = keys_lower.get("uf") or keys_lower.get("estado")
    if uf_real_key:
        params[uf_real_key] = sigla

    loc_key = keys_lower.get("localidadegasto")
    if loc_key and loc_key in params:
        loc_val = params[loc_key]
        if _sigla_from_localidade_value(loc_val):
            del params[loc_key]

    return params


# ---------------------------------------------------------------------------
# Sanitização
# ---------------------------------------------------------------------------
_NULL_STRINGS = {"none", "null", "undefined", "nan", ""}


def _sanitize_params_from_model(raw: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for k, v in raw.items():
        if v is None or isinstance(v, (dict, list)):
            continue
        s = str(v).strip()
        if s.lower() in _NULL_STRINGS:
            continue
        result[k] = s
    return result


def _sanitize_extra_query(extra: dict | None) -> dict[str, str]:
    if not extra:
        return {}
    return _sanitize_params_from_model(extra)


# ---------------------------------------------------------------------------
# Regras Portal — funções/subfunções
# ---------------------------------------------------------------------------
_PORTAL_FUNCAO_MAP: dict[str, str] = {
    "saude": "FN10", "saúde": "FN10",
    "educacao": "FN12", "educação": "FN12",
    "seguranca": "FN06", "segurança": "FN06",
    "defesa": "FN05",
    "assistencia social": "FN08", "assistência social": "FN08",
    "transporte": "FN26", "transportes": "FN26",
    "cultura": "FN13",
    "ciencia": "FN19", "ciência": "FN19",
    "agricultura": "FN20",
    "meio ambiente": "FN18",
}


def _apply_portal_funcao_rules(
    params: dict[str, str], intent: str, form_fields: list[dict], url: str
) -> dict[str, str]:
    if "portaldatransparencia.gov.br" not in url.lower():
        return params
    folded = _fold_for_uf(intent)
    keys_lower = _form_field_keys_lower(form_fields)
    funcao_key = keys_lower.get("funcaosubfuncao") or keys_lower.get("funcao")
    if not funcao_key:
        return params
    for keyword, code in _PORTAL_FUNCAO_MAP.items():
        kw_folded = _fold_for_uf(keyword)
        if kw_folded in folded:
            params[funcao_key] = code
            break
    return params


# ---------------------------------------------------------------------------
# Heurísticas locais
# ---------------------------------------------------------------------------

def _heuristic_suggest(
    intent: str, form_fields: list[dict], url: str
) -> dict[str, str]:
    params: dict[str, str] = {}
    keys_lower = _form_field_keys_lower(form_fields)

    sigla = _uf_sigla_from_intent(intent)
    if sigla:
        uf_key = keys_lower.get("uf") or keys_lower.get("estado")
        if uf_key:
            params[uf_key] = sigla

    num_match = re.search(r"\b(\d{4,}(?:[.,]\d+)?)\b", intent.replace(".", ""))
    if num_match:
        valor = num_match.group(1).replace(",", ".")
        for f in form_fields:
            n = f["name"].lower()
            if ("liquidado" in n and "de" in n) or ("empenhado" in n and "de" in n):
                params[f["name"]] = valor
                break

    params = apply_brazil_uf_rules(params, intent, form_fields)
    params = _apply_portal_funcao_rules(params, intent, form_fields, url)
    return params


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def suggest_filters_ai(
    url: str, intent: str, form_fields: list[dict]
) -> dict[str, Any]:
    pde_h, pat_h = parse_periodo_meses_pt(intent)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()

    if api_key:
        try:
            return _suggest_with_openai(url, intent, form_fields, api_key, pde_h, pat_h)
        except ImportError:
            return _fallback_result(
                intent, form_fields, url, pde_h, pat_h,
                note="openai não instalado — pip install openai. Usando heurísticas.",
            )
        except Exception as exc:
            err_str = str(exc)
            if "401" in err_str or "Unauthorized" in err_str:
                note = "Chave OpenAI inválida ou expirada. Usando heurísticas."
            else:
                note = f"Erro OpenAI: {err_str[:120]}. Usando heurísticas."
            return _fallback_result(intent, form_fields, url, pde_h, pat_h, note=note)

    return _fallback_result(
        intent, form_fields, url, pde_h, pat_h,
        note="Sem OPENAI_API_KEY — usando heurísticas locais.",
    )


def _suggest_with_openai(
    url: str, intent: str, form_fields: list[dict],
    api_key: str, pde_h: str | None, pat_h: str | None,
) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    today = datetime.date.today()
    current_year = today.year

    compact = [
        {"name": f["name"], "type": f.get("type", ""), "label": f.get("label", "")}
        for f in form_fields[:40]
    ]

    system_msg = (
        f"Hoje é {today.isoformat()}, ano corrente {current_year}. "
        "Você é um assistente que mapeia pedidos em linguagem natural para parâmetros "
        "de query string de sites brasileiros. "
        "Use sigla UF (ex. SP, RJ) no campo «uf» quando existir. "
        "Nunca devolva null como texto."
    )
    user_msg = (
        f"URL: {url}\n"
        f"Pedido do utilizador: {intent}\n"
        f"Campos do formulário: {compact}\n\n"
        "Devolva JSON com:\n"
        '- "params": dict de chave→valor (query string)\n'
        '- "periodo_de_mes": "YYYY-MM" ou null\n'
        '- "periodo_ate_mes": "YYYY-MM" ou null\n'
        '- "note": explicação curta em PT\n\n'
        f"Ano corrente: {current_year}. Se o utilizador não indicar ano, use {current_year}. "
        "Use a sigla UF no campo «uf». Nunca retorne null como string."
    )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.15,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
    )

    import json
    data = json.loads(resp.choices[0].message.content or "{}")
    raw_params = data.get("params", {})
    params = _sanitize_params_from_model(raw_params)
    params = apply_brazil_uf_rules(params, intent, form_fields)
    params = _apply_portal_funcao_rules(params, intent, form_fields, url)

    pde = _valid_iso_mes(data.get("periodo_de_mes")) or pde_h
    pat = _valid_iso_mes(data.get("periodo_ate_mes")) or pat_h
    pde, pat = _apply_current_year_if_no_explicit_year_in_intent(intent, pde, pat)

    note = data.get("note", "Parâmetros sugeridos pela IA.")
    return {
        "ok": True,
        "message": "Parâmetros sugeridos com sucesso.",
        "params": params,
        "periodo_de_mes": pde,
        "periodo_ate_mes": pat,
        "note": str(note),
    }


def _fallback_result(
    intent: str, form_fields: list[dict], url: str,
    pde_h: str | None, pat_h: str | None,
    note: str = "",
) -> dict[str, Any]:
    params = _heuristic_suggest(intent, form_fields, url)
    pde_h, pat_h = _apply_current_year_if_no_explicit_year_in_intent(intent, pde_h, pat_h)
    return {
        "ok": True,
        "message": note or "Parâmetros sugeridos por heurísticas.",
        "params": params,
        "periodo_de_mes": pde_h,
        "periodo_ate_mes": pat_h,
        "note": note or "Heurísticas locais utilizadas.",
    }
