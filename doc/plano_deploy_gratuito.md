# Plano de Arquitetura e Deploy 100% Gratuito (Banco de Dados + Imagens + App)

Este documento define o plano técnico completo para hospedar o **Banco de Questões (`101.769` aparições / `58.336` questões únicas)**, as **`4.054+` imagens recortadas** e o **Aplicativo Web/Mobile de Estudo** com **custo zero (`R$ 0,00 / mês`)**, utilizando os melhores planos gratuitos (Free Tiers) de infraestrutura moderna e escalando facilmente para outras bancas no futuro.

---

## 1. Visão Geral da Stack 100% Gratuita (`$0/mês`)

```mermaid
flowchart LR
    subgraph GitHub["GitHub (Open Source Datasets)"]
        FGV["Repo: fgv-scraping\n(JSONs + PNGs)"]
        CEB["Repo: cebraspe-scraping\n(Futuro)"]
    end

    subgraph CDN["CDN Global Gratuita"]
        JSD["jsDelivr CDN\ncdn.jsdelivr.net/gh/..."]
    end

    subgraph DB["Banco de Dados + Auth (Free Tier)"]
        SUPA["Supabase (PostgreSQL 500 MB + Auth)\nou Turso (SQLite Edge 5 GB)"]
    end

    subgraph App["Hospedagem do App (Free Tier)"]
        VERCEL["Vercel / Cloudflare Pages\n(Next.js / React + API Serverless)"]
    end

    FGV -->|"Ingestão (src/export_db.py)"| SUPA
    CEB -.->|"Mesmo Schema Canônico"| SUPA
    FGV -->|"Serve PNGs sem custo de bucket"| JSD
    SUPA <-->|"Queries + Progresso do Aluno"| VERCEL
    JSD -->|"Renderiza image_url nas questões"| VERCEL
```

### Por que essa combinação não custa nada e não estoura limites?

| Camada | Serviço Recomendado | Limite do Plano Gratuito | Consumo Real da Base FGV | Folga no Free Tier |
| :--- | :--- | :--- | :--- | :--- |
| **Banco de Dados + Auth** | **Supabase** *(PostgreSQL)* ou **Turso** *(SQLite Edge)* | **500 MB** no Supabase (`5 GB` no Turso) + **50.000 usuários ativos/mês** no Supabase Auth | **~45 MB** *(usando a modelagem deduplicada por `question_hash`)* | **91% livre no Supabase** (cabe FGV + Cebraspe + FCC + Vunesp juntos!) |
| **Imagens das Questões (`PNGs`)** | **jsDelivr CDN** *(espelhando este repositório público do GitHub)* | **Ilimitado para projetos Open Source** (CDN global com cache Cloudflare/Fastly) | `5.511` questões com campo `image_url` apontando direto para `cdn.jsdelivr.net` | **100% do storage do Supabase economizado** |
| **Frontend + Backend API** | **Vercel** ou **Cloudflare Pages** | **100 GB bandwidth/mês** (Vercel) ou **Ilimitado** (Cloudflare Pages) | ~5–15 KB por página de questões (JSON textual leve) | Suporta dezenas de milhares de acessos mensais grátis |

---

## 2. O Segredo para Caber no Banco Gratuito: Modelagem Deduplicada (`N:N`)

Em concursos públicos, uma mesma prova de *Conhecimentos Básicos* (Língua Portuguesa, Raciocínio Lógico, Direito Constitucional, etc.) é aplicada simultaneamente para 10 a 30 cargos do mesmo edital.
- Na base da FGV existem **101.769 questões em 1.499 provas**, mas **43.433 são aparições compartilhadas** da mesma questão entre cargos diferentes, restando **58.336 questões únicas** (identificadas pelo campo `question_hash`).

Ao separar a tabela **`questions`** (`58.336` linhas com os textos longos) da tabela leve de vínculo **`exam_questions`** (`101.769` linhas contendo apenas IDs curtos), **economizamos 43% de armazenamento no banco de dados**:

### Esquema Relacional PostgreSQL / Supabase (gerado por `src/export_db.py`)

1. **`exams` (`1.499` registros — ~300 KB)**:
   - `exam_id` (PK), `banca` (`'FGV'`), `concurso_slug`, `concurso_title`, `cargo_slug`, `cargo_title`, `year`, `uf`, `level`, `question_type`, `total_questions`.
2. **`questions` (`58.336` registros únicos — ~36 MB)**:
   - `question_hash` (PK, 16 chars), `banca`, `discipline`, `module`, `question_type`, `base_text`, `statement`, `alternatives` (`JSONB`), `answer`, `annulled`, `has_image`, `image_url`.
3. **`exam_questions` (`101.769` vínculos — ~8 MB)**:
   - `(exam_id, question_number)` (PK), `question_hash` (FK), `discipline`, `module`, `answer`, `annulled`, `page_number`.
4. **Tabelas de Usuários do App (no Supabase)**:
   - `user_answers`: `(user_id, question_hash, selected_option, is_correct, answered_at)` — permite saber quais questões o aluno já resolveu, taxa de acerto por disciplina e revisão espaçada.

---

## 3. Como Servir as Imagens Gratuitamente via CDN (`image_url`)

Todos os 1.499 arquivos JSON em `./data/processed` foram enriquecidos com o campo **`image_url`** pronto para produção:

```json
{
  "has_image": true,
  "image_path": "data/processed/images/cnu2024_bloco-1_q3.png",
  "image_url": "https://cdn.jsdelivr.net/gh/Wellington-Miguel/fgv-scraping@main/data/processed/images/cnu2024_bloco-1_q3.png"
}
```

- **Vantagem**: O componente React/Next.js do seu App só precisa fazer `<img src={question.image_url} loading="lazy" />`. Você não precisa pagar nem configurar AWS S3, nem gastar o limite de 1 GB do Supabase Storage.

---

## 4. Passo a Passo Prático para o Deploy

### Passo 1: Gerar os arquivos de banco localmente

Na raiz deste repositório, execute o utilitário [src/export_db.py](file:///c:/Users/welli/Documents/concurso/fgv-scraping/src/export_db.py):

```bash
# Gera simultaneamente o banco SQLite (data/exports/fgv_concursos.db)
# e o script SQL para PostgreSQL/Supabase (data/exports/supabase_seed.sql)
python -m src.export_db
```

### Passo 2: Subir o Banco de Dados Gratuito (2 Opções)

#### Opção A — Supabase (Recomendado: PostgreSQL + Auth + API Automática)
1. Crie uma conta gratuita em `https://supabase.com` e crie um novo projeto.
2. Copie a **Connection String (URI)** em *Project Settings > Database*.
3. Importe o arquivo gerado `data/exports/supabase_seed.sql` via `psql`:
   ```bash
   psql "postgresql://postgres:[SUA-SENHA]@db.[SEU-PROJETO].supabase.co:5432/postgres" -f data/exports/supabase_seed.sql
   ```
4. Ative o **Row Level Security (RLS)** com política de leitura pública (`SELECT`) para `exams`, `questions` e `exam_questions`, e leitura/escrita autenticada (`auth.uid() = user_id`) na tabela `user_answers`.

#### Opção B — Turso ou Cloudflare D1 (SQLite Serverless com até 5 GB Grátis)
1. Caso prefira uma arquitetura baseada em SQLite na borda (Edge) com **5 GB gratuitos** e **500 milhões de leituras/mês grátis**:
2. Instale a CLI do Turso (`turso db create concursos-db`) e importe diretamente o arquivo SQLite gerado por `python -m src.export_db --sqlite data/exports/fgv_concursos.db`.

### Passo 3: Criar e Hospedar o App de Estudo (Next.js + Vercel)
1. Crie um novo repositório leve apenas para o aplicativo: **`concursos-app`**.
2. Stack sugerida para o App:
   - **Framework**: Next.js (App Router + TypeScript) + TailwindCSS + shadcn/ui.
   - **Cliente de Banco & Auth**: `@supabase/supabase-js` (ou `@libsql/client` se usar Turso).
   - **Funcionalidades principais do MVP**:
     - Filtro rápido por **Banca (`FGV`)**, **Disciplina Canônica (61 opções)**, **Módulo**, **Ano (2012–2026)**, **UF**, **Escolaridade (`Superior`, `Médio`, `Fundamental`)** e **Cargo**.
     - Exibição automática do `base_text` expansível acima do enunciado quando presente.
     - Renderização das alternativas (`A–E`, `A–D` ou `Certo/Errado`) com feedback imediato do gabarito oficial (`answer`) e aviso de questão anulada (`annulled`).
     - Modo **Caderno de Prova Original** (usando a tabela `exam_questions` ordenada por `question_number` de 1 a N).
3. Conecte o repositório `concursos-app` à **Vercel** ou **Cloudflare Pages** — cada `git push` fará o deploy automático em produção gratuitamente.

