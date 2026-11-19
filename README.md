# FGV Conhecimento — Crawler, Parser e Consolidador de Provas Objetivas (Alta Veracidade)

Pipeline completo para coleta automatizada, auditoria de veracidade, parsing estruturado e consolidação/higienização de provas objetivas (**Tipo 1 / Branca**, 4 ou 5 alternativas e **Certo/Errado**) e gabaritos oficiais do portal [FGV Conhecimento](https://conhecimento.fgv.br/concursos), com suporte a fallback assistido e classificação taxonômica via **Ollama (`llama3.1:8b`)**.

## Estado Consolidado da Base (`./data/processed`)

A base está **100% consolidada, higienizada, indexada (`_index.json`) e pronta para ingestão** no banco de dados da aplicação:

- **1.499 provas objetivas processadas** (em **231 concursos** únicos):
  - **1.311 provas** de múltipla escolha com 5 alternativas (`A`–`E` — `multiple_choice_5`)
  - **187 provas** de múltipla escolha com 4 alternativas (`A`–`D` — `multiple_choice_4`)
  - **1 prova** oficial no formato Certo/Errado (`C`/`E` — `certo_errado`: `mre_tipo-a.json`)
  - *(As demais 55 pastas em `./data/raw` são cadernos exclusivamente discursivos, peças práticas ou editais sem questões objetivas).*
- **101.769 questões objetivas extraídas e validadas** (**99,90%** de retenção sobre o total teórico de 101.869 questões; **1.415 de 1.499 provas — 94,4% — sem nenhuma questão faltante**):
  - **58.336 questões únicas deduplicadas** via `question_hash` (SHA-1 determinístico)
  - **43.433 aparições compartilhadas** (questões de módulos básicos comuns aplicadas a múltiplos cargos de um mesmo concurso)
- **101.769 questões gabaritadas (100,00% de cobertura oficial)** — `0` questões sem resposta, `0` provas com gabarito parcial e `0` questões de 4 alternativas com gabarito `"E"` inválido
- **101.769 questões classificadas em 61 disciplinas canônicas especializadas (100,00%)** e separadas por módulo (`"Conhecimentos Básicos"`: 52.028 | `"Conhecimentos Específicos"`: 49.741)
- **749 questões dependentes de interpretação enriquecidas com `base_text`** (propagação automática do texto-base compartilhado da questão líder do bloco)
- **1.738 questões anuladas** oficialmente identificadas (`annulled: true`, `answer: "ANULADA"`)
- **5.511 questões com imagens/gráficos recortados** em 200 DPI (`./data/processed/images/`, incluindo questões com alternativas puramente visuais/Libras/desenho técnico)
- **Catálogo mestre `./data/processed/_index.json`** e **100% dos arquivos enriquecidos com metadados para filtros da aplicação**: `banca` (`"FGV"`), `year` (2012–2026), `uf` (27 UFs + `BR`), `level` (`Superior`, `Médio`, `Fundamental`), `question_type`, `cargo_title` higienizado, `module`, `discipline`, `base_text`, `image_url` (CDN gratuita jsDelivr/GitHub) e `question_hash`

Consulte o relatório completo em [doc/panorama_extracao.md](doc/panorama_extracao.md) e o guia de arquitetura e hospedagem gratuita em [doc/plano_deploy_gratuito.md](doc/plano_deploy_gratuito.md).

## Estrutura do Projeto

```text
.
├── .gitignore
├── requirements.txt
├── README.md
├── doc/
│   ├── panorama_extracao.md      # Relatório completo do banco consolidado, taxonomia e esquema JSON
│   └── plano_deploy_gratuito.md  # Arquitetura e passo a passo de deploy $0/mês (Supabase/Turso + jsDelivr + Vercel)
└── src/
    ├── crawler_fgv.py            # Coleta resiliente (Playwright) + pareamento Cargo/Tipo 1 + fallback Wayback
    ├── parser_fgv.py             # Parser 2 colunas (PyMuPDF), 4 padrões de gabarito, disciplinas e recorte visual
    ├── consolidate_fgv.py        # Consolidação final: taxonomia (61 disciplinas), base_text, _index.json e metadados
    ├── export_db.py              # Exportador SQL deduplicado (N:N via question_hash) para PostgreSQL/Supabase e SQLite/Turso
    ├── ollama_helper.py          # Integração local Ollama (llama3.1:8b) com Guarda Estrita de Veracidade
    └── main.py                   # CLI unificada (argparse)
```

## Instalação

1. Instale as dependências e o navegador Chromium do Playwright:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

2. (Opcional) Para utilizar o fallback assistido por IA local, mantenha o **Ollama** em execução com o modelo `llama3.1:8b`:
   ```bash
   ollama pull llama3.1:8b
   ```

## Como Executar

- **Executar apenas o parser nos arquivos já baixados em `./data/raw`**:
  ```bash
  python -m src.main --only-parse
  ```

- **Executar o pipeline de consolidação, higienização taxonômica, propagação de `base_text` e geração de `_index.json`**:
  ```bash
  python -m src.consolidate_fgv
  ```

- **Exportar o banco deduplicado para PostgreSQL (Supabase / Neon) e SQLite (Turso / Cloudflare D1)**:
  ```bash
  python -m src.export_db
  ```

- **Executar o parser com fallback assistido pelo Ollama (`llama3.1:8b` + Veracity Guard)**:
  ```bash
  python -m src.main --only-parse --ollama-fallback
  ```

- **Coletar um número limitado de concursos e processar os PDFs**:
  ```bash
  python -m src.main --limit 5
  ```

- **Coletar e processar um concurso específico**:
  ```bash
  python -m src.main --url https://conhecimento.fgv.br/concursos/cnu2024
  ```
