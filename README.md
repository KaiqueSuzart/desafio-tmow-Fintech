# Extrator de Tabelas HTML

Aplicação local para **extrair tabelas** de páginas web (incluindo SPAs e portais de transparência como **e-publica** e o **Portal da Transparência**), visualizar o resultado, opcionalmente **enriquecer com dados da página de detalhe** e **gravar** consultas em SQLite.

## O que faz

- **Carregar dados** (`POST /api/scrape`): abre a URL com **Playwright** (Chromium), espera por um seletor CSS, extrai tabelas e devolve colunas + linhas em JSON para a interface web.
- **Suporte e-publica** (ex.: listagem de contratos): paginação por clique em «próxima», **linhas por página** respeitadas no browser quando possível e **limite garantido no servidor** para não trazer mais linhas do que o pedido.
- **Detalhe do registo** (`POST /api/scrape-details`): para cada URL de detalhe, extrai campos (ex.: CNPJ, objeto, responsáveis) e devolve pares chave/valor.
- **Interface web** (`/`): formulário de extração, pré-visualização com filtros, colunas reordenáveis, duas formas de ver o detalhe:
  - **Juntar na tabela** — colunas `det_*` fundidas na grelha; opção de buscar detalhes em lote após carregar.
  - **Só listagem** — tabela só com a listagem; **clique na linha** abre um **modal** com todos os campos do detalhe.
- **Gravações** (`/api/salvar`, `/api/consultas`, …): guarda títulos e linhas em **SQLite** (`data/portal_lista.db`), edição, exportação Excel.
- **Sugestão de filtros com IA** (`POST /api/ai/suggest-filters`): opcional, requer `OPENAI_API_KEY` no `.env`.

## Requisitos

- Python 3.10+ (recomendado 3.12)
- Chromium via Playwright (`playwright install chromium`)

## Instalação rápida (Windows)

Na pasta do projeto:

```powershell
.\setup.ps1
```

Ou manualmente:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env
# Edite .env e defina OPENAI_API_KEY se quiser usar a sugestão por IA
```

## Configuração (`.env`)

Copie `.env.example` para `.env`.

| Variável | Descrição |
|----------|-----------|
| `OPENAI_API_KEY` | Chave da OpenAI (opcional; só para `/api/ai/suggest-filters`). |
| `PLAYWRIGHT_NAV_TIMEOUT_MS` | Timeout de navegação Playwright em ms (opcional). |

**Não commite** o ficheiro `.env` (já está no `.gitignore`).

## Como executar

```powershell
python -m uvicorn app:app --host 127.0.0.1 --port 8765
```

Abra no browser: `http://127.0.0.1:8765`

## Estrutura do projeto

| Ficheiro / pasta | Função |
|------------------|--------|
| `app.py` | FastAPI: rotas REST, montagem de estáticos em `/assets`. |
| `scrape_lista.py` | Lógica de scraping: Playwright, e-publica, paginação, extração de tabelas e detalhes. |
| `db_local.py` | SQLite: consultas gravadas, linhas, Excel. |
| `ai_suggest.py` | Integração OpenAI para sugestão de parâmetros (opcional). |
| `web/index.html` | Interface principal. |
| `web/static/app.js` | Cliente: tabela, modos de detalhe, gravações. |
| `web/static/style.css` | Estilos. |
| `data/` | Base de dados local (criada em runtime; ignorada pelo Git). |
| `prompt.txt` | Notas / contexto longo para desenvolvimento (opcional). |

## API (resumo)

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/` | Interface web. |
| `POST` | `/api/scrape` | Extrai tabelas da URL (corpo JSON com `url`, `wait_selector`, `tamanho_pagina`, `max_paginas`, etc.). |
| `POST` | `/api/scrape-details` | Lista de URLs de detalhe → JSON com campos extraídos por URL. |
| `POST` | `/api/salvar` | Grava consulta + linhas. |
| `GET` | `/api/consultas` | Lista gravações. |
| `GET` | `/api/consultas/{id}` | Detalhe de uma gravação. |
| `POST` | `/api/consultas` | Cria gravação vazia (modelo). |
| `PUT` | `/api/consultas/{id}` | Atualiza título ou tabela. |
| `DELETE` | `/api/consultas/{id}` | Remove gravação. |
| `GET` | `/api/consultas/{id}/excel` | Download Excel. |
| `POST` | `/api/ai/suggest-filters` | Sugestão de filtros (requer API key). |

## Parâmetros úteis no formulário

- **Linhas por página** — tamanho da página na listagem (e-publica / portal com paginação); o servidor limita o número de linhas devolvidas por página de acordo com este valor.
- **Máx. páginas** / **Buscar todas as páginas** — quantas folhas de listagem percorrer.
- **Paginação na URL (Portal)** — modo específico para portal da transparência com query string.
- **Parâmetros extra na URL** — chave/valor fundidos no hash/query (útil em SPAs).

## Segurança e boa prática

- Use só em **URLs que tem autorização** para aceder e extrair.
- Respeite **robots.txt**, termos do site e legislação aplicável.
- Não publique **chaves API** nem bases `data/*.db` com dados sensíveis.

## Licença

Uso interno / projeto pessoal — ajuste a licença se for distribuir.

---

## Publicar no GitHub

Se ainda não tiver remoto:

```powershell
cd caminho\para\este\projeto
git init
git add .
git commit -m "Initial commit"
```

Com [GitHub CLI](https://cli.github.com/) autenticado (`gh auth login`):

```powershell
gh repo create NOME-DO-REPO --public --source=. --remote=origin --push
```

Ou crie um repositório vazio no site do GitHub e ligue:

```powershell
git remote add origin https://github.com/SEU-USUARIO/NOME-DO-REPO.git
git branch -M main
git push -u origin main
```
