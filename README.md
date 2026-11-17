# FGV Conhecimento — Crawler & Parser de Provas Objetivas (Alta Veracidade)

Pipeline completo para coleta automatizada, auditoria de veracidade e parsing estruturado de provas objetivas (**Tipo 1 / Branca**, 4 ou 5 alternativas) e gabaritos oficiais do portal [FGV Conhecimento](https://conhecimento.fgv.br/concursos), com suporte a fallback assistido via **Ollama (`llama3.1:8b`)**.

## Resultados Atuais da Base (`./data/processed`)

- **1.498 provas objetivas processadas** (em **231 concursos** únicos)
- **99.522 questões objetivas extraídas e validadas** (Pydantic)
- **99.522 questões gabaritadas (100,00% de cobertura oficial)** — `0` provas sem gabarito e `0` provas com gabarito parcial
- **80.496 questões classificadas por disciplina** (`discipline`) diretamente dos cadernos
- **5.286 imagens/gráficos recortados** em 200 DPI (`./data/processed/images/`)

## Estrutura do Projeto

```text
.
├── requirements.txt
├── README.md
├── doc/
│   └── panorama_extracao.md  # Relatório técnico e auditoria Antes vs. Depois dos 6 Gaps
└── src/
    ├── crawler_fgv.py        # Coleta resiliente (Playwright) + pareamento Cargo/Tipo 1 + fallback Wayback
    ├── parser_fgv.py         # Parser 2 colunas (PyMuPDF), 4 padrões de gabarito, disciplinas e recorte visual
    ├── ollama_helper.py      # Integração local Ollama (llama3.1:8b) com Guarda Estrita de Veracidade
    └── main.py               # CLI unificada (argparse)
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
