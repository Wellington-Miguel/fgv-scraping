# Panorama Geral da Extração — FGV Conhecimento

Este documento consolida o diagnóstico técnico completo da coleta (`crawler`) e do processamento (`parser`) das provas objetivas (**Tipo 1 / Branca**) e respectivos gabaritos do portal [FGV Conhecimento](https://conhecimento.fgv.br/concursos).

---

## 1. Resumo Executivo (O Que Conseguimos)

| Indicador / Camada | Resultado Obtido |
| :--- | :--- |
| **Concursos coletados em `./data/raw`** | **260 concursos** |
| **Pastas de Cargos (`metadata.json`) em `./data/raw`** | **1.554 cargos** |
| **Cadernos de Prova Tipo 1 baixados (`prova_tipo1.pdf`)** | **1.554 PDFs** |
| **Gabaritos baixados (`gabarito.pdf`)** | **1.535 PDFs** (98,8% de cobertura bruta) |
| **Provas Objetivas processadas em `./data/processed/*.json`** | **1.309 provas** (distribuídas em **220 concursos** únicos) |
| **Total de Questões Objetivas (A–E) extraídas e validadas** | **80.031 questões** |
| **Questões com Gabarito Tipo 1 pareado (`answer`)** | **76.999 questões (96,2% do total)** |
| **Questões Anuladas identificadas (`annulled: true`)** | **1.215 questões** |
| **Imagens / Gráficos recortados em 200 DPI (`./data/processed/images/`)** | **4.054 imagens PNG** (3.827 vinculadas às questões finais) |
| **Provas com 100% das questões gabaritadas** | **1.209 de 1.309 provas (92,4%)** |
| **Provas com gabarito parcial** | **42 de 1.309 provas (3,2%)** |
| **Provas sem gabarito extraído (`0%`)** | **58 de 1.309 provas (4,4%)** |

---

## 2. Arquitetura e Garantias de Qualidade Aplicadas

1. **Coleta Dinâmica Resiliente (`src/crawler_fgv.py`)**:
   - Navegação automatizada via **Playwright (Chromium Headless)** percorrendo todas as listagens do portal (`/concursos`, `/concursos/em-andamento` e `/concursos/encerrados`).
   - Desambiguação de **Cargo/Área** subindo na árvore DOM (`<tr>`, `<caption>`, `<li>`, `<h3>`–`<h5>`) e removendo cabeçalhos genéricos de layout do Drupal (*"Navegação principal"*, *"Arquivos do concurso"*).
   - **Filtros de Segurança**: Bloqueio de provas discursivas, redações, peças práticas, pareceres, sentenças cíveis/criminais, provas orais, provas de tribuna, avaliações de títulos, atas, editais e comunicados administrativos.
   - **Seleção Estrita do Tipo 1**: Exclusão de cadernos Tipo 2, 3, 4, 5 (ou cores Amarela, Verde, Azul, Rosa), baixando exclusivamente o **Tipo 1 (Branca)** ou **Tipo Único**.

2. **Parser de 2 Colunas, Gabarito e Recorte Visual (`src/parser_fgv.py`)**:
   - **Leitura em 2 Colunas (`PyMuPDF / fitz`)**: Extração via `page.get_text("blocks")`, remoção de cabeçalhos e rodapés repetitivos por margem vertical e frequência entre páginas, separação entre coluna esquerda (`x0 < largura * 0.48`) e direita, e ordenação vertical (`y0`).
   - **Validação Pydantic (`ExamParsedSchema`, `QuestionSchema`, `AlternativesSchema`)**: Garantia de enunciado válido, 5 alternativas obrigatórias (`A` a `E`), sincronização automática de questões anuladas (`ANULADA` / `annulled: true`) e verificação de integridade de caminho de imagem (`has_image` + `image_path`).
   - **Pareamento de Gabarito Tipo 1 (`pdfplumber`)**: Segmentação de páginas e tabelas por Cargo e **Tipo 1**, com compactação de células mescladas tanto em grades horizontais da FGV quanto em tabelas colunares multi-tipo.
   - **Recorte de Imagens e Vetores**: Inspeção de bitmaps (`page.get_images()`) e desenhos vetoriais (`page.get_drawings()`) entre a coordenada vertical inicial da questão (`q_y0`) e o início da alternativa `(A)` (`alt_a_y0`), exportando recortes em **200 DPI** para `./data/processed/images/{slug}_q{num}.png`.

---

## 3. O Que Deixamos de Conseguir (Gaps e Motivos Técnicos)

### A) 245 de 1.554 pastas em `./data/raw` não geraram JSON final (15,7%)

1. **PDFs Escaneados como Imagem (Sem Camada de Texto / OCR)**:
   - Em concursos mais antigos (ex: `fbn12`, `dpgems21`) ou cadernos digitalizados pela banca após aplicação física (ex: `dnit23`, `cgmcuiaba24`), o arquivo `prova_tipo1.pdf` contém apenas páginas escaneadas em imagem bitmap, sem camada de texto selecionável.
   - Como o pipeline utiliza extração nativa de blocos de texto (`page.get_text("blocks")`) sem OCR, esses cadernos não produzem questões textualizadas e são ignorados para evitar arquivos JSON vazios.

2. **Exames com Apenas 4 Alternativas (`A` a `D`)**:
   - Exames específicos, como o Exame de Suficiência do **CFC** (`cfc_cargo-geral`), utilizam apenas 4 alternativas (`A`, `B`, `C`, `D`), sem alternativa `E`.
   - Pelo requisito de validação estrita do schema Pydantic (`A` a `E`), questões de 4 alternativas são descartadas.

3. **Cadernos Residuais de 2ª Fase com Nomes Genéricos**:
   - Alguns PDFs publicados no portal apenas com rótulos genéricos de turno ou banca (ex: `"Tarde"`, `"Caderno de Prova - Banca I"`, `"Prova Escrita Geral"`) passaram pela triagem de URL, mas foram barrados pelo parser ao constatar a ausência de questões objetivas `A–E`.

---

### B) 58 provas sem gabarito (`0%`) e 42 provas com gabarito parcial (de 1.309 JSONs)

1. **Gabaritos Publicados como Imagem Escaneada**:
   - Em concursos como `cgmrj23`, `mpgo23`, `mprj2019` e `mps12`, o arquivo `gabarito.pdf` foi disponibilizado pela FGV como imagem digitalizada (sem texto ou tabela extraível pelo `pdfplumber`). As questões foram extraídas normalmente, com `answer: null`.

2. **Ausência de Gabarito na Página do Concurso**:
   - Em **19 cargos** (dos 1.554 coletados), a página oficial do concurso na FGV disponibilizou o caderno de prova, mas não publicou o link correspondente de gabarito.

3. **Gabaritos Divididos por Módulo ou Etapa**:
   - Em casos pontuais como `cgepb23` (`20/100`), `fempar24` (`20/80`) e `prefsjc-administrativo23` (`56/66`), o concurso publicou gabaritos separados por módulo (ex: *Curso de Formação / Módulo Básico* vs. *Conhecimentos Específicos*) ou teve renumeração parcial entre cadernos.

---

## 4. Auditoria de Sincronização no GitHub

Todos os arquivos gerados na pasta local foram verificados contra a branch remota `origin/main` (`https://github.com/Wellington-Miguel/fgv-scraping.git`):

- **Arquivos de Prova Brutos (`data/raw/*/*/prova_tipo1.pdf`)**: `1.554` locais vs. `1.554` no remoto (**100% sincronizado**)
- **Arquivos de Gabarito Brutos (`data/raw/*/*/gabarito.pdf`)**: `1.535` locais vs. `1.535` no remoto (**100% sincronizado**)
- **Metadados (`data/raw/*/*/metadata.json`)**: `1.554` locais vs. `1.554` no remoto (**100% sincronizado**)
- **Provas Processadas (`data/processed/*.json`)**: `1.309` locais vs. `1.309` no remoto (**100% sincronizado**)
- **Imagens Recortadas (`data/processed/images/*.png`)**: `4.054` locais vs. `4.054` no remoto (**100% sincronizado**)

