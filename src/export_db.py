"""
Utilitário de Exportação e Seed para Banco de Dados Gratuito (PostgreSQL/Supabase/Neon e SQLite/Turso/D1).

Lê a base consolidada em `./data/processed` e exporta em modelo relacional normalizado
e deduplicado por `question_hash` (58.336 questões únicas vinculadas às 101.769 aparições
em 1.499 provas), reduzindo o consumo de armazenamento em ~43% (~45 MB no PostgreSQL/Supabase,
cabendo com ampla folga no plano gratuito de 500 MB).

Modos de uso:
    # 1. Gerar banco SQLite pronto (compatível com Turso / Cloudflare D1 / App local):
    python -m src.export_db --sqlite data/exports/fgv_concursos.db

    # 2. Gerar DDL + script SQL de seed para PostgreSQL (Supabase / Neon):
    python -m src.export_db --postgres-sql data/exports/supabase_seed.sql
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

POSTGRES_DDL = """-- =============================================================================
-- Schema Relacional Otimizado p/ Free Tier (Supabase / Neon PostgreSQL)
-- Modelo Deduplicado (N:N via question_hash): 58.336 questões únicas (~45 MB)
-- =============================================================================

CREATE TABLE IF NOT EXISTS exams (
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

CREATE TABLE IF NOT EXISTS questions (
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

CREATE TABLE IF NOT EXISTS exam_questions (
    exam_id TEXT NOT NULL REFERENCES exams(exam_id) ON DELETE CASCADE,
    question_number SMALLINT NOT NULL,
    question_hash CHAR(16) NOT NULL REFERENCES questions(question_hash) ON DELETE CASCADE,
    discipline TEXT NOT NULL,
    module TEXT NOT NULL,
    answer TEXT NOT NULL,
    annulled BOOLEAN NOT NULL DEFAULT FALSE,
    page_number SMALLINT NOT NULL DEFAULT 1,
    PRIMARY KEY (exam_id, question_number)
);

-- Índices para filtros rápidos no App de Estudo
CREATE INDEX IF NOT EXISTS idx_questions_banca_disc ON questions(banca, discipline);
CREATE INDEX IF NOT EXISTS idx_questions_module ON questions(module);
CREATE INDEX IF NOT EXISTS idx_questions_has_image ON questions(has_image);
CREATE INDEX IF NOT EXISTS idx_exams_banca_year_uf ON exams(banca, year DESC, uf);
CREATE INDEX IF NOT EXISTS idx_exams_level ON exams(level);
CREATE INDEX IF NOT EXISTS idx_exam_questions_hash ON exam_questions(question_hash);
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


def export_to_sqlite(processed_dir: Path, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    exams = load_processed_exams(processed_dir)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SQLITE_DDL)
        cur = conn.cursor()

        exams_rows = []
        questions_map: Dict[str, tuple] = {}
        exam_q_rows = []

        for ex in exams:
            exam_id = ex["_exam_id"]
            banca = ex.get("banca", "FGV")
            q_type = ex.get("question_type", "multiple_choice_5")
            exams_rows.append(
                (
                    exam_id,
                    banca,
                    ex["concurso_slug"],
                    ex.get("concurso_title", ""),
                    ex["cargo_slug"],
                    ex.get("cargo_title", ""),
                    int(ex.get("year", 2024)),
                    ex.get("uf", "BR"),
                    ex.get("level", "Superior"),
                    ex.get("exam_type", "Tipo 1"),
                    q_type,
                    int(ex.get("total_questions", len(ex["questions"]))),
                )
            )

            for q in ex["questions"]:
                q_hash = q["question_hash"]
                img_url = q.get("image_url")
                if not img_url and q.get("has_image") and q.get("image_path"):
                    img_rel = q["image_path"].lstrip("./")
                    img_url = f"https://cdn.jsdelivr.net/gh/Wellington-Miguel/fgv-scraping@main/{img_rel}"

                # Prioriza registro com base_text ou imagem preenchida
                if q_hash not in questions_map or (q.get("base_text") and not questions_map[q_hash][5]):
                    questions_map[q_hash] = (
                        q_hash,
                        banca,
                        q["discipline"],
                        q.get("module", "Conhecimentos Específicos"),
                        q_type,
                        q.get("base_text"),
                        q["statement"],
                        json.dumps(q["alternatives"], ensure_ascii=False),
                        q["answer"],
                        1 if q.get("annulled") else 0,
                        1 if q.get("has_image") else 0,
                        img_url,
                    )

                exam_q_rows.append(
                    (
                        exam_id,
                        int(q["question_number"]),
                        q_hash,
                        q["discipline"],
                        q.get("module", "Conhecimentos Específicos"),
                        q["answer"],
                        1 if q.get("annulled") else 0,
                        int(q.get("page_number", 1)),
                    )
                )

        cur.executemany(
            "INSERT INTO exams VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            exams_rows,
        )
        cur.executemany(
            "INSERT INTO questions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(questions_map.values()),
        )
        cur.executemany(
            "INSERT INTO exam_questions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            exam_q_rows,
        )
        conn.commit()
        print(
            f"[SQLite] Banco exportado em {db_path} "
            f"({len(exams_rows)} provas, {len(questions_map)} questões únicas, {len(exam_q_rows)} vínculos)."
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


def export_to_postgres_sql(processed_dir: Path, sql_path: Path) -> None:
    sql_path.parent.mkdir(parents=True, exist_ok=True)
    exams = load_processed_exams(processed_dir)

    questions_map: Dict[str, Dict[str, Any]] = {}
    with sql_path.open("w", encoding="utf-8") as f:
        f.write(POSTGRES_DDL + "\nBEGIN;\n\n")

        for ex in exams:
            exam_id = ex["_exam_id"]
            banca = ex.get("banca", "FGV")
            q_type = ex.get("question_type", "multiple_choice_5")
            f.write(
                "INSERT INTO exams (exam_id, banca, concurso_slug, concurso_title, cargo_slug, cargo_title, "
                "year, uf, level, exam_type, question_type, total_questions) VALUES ("
                f"{_sql_lit(exam_id)}, {_sql_lit(banca)}, {_sql_lit(ex['concurso_slug'])}, "
                f"{_sql_lit(ex.get('concurso_title', ''))}, {_sql_lit(ex['cargo_slug'])}, "
                f"{_sql_lit(ex.get('cargo_title', ''))}, {int(ex.get('year', 2024))}, "
                f"{_sql_lit(ex.get('uf', 'BR'))}, {_sql_lit(ex.get('level', 'Superior'))}, "
                f"{_sql_lit(ex.get('exam_type', 'Tipo 1'))}, {_sql_lit(q_type)}, "
                f"{int(ex.get('total_questions', len(ex['questions'])))}"
                ") ON CONFLICT (exam_id) DO NOTHING;\n"
            )

            for q in ex["questions"]:
                q_hash = q["question_hash"]
                if q_hash not in questions_map or (
                    q.get("base_text") and not questions_map[q_hash].get("base_text")
                ):
                    img_url = q.get("image_url")
                    if not img_url and q.get("has_image") and q.get("image_path"):
                        img_rel = q["image_path"].lstrip("./")
                        img_url = f"https://cdn.jsdelivr.net/gh/Wellington-Miguel/fgv-scraping@main/{img_rel}"
                    questions_map[q_hash] = {
                        **q,
                        "banca": banca,
                        "question_type": q_type,
                        "image_url": img_url,
                    }

        f.write("\n")
        for q_hash, q in questions_map.items():
            alt_json = json.dumps(q["alternatives"], ensure_ascii=False)
            f.write(
                "INSERT INTO questions (question_hash, banca, discipline, module, question_type, "
                "base_text, statement, alternatives, answer, annulled, has_image, image_url) VALUES ("
                f"{_sql_lit(q_hash)}, {_sql_lit(q['banca'])}, {_sql_lit(q['discipline'])}, "
                f"{_sql_lit(q.get('module', 'Conhecimentos Específicos'))}, {_sql_lit(q['question_type'])}, "
                f"{_sql_lit(q.get('base_text'))}, {_sql_lit(q['statement'])}, "
                f"{_sql_lit(alt_json)}::jsonb, {_sql_lit(q['answer'])}, "
                f"{_sql_lit(bool(q.get('annulled')))}, {_sql_lit(bool(q.get('has_image')))}, "
                f"{_sql_lit(q.get('image_url'))}"
                ") ON CONFLICT (question_hash) DO NOTHING;\n"
            )

        f.write("\n")
        for ex in exams:
            exam_id = ex["_exam_id"]
            for q in ex["questions"]:
                f.write(
                    "INSERT INTO exam_questions (exam_id, question_number, question_hash, discipline, "
                    "module, answer, annulled, page_number) VALUES ("
                    f"{_sql_lit(exam_id)}, {int(q['question_number'])}, {_sql_lit(q['question_hash'])}, "
                    f"{_sql_lit(q['discipline'])}, {_sql_lit(q.get('module', 'Conhecimentos Específicos'))}, "
                    f"{_sql_lit(q['answer'])}, {_sql_lit(bool(q.get('annulled')))}, "
                    f"{int(q.get('page_number', 1))}"
                    ") ON CONFLICT (exam_id, question_number) DO NOTHING;\n"
                )

        f.write("\nCOMMIT;\n")
    print(
        f"[PostgreSQL] Script SQL gerado em {sql_path} "
        f"({len(exams)} provas, {len(questions_map)} questões únicas)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exporta a base consolidada da FGV para SQLite (Turso/D1) ou PostgreSQL (Supabase/Neon)."
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
    args = parser.parse_args()

    if not args.sqlite and not args.postgres_sql:
        args.sqlite = Path("data/exports/fgv_concursos.db")
        args.postgres_sql = Path("data/exports/supabase_seed.sql")

    if args.sqlite:
        export_to_sqlite(args.processed_dir, args.sqlite)
    if args.postgres_sql:
        export_to_postgres_sql(args.processed_dir, args.postgres_sql)


if __name__ == "__main__":
    main()

