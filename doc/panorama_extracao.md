# Panorama Geral e Relatório de Consolidação da Base — FGV Conhecimento

Este documento consolida o diagnóstico técnico, a auditoria de veracidade e o estado final consolidado do banco de provas objetivas (**Tipo 1 / Branca**, 4 ou 5 alternativas e **Certo/Errado**) e respectivos gabaritos oficiais coletados do portal [FGV Conhecimento](https://conhecimento.fgv.br/concursos).

A base em `./data/processed` encontra-se **100% consolidada, higienizada, deduplicada (`question_hash`), enriquecida com textos-base compartilhados (`base_text`) e metadados de aplicação** (`year`, `uf`, `level`, `question_type`, `module`, `discipline`), além de contar com o catálogo mestre `./data/processed/_index.json` para ingestão imediata no banco de dados do site/aplicativo.

---

## 1. Visão Macro do Banco Consolidado (`./data/processed`)

| Indicador / Camada | Baseline Inicial | Pós-Resolução dos 6 Gaps | **Base Final Consolidada (v2)** | Evolução Total |
| :--- | :--- | :--- | :--- | :--- |
| **Concursos coletados em `./data/raw`** | 260 concursos | 260 concursos | **260 concursos (231 com provas objetivas)** | — |
| **Pastas de Cargos (`metadata.json`) em `./data/raw`** | 1.554 pastas | 1.554 pastas | **1.554 pastas (1.499 objetivas + 55 discursivas/editais)** | 100% triado |
| **Gabaritos Oficiais em `./data/raw` (`gabarito.pdf`)** | 1.535 PDFs (98,8%) | 1.547 PDFs (100%) | **1.547 PDFs (100% das provas objetivas)** | **+12 gabaritos oficiais recuperados** |
| **Provas Objetivas processadas (`./data/processed/*.json`)** | 1.309 provas | 1.499 provas | **1.499 provas objetivas validadas + `_index.json`** | **+190 provas (+14,5%)** |
| **Provas por Formato (`question_type`)** | 1.309 (`A`–`E`) | 1.311 (`A`–`E`), 187 (`A`–`D`), 1 (`C`/`E`) | **1.311 (`multiple_choice_5`), 187 (`multiple_choice_4`), 1 (`certo_errado`)** | **+188 provas de 4 alt. e C/E** |
| **Total de Questões Objetivas Extraídas** | 80.031 questões | 101.079 questões | **101.769 questões objetivas** | **+21.738 questões reais (+27,2%)** |
| **Questões Únicas Deduplicadas (`question_hash`)** | Não existia | Não existia | **58.336 questões únicas** (+ 43.433 compartilhadas entre cargos) | **Deduplicação nativa p/ o App** |
| **Questões com Gabarito Oficial (`answer`)** | 76.999 (96,2%) | 101.079 (100,00%) | **101.769 (100,00% — `0` sem resposta)** | **+24.770 questões gabaritadas** |
| **Questões de 4 Alternativas com Gabarito `"E"` Inválido** | — | 24 questões (`dnit23`) | **0 questões (`100%` saneado)** | **100% de consistência A–D** |
| **Questões Anuladas Oficiais (`annulled: true`)** | 1.215 questões | 1.704 questões | **1.738 questões anuladas** | **+523 anulações mapeadas** |
| **Questões com Disciplina e Módulo (`discipline` + `module`)** | 0 (0,0%) | 472 rótulos brutos | **101.769 questões (100%) em 61 disciplinas canônicas** (apenas 2,65% genéricas) | **Taxonomia especializada p/ o App** |
| **Questões com Texto-Base Compartilhado (`base_text`)** | 0 | 0 | **749 questões dependentes enriquecidas com `base_text`** | **Autossuficiência em filtros do App** |
| **Provas com `cargo_title` higienizado** | 290 títulos truncados | 290 títulos truncados | **1.499 provas (`0` títulos quebrados/genéricos)** | **100% dos títulos padronizados** |
| **Imagens / Gráficos Recortados em 200 DPI (`images/`)** | 4.054 PNGs | 5.270 questões | **5.511 questões com imagem vinculada (`has_image: true`)** | **+1.684 imagens vinculadas** |
| **Provas com Sequência 100% Contígua (`0` lacunas)** | 565 de 1.309 (43,2%) | 1.276 de 1.499 (85,1%) | **1.415 de 1.499 provas (94,4%)** | **+850 provas 100% completas** |
| **Taxa de Retenção Real sobre o Total Teórico de Questões** | 91,6% (7.293 faltantes) | 99,3% (697 faltantes) | **99,90% (101.769 de 101.869 teóricas; apenas 100 ausentes)** | **Redução de 98,6% nas perdas internas** |

---

## 2. Esquema JSON Final Consolidado (Pronto para o Banco do Aplicativo)

Além do catálogo mestre `./data/processed/_index.json`, cada arquivo individual `./data/processed/<concurso_slug>_<cargo_slug>.json` segue rigorosamente o esquema abaixo:

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
  "total_questions": 70,
  "questions": [
    {
      "question_number": 1,
      "question_hash": "a1b2c3d4e5f60718",
      "discipline": "Língua Portuguesa",
      "module": "Conhecimentos Básicos",
      "base_text": null,
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
- **`_index.json` (Catálogo Mestre da Base):** Contém o sumário global da base, distribuição por módulos, anos, UFs, níveis de escolaridade, as 61 disciplinas canônicas e a lista indexada de todas as 1.499 provas com suas respectivas disciplinas e contagens.
- **`question_hash` (16 hex chars, SHA-1 determinístico):** Permite à aplicação deduplicar automaticamente as **43.433 aparições compartilhadas** (questões de Conhecimentos Básicos que caíram na mesma manhã para múltiplos cargos do mesmo concurso), resultando em **58.336 questões únicas** e mantendo o vínculo `N:N` com todas as 1.499 provas onde a questão foi cobrada.
- **`base_text`:** Em questões de interpretação de texto (`Língua Portuguesa`, `Língua Inglesa`, `Língua Espanhola`, `Redação Oficial`) cujo enunciado curto (<280 caracteres) referencia o texto-base impresso na questão anterior (*"No segundo parágrafo do texto..."*, *"De acordo com o Texto I..."*), o texto-base é propagado automaticamente para `base_text`, garantindo que a questão possa ser exibida isoladamente em filtros do aplicativo.
- **`question_type`:**
  - `"multiple_choice_5"`: 1.311 provas com alternativas `A` a `E`;
  - `"multiple_choice_4"`: 187 provas com alternativas `A` a `D` (`E: null`);
  - `"certo_errado"`: 1 prova oficial no formato Certo/Errado (`mre_tipo-a.json` — Oficial de Chancelaria MRE, `alternatives: C="Certo", E="Errado"`, `answer: "C" | "E" | "ANULADA"`).
- **`module`:** Classifica 100% das questões em `"Conhecimentos Básicos"` (**52.028 questões** — 51,1%) ou `"Conhecimentos Específicos"` (**49.741 questões** — 48,9%).
- **`level` (Escolaridade do Cargo):**
  - `"Superior"`: **1.260 provas** (84,1%)
  - `"Médio"`: **224 provas** (14,9%)
  - `"Fundamental"`: **15 provas** (1,0%)
- **`year` (Distribuição Temporal das 1.499 Provas):**
  - `2026`: 186 provas | `2025`: 168 provas | `2024`: 521 provas | `2023`: 324 provas | `2022`: 115 provas | `2021`: 42 provas | `2019–2020`: 6 provas | `2012–2016`: 137 provas.
- **`uf` (Distribuição Geográfica por Unidade Federativa):**
  - Nacional (`BR`): 261 | `SP`: 135 | `MT`: 116 | `MG`: 100 | `RJ`: 87 | `ES`: 87 | `TO`: 84 | `RO`: 83 | `PE`: 70 | `PA`: 66 | `BA`: 58 | `MS`: 50 | `AP`: 49 | `AM`: 47 | `SC`: 39 | `GO`: 30 | `SE`: 29 | `MA`: 26 | `PB`: 17 | `RS`: 12 | `RN`: 11 | `PI`: 9 | `RR`: 9 | `AC`: 8 | `PR`: 7 | `CE`: 6 | `DF`: 3.

---

## 3. Taxonomia Canônica de Disciplinas (61 Disciplinas Padronizadas e Especializadas)

Com o cruzamento entre cabeçalhos de caderno, especialidade do `cargo_title` e vocabulário técnico do enunciado (`specialize_generic_discipline`), o rótulo genérico `"Conhecimentos Específicos"` caiu de **19.066 questões (18,77%)** para apenas **2.701 questões (2,65%)**:

| # | Disciplina Canônica | Módulo Padrão | Total de Questões | % da Base |
| :-: | :--- | :--- | :---: | :---: |
| 1 | **Língua Portuguesa** | Conhecimentos Básicos | 28.552 | 28,06% |
| 2 | **Legislação Específica e Institucional** | Conhecimentos Específicos | 7.009 | 6,89% |
| 3 | **Medicina** | Conhecimentos Específicos | 5.140 | 5,05% |
| 4 | **Raciocínio Lógico-Matemático** | Conhecimentos Básicos | 4.867 | 4,78% |
| 5 | **Atualidades** | Conhecimentos Básicos | 4.152 | 4,08% |
| 6 | **Engenharia e Arquitetura** | Conhecimentos Específicos | 3.864 | 3,80% |
| 7 | **Conhecimentos Básicos / Gerais** | Conhecimentos Básicos | 3.732 | 3,67% |
| 8 | **Legislação do SUS e Saúde Pública** | Conhecimentos Específicos | 3.473 | 3,41% |
| 9 | **Noções de Direito** | Conhecimentos Específicos | 3.446 | 3,39% |
| 10 | **Informática** | Conhecimentos Básicos | 3.049 | 3,00% |
| 11 | **Conhecimentos Específicos** *(multidisciplinar residual)* | Conhecimentos Específicos | 2.701 | 2,65% |
| 12 | **Tecnologia da Informação** | Conhecimentos Específicos | 2.488 | 2,44% |
| 13 | **Pedagogia e Legislação Educacional** | Conhecimentos Específicos | 2.299 | 2,26% |
| 14 | **Língua Inglesa** | Conhecimentos Básicos | 2.110 | 2,07% |
| 15 | **Direito Constitucional** | Conhecimentos Específicos | 2.026 | 1,99% |
| 16 | **História e Geografia de Mato Grosso** | Conhecimentos Básicos | 1.916 | 1,88% |
| 17 | **Administração Geral e Pública** | Conhecimentos Específicos | 1.508 | 1,48% |
| 18 | **História e Geografia de Rondônia** | Conhecimentos Básicos | 1.490 | 1,46% |
| 19 | **Contabilidade Geral** | Conhecimentos Específicos | 1.269 | 1,25% |
| 20 | **Direito Administrativo** | Conhecimentos Específicos | 1.222 | 1,20% |
| 21 | **Direito da Criança e do Adolescente** | Conhecimentos Específicos | 879 | 0,86% |
| 22 | **Economia e Finanças** | Conhecimentos Específicos | 833 | 0,82% |
| 23 | **Direito Civil** | Conhecimentos Específicos | 739 | 0,73% |
| 24 | **Administração Financeira e Orçamentária (AFO)** | Conhecimentos Específicos | 713 | 0,70% |
| 25 | **Direito Eleitoral** | Conhecimentos Específicos | 612 | 0,60% |
| 26 | **Direito Processual Civil** | Conhecimentos Específicos | 609 | 0,60% |
| 27 | **Direitos Humanos, Ética e Cidadania** | Conhecimentos Básicos | 602 | 0,59% |
| 28 | **Direito Penal e Criminologia** | Conhecimentos Específicos | 582 | 0,57% |
| 29 | **Direito Processual Penal** | Conhecimentos Específicos | 565 | 0,56% |
| 30 | **Direito Tributário** | Conhecimentos Específicos | 548 | 0,54% |
| 31 | **Ciências da Saúde** *(Nutrição, Fisioterapia, Fonoaudiologia, Veterinária)* | Conhecimentos Específicos | 541 | 0,53% |
| 32 | **Direito Notarial e Registral** | Conhecimentos Específicos | 530 | 0,52% |
| 33 | **Estatística** | Conhecimentos Específicos | 522 | 0,51% |
| 34 | **Matemática** | Conhecimentos Básicos | 517 | 0,51% |
| 35 | **História e Geografia do Tocantins** | Conhecimentos Básicos | 492 | 0,48% |
| 36–61 | **Outras 26 Disciplinas Específicas** *(Biologia e Ciências: 475, Comunicação Social e Jornalismo: 454, Enfermagem: 422, Auditoria e Controle: 401, Matemática Financeira: 397, Psicologia: 373, Odontologia: 312, História: 309, Geografia: 303, Realidade do AM: 302, Realidade do AP: 297, Química: 288, Serviço Social: 251, Biblioteconomia e Arquivologia: 245, Farmácia: 244, Direito Ambiental: 219, Física: 209, Legislação Estadual: 124, Contabilidade Pública: 122, Direito Empresarial: 109, Direito do Consumidor: 107, Direito Internacional: 71, Língua Espanhola: 50, Direito do Trabalho: 44, Direito Previdenciário: 43, Direito Urbanístico: 1)* | Específicos / Básicos | 6.170 | 6,06% |

---

## 4. Histórico Completo das Melhorias de Engenharia e Veracidade

1. **Suporte Universal aos Formatos FGV (`A`–`E`, `A`–`D`, `Certo/Errado` e Módulos Específicos `Q41..70`):**
   - Extração completa das 1.311 provas de 5 alternativas, 187 provas de 4 alternativas e da prova oficial de Certo/Errado (`mre_tipo-a.json`).
2. **Triagem Automática de Cadernos Discursivos e Reconstrução de Páginas Fatiadas (`Tiled PDFs`):**
   - Isolamento das 55 pastas puramente discursivas/editais e reconstrução por glifos (`rawdict`) dos PDFs antigos fatiados em tiras verticais (`fbn12`, `alba`, `mps12`).
3. **Unificação dos 4 Padrões de Gabarito + Resgate de Gabaritos Faltantes / Wayback Machine (`100,00%` de cobertura):**
   - Suporte a grades horizontais, verticais, pares inline hifenizados de Diário Oficial e pares verticais alternados, com substituição de todos os gabaritos de Curso de Formação indevidos e restauração do gabarito `A–D` do 2º Curso de Formação em `dnit23`.
4. **Blindagem contra Falsas Alternativas no Meio de Frases (`ALT_SPLIT_RE`) e Suporte a Alternativas Visuais/Libras:**
   - Restrição do padrão sem parênteses (`A.`, `A)`, `A-`) exclusivamente ao início de linha, eliminando 100% dos cortes falsos em iniciais de autores (`GUERRA, A. T.`), pacientes (`Paciente A. C.`) ou expressões (`diferença A – B`).
   - Suporte a alternativas puramente visuais (sinais de Libras, ícones de software, símbolos de engenharia e frações empilhadas) com preenchimento `"[Imagem na questão]"` e recorte estendido até a base da questão (`+193 novos recortes PNG`, elevando as provas 100% contíguas para **1.415 de 1.499 — 94,4%** e a retenção global para **99,90%**).
5. **Propagação de Textos-Base Compartilhados (`base_text`) e Catálogo Mestre (`_index.json`):**
   - Enriquecimento de **749 questões dependentes** de interpretação com o texto-base da questão líder do bloco e geração do catálogo consolidado `data/processed/_index.json`.
