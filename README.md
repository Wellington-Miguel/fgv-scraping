# FGV Conhecimento — Crawler & Parser de Provas Objetivas

Pipeline para coleta automatizada e parsing estruturado de provas objetivas (**Tipo 1 / Branca**) e gabaritos do portal [FGV Conhecimento](https://conhecimento.fgv.br/concursos).

## Estrutura do Projeto

```text
.
├── requirements.txt
├── README.md
└── src/
    ├── crawler_fgv.py   # Coleta resiliente com Playwright + pareamento por Cargo/Tipo 1
    ├── parser_fgv.py    # Parser 2 colunas (PyMuPDF), gabarito (pdfplumber) e validação Pydantic
    └── main.py          # CLI unificada (argparse)
```

## Instalação

1. Crie e ative um ambiente virtual (opcional, mas recomendado):
   ```bash
   python -m venv .venv
   # Windows (PowerShell):
   .\.venv\Scripts\Activate.ps1
   # Linux/macOS:
   source .venv/bin/activate
   ```

2. Instale as dependências e o navegador Chromium do Playwright:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

## Como Executar

- **Coletar um número limitado de concursos e processar os PDFs**:
  ```bash
  python -m src.main --limit 5
  ```

- **Coletar e processar um concurso específico**:
  ```bash
  python -m src.main --url https://conhecimento.fgv.br/concursos/cnu2024
  ```

- **Executar apenas o parser nos arquivos já baixados em `./data/raw`**:
  ```bash
  python -m src.main --only-parse
  ```

