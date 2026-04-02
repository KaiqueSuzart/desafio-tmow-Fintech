# Desafio técnico — Otmow Fintech

**Extrator inteligente de dados tabulares** a partir de portais web reais — com foco em **transparência pública** (e-publica, Portal da Transparência) e SPAs Angular onde o HTML “clássico” não chega.

Este repositório é a entrega do **desafio técnico para a Otmow Fintech**: uma aplicação **full-stack local** que combina **automação de browser headless**, **API REST** e **interface web** para extrair listagens, cruzar com páginas de detalhe e persistir resultados de forma auditável.

---

## O problema e a abordagem

Portais de contratos e transparência misturam **tabelas renderizadas em JavaScript**, **iframes**, **hash routing** e **paginação híbrida** (UI + URL). Um parser só de HTML estático falha; aqui a solução é **Playwright (Chromium)** para executar a página como um utilizador, esperar pelos seletores certos e extrair estrutura tabular via DOM.

No **e-publica**, a listagem e a ficha de contrato são mundos diferentes: a solução expõe **`/api/scrape-details`** e, na UI, dois modos — **fundir colunas `det_*` na grelha** ou **modal por linha** — para o utilizador decidir entre volume de dados e rapidez.

---

## Stack

| Camada | Tecnologia |
|--------|------------|
| Backend | **Python 3.10+**, **FastAPI**, **Uvicorn** |
| Scraping | **Playwright** (Chromium), lógica dedicada em `scrape_lista.py` |
| Frontend | HTML/CSS/JS vanilla (sem build step), consumo da API |
| Persistência | **SQLite** (`data/portal_lista.db`), export **Excel** (`openpyxl`) |
| Opcional | **OpenAI** para sugestão de filtros (`ai_suggest.py`, chave no `.env`) |

---

## Funcionalidades principais

- **Extração robusta** — `POST /api/scrape`: URL, seletor de espera, índice de tabela, iframe automático ou manual, período em hash/query, parâmetros extra.
- **e-publica** — Paginação por clique em «próxima», **linhas por página** (ajuste no browser + **limite garantido no servidor**), respeito a **máximo de páginas** / «buscar todas».
- **Detalhe por contrato** — `POST /api/scrape-details`: lotes de URLs, parsing de campos tipo ficha (incl. labels longos `det_*`).
- **UI** — Pré-visualização com filtros, reordenação de colunas, gravação de consultas, dois modos de detalhe (merge vs modal).
- **Gravações** — CRUD de consultas, edição de tabela, download Excel.

---

## Início rápido (Windows)

```powershell
.\setup.ps1
copy .env.example .env
# Opcional: OPENAI_API_KEY para sugestões por IA
python -m uvicorn app:app --host 127.0.0.1 --port 8765
```

Abrir: `http://127.0.0.1:8765`

Instalação manual: `pip install -r requirements.txt` e `python -m playwright install chromium`.

---

## Variáveis de ambiente (`.env`)

| Variável | Uso |
|----------|-----|
| `OPENAI_API_KEY` | Opcional — `/api/ai/suggest-filters` |
| `PLAYWRIGHT_NAV_TIMEOUT_MS` | Opcional — timeout de navegação |

Nunca commite `.env` (está no `.gitignore`).

---

## Estrutura do código

| Ficheiro | Responsabilidade |
|----------|------------------|
| `app.py` | Rotas FastAPI, montagem de `/assets` |
| `scrape_lista.py` | Playwright, e-publica, paginação portal, extração JS de tabelas e detalhes |
| `db_local.py` | SQLite, Excel |
| `ai_suggest.py` | Integração OpenAI (opcional) |
| `web/` | `index.html`, `static/app.js`, `static/style.css` |

---

## API (referência rápida)

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/` | Interface web |
| `POST` | `/api/scrape` | Extrai tabelas |
| `POST` | `/api/scrape-details` | Extrai fichas de detalhe por URL |
| `POST` | `/api/salvar` | Grava consulta |
| `GET` | `/api/consultas` | Lista gravações |
| `GET` | `/api/consultas/{id}` | Detalhe |
| `POST` | `/api/consultas` | Nova gravação vazia |
| `PUT` | `/api/consultas/{id}` | Atualiza |
| `DELETE` | `/api/consultas/{id}` | Remove |
| `GET` | `/api/consultas/{id}/excel` | Excel |
| `POST` | `/api/ai/suggest-filters` | Sugestão IA (requer chave) |

---

## Segurança e ética

Utilize apenas em fontes e para fins **legalmente autorizados**. Respeite termos de uso, `robots.txt` e a LGPD quando houver dados pessoais. Não exponha chaves API nem bases de dados com dados sensíveis.

---

## Autor

Entrega de **desafio técnico — Otmow Fintech** · Repositório: [desafio-tmow-Fintech](https://github.com/KaiqueSuzart/desafio-tmow-Fintech).
