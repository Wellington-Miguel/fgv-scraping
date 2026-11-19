"""
Utilitário de Exportação e Deploy para Banco de Dados Gratuito (Supabase / PostgreSQL e SQLite / Turso).

Lê a base consolidada em `./data/processed` e exporta/envia em modelo relacional normalizado
e deduplicado por `question_hash` (58.336 questões únicas vinculadas às 101.769 aparições
em 1.499 provas), reduzindo o consumo de armazenamento em ~43% (~45-85 MB no Supabase,
cabendo com ampla folga no plano gratuito de 500 MB).

Modos de uso:
    # 1. Gerar arquivos locais (SQLite + DDL enxuto p/ Supabase SQL Editor + Seed SQL completo):
    python -m src.export_db

    # 2. Enviar todos os dados direto para o Supabase via HTTPS REST API (sem precisar de psql/IPv6):
    python -m src.export_db --push-supabase --supabase-url https://SEU_ID.supabase.co --supabase-key SUA_SERVICE_ROLE_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from tqdm import tqdm

_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean_pg_str(val: Optional[str]) -> Optional[str]:
    """Remove null bytes (\\u0000) e caracteres de controle inválidos no PostgreSQL TEXT/JSONB."""
    if val is None:
        return None
    return _CTRL_CHARS_RE.sub("", val)

SUPABASE_SCHEMA_DDL = """-- =============================================================================
-- 1. SCHEMA RELACIONAL OTIMIZADO PARA SUPABASE (FREE TIER - 500 MB)
-- Cole este conteúdo no "SQL Editor" do painel web do Supabase e clique em RUN.
-- =============================================================================

-- Tabela 1: Cadernos de Prova (1.499 registros)
CREATE TABLE IF NOT EXISTS public.exams (
    exam_id TEXT PRIMARY KEY,
    banca TEXT NOT NULL DEFAULT 'FGV',
    concurso_slug TEXT NOT NULL,
    concurso_title TEXT NOT NULL,
    cargo_slug TEXT NOT NULL,
    cargo_title TEXT NOT NULL,
    year SMALLINT NOT NULL,
    uf CHAR(2) NOT NULL,
    level TEXT NOT NULL,
    exam_type TEXT NOT NULL DEFAULT 'Tipo 1',
    question_type TEXT NOT NULL,
    total_questions SMALLINT NOT NULL
);

-- Tabela 2: Questões Únicas Deduplicadas via SHA-1 (58.336 registros)
CREATE TABLE IF NOT EXISTS public.questions (
    question_hash CHAR(16) PRIMARY KEY,
    banca TEXT NOT NULL DEFAULT 'FGV',
    discipline TEXT NOT NULL,
    module TEXT NOT NULL,
    question_type TEXT NOT NULL,
    base_text TEXT,
    statement TEXT NOT NULL,
    alternatives JSONB NOT NULL,
    answer TEXT NOT NULL,
    annulled BOOLEAN NOT NULL DEFAULT FALSE,
    has_image BOOLEAN NOT NULL DEFAULT FALSE,
    image_url TEXT
);

-- Tabela 3: Vínculo N:N Prova <-> Questão (101.769 registros)
CREATE TABLE IF NOT EXISTS public.exam_questions (
    exam_id TEXT NOT NULL REFERENCES public.exams(exam_id) ON DELETE CASCADE,
    question_number SMALLINT NOT NULL,
    question_hash CHAR(16) NOT NULL REFERENCES public.questions(question_hash) ON DELETE CASCADE,
    discipline TEXT NOT NULL,
    module TEXT NOT NULL,
    answer TEXT NOT NULL,
    annulled BOOLEAN NOT NULL DEFAULT FALSE,
    page_number SMALLINT NOT NULL DEFAULT 1,
    PRIMARY KEY (exam_id, question_number)
);

-- Tabela 4: Respostas e Progresso dos Alunos no App (Modelo UPSERT: 1 linha por Aluno + Questão)
CREATE TABLE IF NOT EXISTS public.user_answers (
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    question_hash CHAR(16) NOT NULL REFERENCES public.questions(question_hash) ON DELETE CASCADE,
    exam_id TEXT REFERENCES public.exams(exam_id) ON DELETE SET NULL,
    selected_option TEXT NOT NULL,
    is_correct BOOLEAN NOT NULL,
    attempts SMALLINT NOT NULL DEFAULT 1,
    correct_count SMALLINT NOT NULL DEFAULT 0,
    answered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, question_hash)
);

-- Índices para filtros rápidos no App de Estudo
CREATE INDEX IF NOT EXISTS idx_questions_banca_disc ON public.questions(banca, discipline);
CREATE INDEX IF NOT EXISTS idx_questions_module ON public.questions(module);
CREATE INDEX IF NOT EXISTS idx_questions_has_image ON public.questions(has_image);
CREATE INDEX IF NOT EXISTS idx_exams_banca_year_uf ON public.exams(banca, year DESC, uf);
CREATE INDEX IF NOT EXISTS idx_exams_level ON public.exams(level);
CREATE INDEX IF NOT EXISTS idx_exam_questions_hash ON public.exam_questions(question_hash);
CREATE INDEX IF NOT EXISTS idx_user_answers_user_correct ON public.user_answers(user_id, is_correct);

-- =============================================================================
-- 2. POLÍTICAS DE SEGURANÇA (ROW LEVEL SECURITY - RLS) E FUNÇÃO RPC DE UPSERT
-- =============================================================================
ALTER TABLE public.exams ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.exam_questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_answers ENABLE ROW LEVEL SECURITY;

-- Leitura pública para provas e questões (qualquer visitante/aluno pode consultar)
DROP POLICY IF EXISTS "Leitura publica exams" ON public.exams;
CREATE POLICY "Leitura publica exams" ON public.exams FOR SELECT USING (true);

DROP POLICY IF EXISTS "Leitura publica questions" ON public.questions;
CREATE POLICY "Leitura publica questions" ON public.questions FOR SELECT USING (true);

DROP POLICY IF EXISTS "Leitura publica exam_questions" ON public.exam_questions;
CREATE POLICY "Leitura publica exam_questions" ON public.exam_questions FOR SELECT USING (true);

-- Cada usuário autenticado só lê, insere e atualiza o próprio histórico de respostas
DROP POLICY IF EXISTS "Usuario le proprias respostas" ON public.user_answers;
CREATE POLICY "Usuario le proprias respostas" ON public.user_answers
    FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Usuario insere proprias respostas" ON public.user_answers;
CREATE POLICY "Usuario insere proprias respostas" ON public.user_answers
    FOR INSERT WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Usuario atualiza proprias respostas" ON public.user_answers;
CREATE POLICY "Usuario atualiza proprias respostas" ON public.user_answers
    FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- Função RPC atômica para registrar tentativa via UPSERT incrementando attempts e correct_count
CREATE OR REPLACE FUNCTION public.submit_answer(
    p_question_hash CHAR(16),
    p_selected_option TEXT,
    p_is_correct BOOLEAN,
    p_exam_id TEXT DEFAULT NULL
)
RETURNS public.user_answers
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
    v_result public.user_answers;
BEGIN
    INSERT INTO public.user_answers (
        user_id, question_hash, exam_id, selected_option, is_correct, attempts, correct_count, answered_at
    )
    VALUES (
        auth.uid(),
        p_question_hash,
        p_exam_id,
        p_selected_option,
        p_is_correct,
        1,
        CASE WHEN p_is_correct THEN 1 ELSE 0 END,
        NOW()
    )
    ON CONFLICT (user_id, question_hash) DO UPDATE SET
        exam_id = COALESCE(EXCLUDED.exam_id, public.user_answers.exam_id),
        selected_option = EXCLUDED.selected_option,
        is_correct = EXCLUDED.is_correct,
        attempts = public.user_answers.attempts + 1,
        correct_count = public.user_answers.correct_count + (CASE WHEN EXCLUDED.is_correct THEN 1 ELSE 0 END),
        answered_at = NOW()
    RETURNING * INTO v_result;

    RETURN v_result;
END;
$$;
"""

SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS exams (
    exam_id TEXT PRIMARY KEY,
    banca TEXT NOT NULL DEFAULT 'FGV',
    concurso_slug TEXT NOT NULL,
    concurso_title TEXT NOT NULL,
    cargo_slug TEXT NOT NULL,
    cargo_title TEXT NOT NULL,
    year INTEGER NOT NULL,
    uf TEXT NOT NULL,
    level TEXT NOT NULL,
    exam_type TEXT NOT NULL DEFAULT 'Tipo 1',
    question_type TEXT NOT NULL,
    total_questions INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
    question_hash TEXT PRIMARY KEY,
    banca TEXT NOT NULL DEFAULT 'FGV',
    discipline TEXT NOT NULL,
    module TEXT NOT NULL,
    question_type TEXT NOT NULL,
    base_text TEXT,
    statement TEXT NOT NULL,
    alternatives_json TEXT NOT NULL,
    answer TEXT NOT NULL,
    annulled INTEGER NOT NULL DEFAULT 0,
    has_image INTEGER NOT NULL DEFAULT 0,
    image_url TEXT
);

CREATE TABLE IF NOT EXISTS exam_questions (
    exam_id TEXT NOT NULL,
    question_number INTEGER NOT NULL,
    question_hash TEXT NOT NULL,
    discipline TEXT NOT NULL,
    module TEXT NOT NULL,
    answer TEXT NOT NULL,
    annulled INTEGER NOT NULL DEFAULT 0,
    page_number INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (exam_id, question_number),
    FOREIGN KEY (exam_id) REFERENCES exams(exam_id),
    FOREIGN KEY (question_hash) REFERENCES questions(question_hash)
);

CREATE INDEX IF NOT EXISTS idx_questions_banca_disc ON questions(banca, discipline);
CREATE INDEX IF NOT EXISTS idx_questions_module ON questions(module);
CREATE INDEX IF NOT EXISTS idx_exams_banca_year_uf ON exams(banca, year DESC, uf);
CREATE INDEX IF NOT EXISTS idx_exams_level ON exams(level);
CREATE INDEX IF NOT EXISTS idx_exam_questions_hash ON exam_questions(question_hash);
"""


def load_processed_exams(processed_dir: Path) -> List[Dict[str, Any]]:
    exams = []
    for jpath in sorted(processed_dir.glob("*.json")):
        if jpath.name == "_index.json":
            continue
        data = json.loads(jpath.read_text(encoding="utf-8"))
        data["_exam_id"] = jpath.stem
        exams.append(data)
    return exams


def prepare_relational_payloads(
    exams: List[Dict[str, Any]]
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Transforma a lista de JSONs nas 3 coleções normalizadas: exams, questions (únicas), exam_questions."""
    exams_list: List[Dict[str, Any]] = []
    questions_map: Dict[str, Dict[str, Any]] = {}
    exam_questions_list: List[Dict[str, Any]] = []

    for ex in exams:
        exam_id = ex["_exam_id"]
        banca = ex.get("banca", "FGV")
        q_type = ex.get("question_type", "multiple_choice_5")

        exams_list.append(
            {
                "exam_id": exam_id,
                "banca": banca,
                "concurso_slug": ex["concurso_slug"],
                "concurso_title": ex.get("concurso_title", ""),
                "cargo_slug": ex["cargo_slug"],
                "cargo_title": ex.get("cargo_title", ""),
                "year": int(ex.get("year", 2024)),
                "uf": ex.get("uf", "BR"),
                "level": ex.get("level", "Superior"),
                "exam_type": ex.get("exam_type", "Tipo 1"),
                "question_type": q_type,
                "total_questions": int(ex.get("total_questions", len(ex["questions"]))),
            }
        )

        for q in ex["questions"]:
            q_hash = q["question_hash"]
            img_url = q.get("image_url")
            if not img_url and q.get("has_image") and q.get("image_path"):
                img_rel = q["image_path"].lstrip("./")
                img_url = f"https://cdn.jsdelivr.net/gh/Wellington-Miguel/fgv-scraping@main/{img_rel}"

            if q_hash not in questions_map or (
                q.get("base_text") and not questions_map[q_hash].get("base_text")
            ):
                clean_alts = {
                    k: _clean_pg_str(v) if isinstance(v, str) else v
                    for k, v in q["alternatives"].items()
                }
                questions_map[q_hash] = {
                    "question_hash": q_hash,
                    "banca": banca,
                    "discipline": _clean_pg_str(q["discipline"]) or "Conhecimentos Específicos",
                    "module": _clean_pg_str(q.get("module", "Conhecimentos Específicos")),
                    "question_type": q_type,
                    "base_text": _clean_pg_str(q.get("base_text")),
                    "statement": _clean_pg_str(q["statement"]) or "",
                    "alternatives": clean_alts,
                    "answer": q["answer"],
                    "annulled": bool(q.get("annulled")),
                    "has_image": bool(q.get("has_image")),
                    "image_url": img_url,
                }

            exam_questions_list.append(
                {
                    "exam_id": exam_id,
                    "question_number": int(q["question_number"]),
                    "question_hash": q_hash,
                    "discipline": q["discipline"],
                    "module": q.get("module", "Conhecimentos Específicos"),
                    "answer": q["answer"],
                    "annulled": bool(q.get("annulled")),
                    "page_number": int(q.get("page_number", 1)),
                }
            )

    return exams_list, list(questions_map.values()), exam_questions_list


def export_to_sqlite(processed_dir: Path, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    exams = load_processed_exams(processed_dir)
    exams_list, questions_list, exam_q_list = prepare_relational_payloads(exams)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SQLITE_DDL)
        cur = conn.cursor()

        cur.executemany(
            "INSERT INTO exams VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    e["exam_id"],
                    e["banca"],
                    e["concurso_slug"],
                    e["concurso_title"],
                    e["cargo_slug"],
                    e["cargo_title"],
                    e["year"],
                    e["uf"],
                    e["level"],
                    e["exam_type"],
                    e["question_type"],
                    e["total_questions"],
                )
                for e in exams_list
            ],
        )
        cur.executemany(
            "INSERT INTO questions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    q["question_hash"],
                    q["banca"],
                    q["discipline"],
                    q["module"],
                    q["question_type"],
                    q["base_text"],
                    q["statement"],
                    json.dumps(q["alternatives"], ensure_ascii=False),
                    q["answer"],
                    1 if q["annulled"] else 0,
                    1 if q["has_image"] else 0,
                    q["image_url"],
                )
                for q in questions_list
            ],
        )
        cur.executemany(
            "INSERT INTO exam_questions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    eq["exam_id"],
                    eq["question_number"],
                    eq["question_hash"],
                    eq["discipline"],
                    eq["module"],
                    eq["answer"],
                    1 if eq["annulled"] else 0,
                    eq["page_number"],
                )
                for eq in exam_q_list
            ],
        )
        conn.commit()
        print(
            f"[SQLite] Banco exportado em {db_path} "
            f"({len(exams_list)} provas, {len(questions_list)} questões únicas, {len(exam_q_list)} vínculos)."
        )
    finally:
        conn.close()


def _sql_lit(val: Any) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, int):
        return str(val)
    s = str(val).replace("'", "''")
    return f"'{s}'"


def export_to_postgres_sql(processed_dir: Path, sql_path: Path, schema_only_path: Path) -> None:
    sql_path.parent.mkdir(parents=True, exist_ok=True)
    schema_only_path.write_text(SUPABASE_SCHEMA_DDL, encoding="utf-8")
    print(f"[Supabase Schema] DDL enxuto salvo em {schema_only_path} (pronto para colar no SQL Editor do Supabase).")

    exams = load_processed_exams(processed_dir)
    exams_list, questions_list, exam_q_list = prepare_relational_payloads(exams)

    with sql_path.open("w", encoding="utf-8") as f:
        f.write(SUPABASE_SCHEMA_DDL + "\nBEGIN;\n\n")

        for e in exams_list:
            f.write(
                "INSERT INTO public.exams (exam_id, banca, concurso_slug, concurso_title, cargo_slug, cargo_title, "
                "year, uf, level, exam_type, question_type, total_questions) VALUES ("
                f"{_sql_lit(e['exam_id'])}, {_sql_lit(e['banca'])}, {_sql_lit(e['concurso_slug'])}, "
                f"{_sql_lit(e['concurso_title'])}, {_sql_lit(e['cargo_slug'])}, "
                f"{_sql_lit(e['cargo_title'])}, {e['year']}, {_sql_lit(e['uf'])}, "
                f"{_sql_lit(e['level'])}, {_sql_lit(e['exam_type'])}, {_sql_lit(e['question_type'])}, "
                f"{e['total_questions']}) ON CONFLICT (exam_id) DO NOTHING;\n"
            )

        f.write("\n")
        for q in questions_list:
            alt_json = json.dumps(q["alternatives"], ensure_ascii=False)
            f.write(
                "INSERT INTO public.questions (question_hash, banca, discipline, module, question_type, "
                "base_text, statement, alternatives, answer, annulled, has_image, image_url) VALUES ("
                f"{_sql_lit(q['question_hash'])}, {_sql_lit(q['banca'])}, {_sql_lit(q['discipline'])}, "
                f"{_sql_lit(q['module'])}, {_sql_lit(q['question_type'])}, {_sql_lit(q['base_text'])}, "
                f"{_sql_lit(q['statement'])}, {_sql_lit(alt_json)}::jsonb, {_sql_lit(q['answer'])}, "
                f"{_sql_lit(q['annulled'])}, {_sql_lit(q['has_image'])}, {_sql_lit(q['image_url'])}"
                ") ON CONFLICT (question_hash) DO NOTHING;\n"
            )

        f.write("\n")
        for eq in exam_q_list:
            f.write(
                "INSERT INTO public.exam_questions (exam_id, question_number, question_hash, discipline, "
                "module, answer, annulled, page_number) VALUES ("
                f"{_sql_lit(eq['exam_id'])}, {eq['question_number']}, {_sql_lit(eq['question_hash'])}, "
                f"{_sql_lit(eq['discipline'])}, {_sql_lit(eq['module'])}, {_sql_lit(eq['answer'])}, "
                f"{_sql_lit(eq['annulled'])}, {eq['page_number']}"
                ") ON CONFLICT (exam_id, question_number) DO NOTHING;\n"
            )

        f.write("\nCOMMIT;\n")
    print(
        f"[PostgreSQL] Script SQL completo gerado em {sql_path} "
        f"({len(exams_list)} provas, {len(questions_list)} questões únicas)."
    )


def push_to_supabase_rest(
    processed_dir: Path,
    supabase_url: str,
    supabase_key: str,
    batch_size: int = 500,
) -> None:
    """
    Envia toda a base diretamente para o Supabase via HTTPS REST API (PostgREST),
    sem precisar instalar `psql` e sem problemas de bloqueio IPv6.
    """
    base_url = supabase_url.rstrip("/") + "/rest/v1"
    session = requests.Session()
    session.headers.update(
        {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }
    )

    exams = load_processed_exams(processed_dir)
    exams_list, questions_list, exam_q_list = prepare_relational_payloads(exams)

    def _upload_batches(table: str, rows: List[Dict[str, Any]], on_conflict: str) -> None:
        url = f"{base_url}/{table}?on_conflict={on_conflict}"
        for i in tqdm(range(0, len(rows), batch_size), desc=f"Subindo {table}"):
            chunk = rows[i : i + batch_size]
            for attempt in range(1, 4):
                resp = session.post(url, json=chunk, timeout=60)
                if resp.status_code in (200, 201, 204):
                    break
                if attempt == 3:
                    raise RuntimeError(
                        f"Erro ao subir lote em {table} (HTTP {resp.status_code}): {resp.text[:400]}"
                    )
                time.sleep(1.5 * attempt)

    print(f"Iniciando upload via HTTPS para {base_url}...")
    _upload_batches("exams", exams_list, on_conflict="exam_id")
    _upload_batches("questions", questions_list, on_conflict="question_hash")
    _upload_batches("exam_questions", exam_q_list, on_conflict="exam_id,question_number")
    print("Upload para o Supabase concluído com 100% de sucesso!")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exporta ou envia a base consolidada da FGV para SQLite (Turso/D1) ou Supabase (PostgreSQL)."
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=Path("./data/processed"),
        help="Diretório com os JSONs consolidados.",
    )
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=None,
        help="Caminho de saída para gerar banco SQLite (.db).",
    )
    parser.add_argument(
        "--postgres-sql",
        type=Path,
        default=None,
        help="Caminho de saída para gerar script SQL de seed para PostgreSQL/Supabase (.sql).",
    )
    parser.add_argument(
        "--push-supabase",
        action="store_true",
        help="Envia todas as provas e questões direto para o Supabase via HTTPS REST API.",
    )
    parser.add_argument(
        "--supabase-url",
        type=str,
        default=os.environ.get("SUPABASE_URL"),
        help="URL do projeto Supabase (ex: https://xyz.supabase.co) ou variável SUPABASE_URL.",
    )
    parser.add_argument(
        "--supabase-key",
        type=str,
        default=(
            os.environ.get("SUPABASE_SECRET_KEY")
            or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            or os.environ.get("SUPABASE_KEY")
        ),
        help="Secret key (sb_secret_...) ou service_role do Supabase.",
    )
    args = parser.parse_args()

    if args.push_supabase:
        if not args.supabase_url or not args.supabase_key:
            raise SystemExit(
                "Erro: para usar --push-supabase, informe --supabase-url e --supabase-key "
                "(ou defina SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY no ambiente)."
            )
        push_to_supabase_rest(args.processed_dir, args.supabase_url, args.supabase_key)
        return

    if not args.sqlite and not args.postgres_sql:
        args.sqlite = Path("data/exports/fgv_concursos.db")
        args.postgres_sql = Path("data/exports/supabase_seed.sql")

    schema_only_path = Path("data/exports/01_supabase_schema.sql")
    if args.sqlite:
        export_to_sqlite(args.processed_dir, args.sqlite)
    if args.postgres_sql:
        export_to_postgres_sql(args.processed_dir, args.postgres_sql, schema_only_path)


if __name__ == "__main__":
    main()
