# Panorama Geral e Relatório de Consolidação da Base — FGV Conhecimento

Este documento consolida o diagnóstico técnico, a auditoria de veracidade e o estado final consolidado do banco de provas objetivas (**Tipo 1 / Branca**, 4 ou 5 alternativas e **Certo/Errado**) e respectivos gabaritos oficiais coletados do portal [FGV Conhecimento](https://conhecimento.fgv.br/concursos).

A base em `./data/processed` encontra-se **100% consolidada, higienizada, deduplicada (`question_hash`) e enriquecida com metadados de aplicação** (`year`, `uf`, `level`, `question_type`, `module`, `discipline`), pronta para ingestão direta no banco de dados do site/aplicativo de questões.

---

## 1. Visão Macro do Banco Consolidado (`./data/processed`)

| Indicador / Camada | Baseline Inicial | Pós-Resolução dos 6 Gaps | **Base Final Consolidada** | Evolução Total |
| :--- | :--- | :--- | :--- | :--- |
| **Concursos coletados em `./data/raw`** | 260 concursos | 260 concursos | **260 concursos (231 com provas objetivas)** | — |
| **Pastas de Cargos (`metadata.json`) em `./data/raw`** | 1.554 pastas | 1.554 pastas | **1.554 pastas (1.499 objetivas + 55 discursivas/editais)** | 100% triado |
| **Gabaritos Oficiais em `./data/raw` (`gabarito.pdf`)** | 1.535 PDFs (98,8%) | 1.547 PDFs (100%) | **1.547 PDFs (100% das provas objetivas)** | **+12 gabaritos oficiais recuperados** |
| **Provas Objetivas processadas (`./data/processed/*.json`)** | 1.309 provas | 1.499 provas | **1.499 provas objetivas validadas** | **+190 provas (+14,5%)** |
| **Provas por Formato (`question_type`)** | 1.309 (`A`–`E`) | 1.311 (`A`–`E`), 187 (`A`–`D`), 1 (`C`/`E`) | **1.311 (`multiple_choice_5`), 187 (`multiple_choice_4`), 1 (`certo_errado`)** | **+188 provas de 4 alt. e C/E** |
| **Total de Questões Objetivas Extraídas** | 80.031 questões | 101.079 questões | **101.602 questões objetivas** | **+21.571 questões reais (+27,0%)** |
| **Questões Únicas Deduplicadas (`question_hash`)** | Não existia | Não existia | **58.157 questões únicas** (+ 43.445 compartilhadas entre cargos) | **Deduplicação nativa p/ o App** |
| **Questões com Gabarito Oficial (`answer`)** | 76.999 (96,2%) | 101.079 (100,00%) | **101.602 (100,00% — `0` sem resposta)** | **+24.603 questões gabaritadas** |
| **Questões de 4 Alternativas com Gabarito `"E"` Inválido** | — | 24 questões (`dnit23`) | **0 questões (`100%` saneado)** | **100% de consistência A–D** |
| **Questões Anuladas Oficiais (`annulled: true`)** | 1.215 questões | 1.704 questões | **1.736 questões anuladas** | **+521 anulações mapeadas** |
| **Questões com Disciplina e Módulo (`discipline` + `module`)** | 0 (0,0%) | 472 rótulos brutos | **101.602 questões (100%) em 57 disciplinas canônicas** | **Taxonomia limpa p/ filtros do App** |
| **Provas com `cargo_title` higienizado** | 290 títulos truncados | 290 títulos truncados | **1.499 provas (`0` títulos quebrados/genéricos)** | **100% dos títulos padronizados** |
| **Imagens / Gráficos Recortados em 200 DPI (`images/`)** | 4.054 PNGs | 5.313 PNGs | **5.318 questões com imagem vinculada (`has_image: true`)** | **+1.264 imagens vinculadas** |
| **Provas com Sequência 100% Contígua (`0` lacunas)** | 565 de 1.309 (43,2%) | 1.276 de 1.499 (85,1%) | **1.314 de 1.499 provas (87,7%)** | **+749 provas 100% completas** |
| **Taxa de Retenção Real sobre o Total Teórico de Questões** | 91,6% (7.293 faltantes) | 99,4% (605 faltantes) | **99,75% (101.602 de 101.861 teóricas; apenas 259 ausentes)** | **Redução de 96,4% nas perdas internas** |

---

## 2. Esquema JSON Final Consolidado (Pronto para o Banco do Aplicativo)

Cada arquivo em `./data/processed/<concurso_slug>_<cargo_slug>.json` segue rigorosamente o esquema abaixo:

```json
{
  "concurso_slug": "cnu2024",
  "concurso_title": "Concurso Público Nacional Unificado - CNU 2024",
  "cargo_slug": "bloco-1-infraestrutura-exatas-e-engenharias",
  "cargo_title": "Bloco 1 - Infraestrutura, Exatas e Engenharias",
  "exam_type": "Tipo 1",
  "question_type": "multiple_choice_5",
  "year": 2024,
  "uf": "BR",
  "level": "Superior",
  "source_pdf": "data/raw/cnu2024/bloco-1-infraestrutura-exatas-e-engenharias/prova_tipo1.pdf",
  "gabarito_pdf": "data/raw/cnu2024/bloco-1-infraestrutura-exatas-e-engenharias/gabarito.pdf",
  "total_questions": 70,
  "questions": [
    {
      "question_number": 1,
      "question_hash": "a1b2c3d4e5f60718",
      "discipline": "Língua Portuguesa",
      "module": "Conhecimentos Básicos",
      "statement": "Texto literal do enunciado...",
      "alternatives": {
        "A": "Texto da alternativa A...",
        "B": "Texto da alternativa B...",
        "C": "Texto da alternativa C...",
        "D": "Texto da alternativa D...",
        "E": "Texto da alternativa E..."
      },
      "answer": "C",
      "annulled": false,
      "has_image": false,
      "image_path": null,
      "page_number": 2
    }
  ]
}
```

### Detalhamento dos Metadados Adicionados para a Aplicação:
- **`question_hash` (16 hex chars, SHA-1 determinístico):** Permite à aplicação deduplicar automaticamente as **43.445 aparições compartilhadas** (questões de Conhecimentos Básicos que caíram na mesma manhã para múltiplos cargos do mesmo concurso), resultando em **58.157 questões únicas** e mantendo o vínculo `N:N` com todas as 1.499 provas onde a questão foi cobrada.
- **`question_type`:**
  - `"multiple_choice_5"`: 1.311 provas com alternativas `A` a `E`;
  - `"multiple_choice_4"`: 187 provas com alternativas `A` a `D` (`E: null`);
  - `"certo_errado"`: 1 prova oficial no formato Certo/Errado (`mre_tipo-a.json` — Oficial de Chancelaria MRE, `alternatives: A..E = null`, `answer: "C" | "E" | "ANULADA"`).
- **`module`:** Classifica 100% das questões em `"Conhecimentos Básicos"` (**53.138 questões** — 52,3%) ou `"Conhecimentos Específicos"` (**48.464 questões** — 47,7%).
- **`level` (Escolaridade do Cargo):**
  - `"Superior"`: **1.260 provas** (84,1%)
  - `"Médio"`: **224 provas** (14,9%)
  - `"Fundamental"`: **15 provas** (1,0%)
- **`year` (Distribuição Temporal das 1.499 Provas):**
  - `2026`: 186 provas | `2025`: 168 provas | `2024`: 521 provas | `2023`: 324 provas | `2022`: 115 provas | `2021`: 42 provas | `2019–2020`: 6 provas | `2012–2016`: 137 provas.
- **`uf` (Distribuição Geográfica por Unidade Federativa):**
  - Nacional (`BR`): 261 | `SP`: 135 | `MT`: 116 | `MG`: 100 | `RJ`: 87 | `ES`: 87 | `TO`: 84 | `RO`: 83 | `PE`: 70 | `PA`: 66 | `BA`: 58 | `MS`: 50 | `AP`: 49 | `AM`: 47 | `SC`: 39 | `GO`: 30 | `SE`: 29 | `MA`: 26 | `PB`: 17 | `RS`: 12 | `RN`: 11 | `PI`: 9 | `RR`: 9 | `AC`: 8 | `PR`: 7 | `CE`: 6 | `DF`: 3.

---

## 3. Taxonomia Canônica de Disciplinas (57 Disciplinas Padronizadas)

Os 472 rótulos brutos capturados nos cabeçalhos ou inferidos pelo LLM foram higienizados e consolidados em **57 disciplinas canônicas** (sem fragmentos de frases, URLs ou rodapés):

| # | Disciplina Canônica | Módulo Padrão | Total de Questões | % da Base |
| :-: | :--- | :--- | :---: | :---: |
| 1 | **Língua Portuguesa** | Conhecimentos Básicos | 28.699 | 28,25% |
| 2 | **Conhecimentos Específicos** *(multidisciplinar do cargo)* | Conhecimentos Específicos | 19.066 | 18,77% |
| 3 | **Legislação Específica e Institucional** | Conhecimentos Específicos | 7.262 | 7,15% |
| 4 | **Raciocínio Lógico-Matemático** | Conhecimentos Básicos | 4.821 | 4,75% |
| 5 | **Atualidades** | Conhecimentos Básicos | 4.199 | 4,13% |
| 6 | **Conhecimentos Básicos / Gerais** | Conhecimentos Básicos | 4.140 | 4,07% |
| 7 | **Legislação do SUS e Saúde Pública** | Conhecimentos Específicos | 3.426 | 3,37% |
| 8 | **Noções de Direito** | Conhecimentos Específicos | 2.939 | 2,89% |
| 9 | **Língua Inglesa** | Conhecimentos Básicos | 2.935 | 2,89% |
| 10 | **Informática** | Conhecimentos Básicos | 2.762 | 2,72% |
| 11 | **História e Geografia de Mato Grosso** | Conhecimentos Básicos | 1.916 | 1,89% |
| 12 | **Direito Constitucional** | Conhecimentos Específicos | 1.661 | 1,63% |
| 13 | **Pedagogia e Legislação Educacional** | Conhecimentos Específicos | 1.561 | 1,54% |
| 14 | **História e Geografia de Rondônia** | Conhecimentos Básicos | 1.486 | 1,46% |
| 15 | **Medicina** | Conhecimentos Específicos | 1.060 | 1,04% |
| 16 | **Direito Administrativo** | Conhecimentos Específicos | 963 | 0,95% |
| 17 | **Administração Geral e Pública** | Conhecimentos Específicos | 892 | 0,88% |
| 18 | **Direito da Criança e do Adolescente** | Conhecimentos Específicos | 769 | 0,76% |
| 19 | **Direito Civil** | Conhecimentos Específicos | 690 | 0,68% |
| 20 | **Direito Eleitoral** | Conhecimentos Específicos | 612 | 0,60% |
| 21 | **Administração Financeira e Orçamentária (AFO)** | Conhecimentos Específicos | 609 | 0,60% |
| 22 | **Direito Notarial e Registral** | Conhecimentos Específicos | 600 | 0,59% |
| 23 | **Engenharia e Arquitetura** | Conhecimentos Específicos | 579 | 0,57% |
| 24 | **Direitos Humanos, Ética e Cidadania** | Conhecimentos Básicos | 569 | 0,56% |
| 25 | **Direito Processual Civil** | Conhecimentos Específicos | 536 | 0,53% |
| 26 | **Direito Processual Penal** | Conhecimentos Específicos | 526 | 0,52% |
| 27 | **Direito Penal e Criminologia** | Conhecimentos Específicos | 523 | 0,51% |
| 28 | **História e Geografia do Tocantins** | Conhecimentos Básicos | 492 | 0,48% |
| 29 | **Matemática** | Conhecimentos Básicos | 471 | 0,46% |
| 30 | **Contabilidade Geral** | Conhecimentos Específicos | 404 | 0,40% |
| 31–57 | **Outras 27 Disciplinas Específicas** *(Matemática Financeira, Direito Tributário, TI, Estatística, Biologia, Auditoria, Direito Ambiental, Química, Geografia, Economia, Contabilidade Pública, Física, Direito Empresarial, Farmácia, Direito Internacional, Enfermagem, Serviço Social, Direito do Consumidor, Odontologia, Língua Espanhola, Direito Previdenciário, Psicologia, Direito do Trabalho, Direito Urbanístico, Realidade do AM/AP)* | Específicos / Básicos | 4.828 | 4,75% |

---

## 4. Resumo das 11 Melhorias de Engenharia e Veracidade (6 Gaps + 5 Correções Finais)

### Fase 1: Resolução dos 6 Gaps Estruturais de Extração
1. **Gap 1 — Provas de 4 Alternativas (`A`–`D`), Certo/Errado (`C`/`E`) e Cadernos de Módulo Específico (`Q41..70`):**
   - `AlternativesSchema` e `_finalize_question_dict` em [src/parser_fgv.py](../src/parser_fgv.py) passaram a aceitar 4 alternativas (`A`–`D` com `E: null`), provas de julgamento de itens Certo/Errado (`mre_tipo-a.json`, com 108 itens oficiais) e cadernos de reaplicação iniciados em fronteiras de módulo (`Q41..70`).
2. **Gap 2 — Triagem de Cadernos Discursivos e Reconstrução de Páginas Fatiadas (`Tiled PDFs`):**
   - `_is_discursive_only_pdf` isola as **55 pastas que são exclusivamente discursivas/peças práticas/editais**; `_reconstruct_tiled_page_blocks` reconstrói por coordenadas de glifos (`rawdict`) as provas antigas (2012–2014: `fbn12`, `alba`, `mps12`) cujos PDFs eram fatiados em tiras verticais de ~60pt.
3. **Gap 3 — Unificação dos 4 Padrões de Gabarito da FGV:**
   - Suporte unificado em `parse_gabarito_pdf` para grades horizontais, grades verticais, pares inline hifenizados de Diário Oficial (`1 – D`, `6-*`) e pares verticais alternados (`1\nB\n2\nC`), além de asteriscos de nota de rodapé (`16**`) e anulações entre colchetes (`[*]`).
4. **Gap 4 — Fim dos Gabaritos Parciais e Correção de Pareamento com "Curso de Formação":**
   - Substituição de todos os `gabarito.pdf` de Curso de Formação pelos gabaritos definitivos da Prova Objetiva (`cgepb23`, `tcm-sp`, `seadse25`, `seplag`, `seplagcgmnit24`, `prefsjc-administrativo23`, `seadap22`, `smacanaacarajas24`) e resolução automática do cargo real na capa quando `metadata.json` traz `"Cargo Geral"`.
5. **Gap 5 — Isolamento da Capa e Desmembramento de Sub-blocos:**
   - `_is_cover_page` impede que as instruções `1.` a `15.` da capa sejam tratadas como questões; `_split_merged_block_lines` separa enunciados e alternativas mesclados no mesmo bloco PyMuPDF e reconhece códigos internos da FGV (`(NMCB01_09)`).
6. **Gap 6 — Resgate dos 12 Gabaritos Faltantes (FGV + Wayback Machine) e Integração Ollama (`llama3.1:8b`):**
   - Priorização de `"gabarito"` sobre `ADMIN_KEYWORDS` e fallback automático via Internet Archive (`web.archive.org`) para links legados `netstorage.fgv.br`.
   - Módulo [src/ollama_helper.py](../src/ollama_helper.py) com guarda estrita de veracidade (`verify_literal_veracity`) e classificação em lote de disciplinas via `llama3.1:8b`.

### Fase 2: As 5 Correções Finais de Consolidação e Higienização ([src/consolidate_fgv.py](../src/consolidate_fgv.py))
7. **Correção 1 — Restauração do Gabarito Oficial `A–D` do 2º Curso de Formação em `dnit23`:**
   - Os 4 cadernos de 40 questões (`A–D`) do 2º Curso de Formação do `dnit23` foram pareados ao gabarito definitivo oficial do CF (`cf-dnit-gabaritos-para-publicacao_definitivo.pdf`), enquanto `dnit23/cargo-geral` (80 questões `A–E`) manteve o gabarito da prova objetiva principal. Resultado: **0 questões de 4 alternativas com resposta `"E"`**.
8. **Correção 2 — Normalização Taxonômica de Disciplinas e Módulo (`normalize_discipline_and_module`):**
   - Remoção de 100% dos falsos cabeçalhos capturados de início de página (como `'de Mato Grosso'`, `'fiscalização de contratos'`, `'As questões da prova de Língua Portuguesa referem-se ao texto'`, URLs e citações bibliográficas), consolidando as 101.602 questões em **57 disciplinas canônicas** e adicionando o campo `module`.
9. **Correção 3 — Higienização de 100% dos Títulos de Cargo (`clean_cargo_title`):**
   - Limpeza de prefixos de menu Drupal (`"Navegação principal - "`, `"Arquivos do concurso - "`) e reconstrução dos títulos truncados por cabeçalhos estaduais (`'DO MUNICÍPIO DO RIO - DE JANEIRO'` → `'Contador'`, `'DO ESTADO DE - GOIÁS'` → `'Juiz Substituto'`, etc.).
10. **Correção 4 — Correção do Filtro de Linhas Repetidas (`_detect_repeated_header_footer_lines`) e Questões de Correlação:**
    - O filtro `_detect_repeated_header_footer_lines` em [src/parser_fgv.py](../src/parser_fgv.py) foi restrito estritamente às margens superior/inferior (`y < 12%` ou `y > 88%` da página) e proibido de remover linhas iniciadas por `(A)`..`(E)`. Isso evitou que alternativas curtas frequentes como `"(A) I, apenas."` fossem apagadas em provas com muitas questões de algarismos romanos, recuperando **+523 questões completas** e elevando a retenção da base para **99,75%** (`101.602 / 101.861`).
11. **Correção 5 — Enriquecimento de Metadados e Hash de Deduplicação (`question_hash`):**
    - Adição dos campos `year`, `uf`, `level`, `question_type`, `module` e `question_hash` em todos os 1.499 arquivos JSON.
