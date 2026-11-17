# Panorama Geral da Extração e Auditoria de Veracidade — FGV Conhecimento

Este documento consolida o diagnóstico técnico completo da coleta (`crawler`), do processamento de alta veracidade (`parser`) e da camada assistiva local com **Ollama (`llama3.1:8b`)** das provas objetivas (**Tipo 1 / Branca**) e respectivos gabaritos oficiais do portal [FGV Conhecimento](https://conhecimento.fgv.br/concursos).

---

## 1. Resumo Executivo (Evolução Antes vs. Depois da Resolução dos 6 Gaps)

| Indicador / Camada | Antes (Baseline) | Depois (Pós-Correção dos 6 Gaps) | Evolução |
| :--- | :--- | :--- | :--- |
| **Concursos coletados em `./data/raw`** | 260 concursos | **260 concursos** | — |
| **Pastas de Cargos (`metadata.json`) em `./data/raw`** | 1.554 cargos | **1.554 cargos** | — |
| **Gabaritos Oficiais em `./data/raw` (`gabarito.pdf`)** | 1.535 PDFs (98,8%) | **1.547 PDFs (100% das provas objetivas)** | **+12 gabaritos oficiais recuperados** |
| **Provas Objetivas processadas (`./data/processed/*.json`)** | 1.309 provas (220 concursos) | **1.498 provas (231 concursos)** | **+189 provas objetivas (+14,4%)** |
| **Provas de 4 Alternativas (`A`–`D`) recuperadas** | 0 provas | **187 provas** (`cfc`, `dnit23`, `fbn12`, `dpgems21`) | **+187 provas** |
| **Total de Questões Objetivas extraídas e validadas** | 80.031 questões | **99.522 questões** | **+19.491 questões reais (+24,4%)** |
| **Questões com Gabarito Oficial pareado (`answer`)** | 76.999 (96,2%) | **99.522 (100,00%)** | **+22.523 questões gabaritadas** |
| **Questões com Disciplina extraída (`discipline`)** | 0 (0,0%) | **80.496 questões (80,9%) em 1.344 provas (89,7%)** | **+80.496 questões classificadas** |
| **Questões Anuladas identificadas (`annulled: true`)** | 1.215 questões | **1.683 questões** | **+468 anulações mapeadas** |
| **Imagens / Gráficos recortados em 200 DPI (`images/`)** | 4.054 PNGs (3.827 vinculados) | **5.286 PNGs (5.115 vinculados às questões)** | **+1.232 imagens recortadas** |
| **Provas com 100% das questões gabaritadas** | 1.209 de 1.309 (92,4%) | **1.498 de 1.498 (100,00%)** | **100% de cobertura oficial** |
| **Provas com gabarito parcial** | 42 provas (3,2%) | **0 provas (0,0%)** | **100% resolvido** |
| **Provas sem gabarito extraído (`0%`)** | 58 provas (4,4%) | **0 provas (0,0%)** | **100% resolvido** |
| **Buracos internos na sequência (`sequence gaps`)** | 7.293 questões em 744 provas | **697 questões em 213 provas (99,3% de retenção)** | **Redução de 90,4% nas perdas internas** |

---

## 2. Solução Técnica Aplicada aos 6 Gaps (Foco em Veracidade 100%)

### Gap 1: Provas com 4 Alternativas (`A`–`D`) e Cadernos de Módulo Específico (`Q41..70`)
- **Diagnóstico Real:** Das 245 pastas que não geravam JSON, **187 eram provas objetivas de 4 alternativas (`A` a `D`)** (como `cfc`, `dnit23`, `dpgems21` e `fbn12`) e **2 eram cadernos de reaplicação de Conhecimentos Específicos** (`navbrasil26_pedagogo` e `navbrasil26_psicologo`) cuja numeração iniciava na questão `41`.
- **Solução Implementada ([src/parser_fgv.py](../src/parser_fgv.py)):**
  - `AlternativesSchema` atualizado com `E: Optional[str] = None`.
  - `_finalize_question_dict` valida obrigatoriamente `A`, `B`, `C`, `D` e captura `E` quando presente.
  - `_segment_questions` suporta início de caderno nas fronteiras padrão de módulo da FGV (`1, 11, 16, 21, 26, 31, 36, 41, 46, 51, 61`).

### Gap 2: Triagem de Cadernos Discursivos pelo Conteúdo e Reconstrução de Páginas Fatiadas
- **Diagnóstico Real:** Nenhuma das 1.554 provas em `./data/raw` era imagem sem texto; porém, **55 pastas eram provas discursivas/peças práticas/editais** cujo link na FGV omitia a palavra "Discursiva", e provas de 2012–2014 (`fbn12`, `alba`, `mps12`) tinham o fluxo PDF fatiado em tiras verticais de ~60pt com duplicação de caracteres nas bordas (`'Ass' + 'sinale'`).
- **Solução Implementada ([src/parser_fgv.py](../src/parser_fgv.py)):**
  - `_is_discursive_only_pdf` inspeciona as 4 primeiras páginas para barrar cadernos exclusivamente discursivos sem jamais bloquear provas objetivas que mencionem 2ª fase na capa.
  - `_reconstruct_tiled_page_blocks` reconstrói as linhas horizontais via coordenadas de glifos (`rawdict`) e deduplica caracteres sobrepostos nas fronteiras de tira.

### Gap 3: Unificação dos 4 Padrões de Gabarito da FGV (`0` Provas Sem Gabarito)
- **Diagnóstico Real:** As 46 provas que tinham `gabarito.pdf` mas saíam com `0%` de respostas (`mps12` com 26 cargos, `pmesqos2022` com 19 cargos em Diário Oficial e `pmespsoldado25`) usavam pares inline hifenizados (`1 – D`, `1-B - 2-E - 6-*`).
- **Solução Implementada ([src/parser_fgv.py](../src/parser_fgv.py)):**
  - `parse_gabarito_pdf` e `_extract_sectioned_grids_from_text` unificam os 4 formatos FGV: grades horizontais, grades verticais, pares inline (`INLINE_PAIR_RE`) e pares verticais alternados (`1\nB\n2\nC`), além de suportar números com asteriscos de nota de rodapé (`16**` em `fempar24`) e anulações entre colchetes (`[*]`).

### Gap 4: Fim dos Gabaritos Parciais e Correção de Pareamento Indevido com "Curso de Formação"
- **Diagnóstico Real de Veracidade:** Em concursos como `cgepb23`, `tcm-sp`, `dnit23`, `seadse25`, `seplag`, `seplagcgmnit24`, `prefsjc-administrativo23`, `seadap22` e `smacanaacarajas24`, o crawler havia pareado o gabarito do *Curso de Formação* (20 ou 40 questões) ou de um único cargo/dia ao caderno da Prova Objetiva (60 a 100 questões). Além disso, pastas rotuladas como `"Cargo Geral"` perdiam a especificidade ao buscar a tabela dentro de gabaritos multi-cargo (`tjrs25`, `cgmrj23`, `pmn20`, `stn`, `sefmg22`).
- **Solução Implementada ([src/crawler_fgv.py](../src/crawler_fgv.py) e [src/parser_fgv.py](../src/parser_fgv.py)):**
  - Substituição de todos os `gabarito.pdf` de Curso de Formação pelos gabaritos definitivos oficiais da Prova Objetiva e unificação dos 4 dias de aplicação de `smacanaacarajas24`.
  - `_resolve_real_cargo_title` lê a capa (Página 1) e o rodapé da Página 3 da prova para descobrir o cargo verdadeiro quando `metadata.json` traz `"Cargo Geral"`.
  - Pontuação de compatibilidade em `parse_gabarito_pdf` prioriza casamento exato da questão máxima (`max(ans_map) == max(expected_q_nums)`) e faz *merge* automático de seções complementares (Conhecimentos Básicos + Específicos).

### Gap 5: Isolamento da Capa e Desmembramento de Sub-blocos (-90,4% em Buracos de Sequência)
- **Diagnóstico Real:** Em 200 provas (como toda a família `alba_*`), os itens numerados `1.` a `15.` das instruções da Capa (Página 1) eram confundidos com questões, fazendo o parser engolir as questões reais `01` a `15` dentro da alternativa `E` da questão 15 (gerando 2.126 alternativas contaminadas). Além disso, `y1 > FOOTER_RATIO` descartava questões inteiras no pé da coluna (ex: Q5 e Q25 em `alba_auditor`), e quebras de linha começando com números por extenso (ex: `"65 (sessenta e cinco) anos"` em `mpgo23`) causavam saltos falsos de numeração.
- **Solução Implementada ([src/parser_fgv.py](../src/parser_fgv.py)):**
  - Isolamento estrito da capa (`_is_cover_page`), desmembramento de blocos mesclados (`_split_merged_block_lines`), corte de rodapé apenas por `y0 > FOOTER_TOP_CUTOFF`, trava de completude (`_has_complete_alternatives_so_far`) e extração automática da disciplina (`discipline`) limpando o final da última alternativa.

### Gap 6: Resgate dos 12 Gabaritos Faltantes e Camada Assistiva Auditável com Ollama (`llama3.1:8b`)
- **Diagnóstico Real:** Dos 19 cargos sem `gabarito.pdf`, 7 eram provas discursivas (sem gabarito objetivo), 6 estavam publicados na própria página da FGV mas foram barrados por `ADMIN_KEYWORDS` (`"edital"`, `"comunicado"`, `"resultado"` no nome do arquivo: `cgmrj23`, `mpgo23`, `tjmsjuiz23`, `tjmsjuiz25`, `tjpr25`, `tjrs25`), e 6 apontavam para o servidor legado `netstorage.fgv.br` (`dpgerj2021`, `mprj2019` e os 4 cargos de `seplag`), todos preservados no Internet Archive (`web.archive.org`).
- **Solução Implementada ([src/crawler_fgv.py](../src/crawler_fgv.py) e [src/ollama_helper.py](../src/ollama_helper.py)):**
  - Todos os 12 gabaritos oficiais foram baixados, verificados e processados com 100% de aproveitamento.
  - Criado o módulo [src/ollama_helper.py](../src/ollama_helper.py) conectando ao modelo local **`llama3.1:8b`** via Ollama (`temperature=0.0`, `format="json"`) com **Guarda de Veracidade (`verify_literal_veracity`)** para fallback de segmentação de blocos atípicos, extração assistida de gabaritos, classificação de disciplinas e resolução em quarentena (`answer_source = "ai_quarantine"`).
