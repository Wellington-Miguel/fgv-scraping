"""
Módulo de Parsing Robusto para Provas e Gabaritos da FGV Conhecimento.

Implementa:
- Validação rigorosa via Pydantic (Questões, Alternativas A-E, Gabarito, Imagem e Anulação).
- Leitura em 2 colunas via PyMuPDF (`page.get_text("blocks")`), separando esquerda/direita
  por coordenada `x0` vs largura da página e ordenando verticalmente (`y0`), com remoção
  de cabeçalhos e rodapés repetitivos.
- Extração de gabarito via `pdfplumber`, inspecionando os cabeçalhos das tabelas e seções
  de cargo/tipo para isolar estritamente o gabarito correspondente ao **Tipo 1** (Branca)
  do respectivo cargo, suportando tanto formato colunar quanto grades horizontais FGV.
- Detecção e recorte de elementos gráficos/imagens (`page.get_images()` e `page.get_drawings()`)
  entre o enunciado da questão e a alternativa (A), exportando em 200 DPI para
  `./data/processed/images/{slug}_q{num}.png`.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import fitz  # PyMuPDF
import pdfplumber
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

ValidAnswer = Literal["A", "B", "C", "D", "E", "ANULADA"]


def _norm(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_str).strip().lower()


class AlternativesSchema(BaseModel):
    """Schema Pydantic para as 5 alternativas padrão da FGV (A a E)."""

    A: str = Field(..., min_length=1, description="Texto da alternativa A")
    B: str = Field(..., min_length=1, description="Texto da alternativa B")
    C: str = Field(..., min_length=1, description="Texto da alternativa C")
    D: str = Field(..., min_length=1, description="Texto da alternativa D")
    E: str = Field(..., min_length=1, description="Texto da alternativa E")

    @field_validator("A", "B", "C", "D", "E")
    @classmethod
    def clean_whitespace(cls, v: str) -> str:
        cleaned = re.sub(r"\s+", " ", v).strip()
        if not cleaned:
            raise ValueError("O texto da alternativa não pode ser vazio.")
        return cleaned


class QuestionSchema(BaseModel):
    """Schema Pydantic rígido para validação de cada questão extraída."""

    question_number: int = Field(..., ge=1, le=250)
    discipline: Optional[str] = Field(default=None)
    statement: str = Field(..., min_length=5, description="Enunciado da questão")
    alternatives: AlternativesSchema
    answer: Optional[ValidAnswer] = Field(
        default=None, description="Letra do gabarito (A-E) ou ANULADA"
    )
    annulled: bool = Field(default=False, description="Indica se a questão foi anulada (*)")
    has_image: bool = Field(
        default=False,
        description="Indica se há imagem/vetor entre o início da questão e a alternativa (A)",
    )
    image_path: Optional[str] = Field(default=None)
    page_number: int = Field(..., ge=1)

    @field_validator("statement")
    @classmethod
    def clean_statement(cls, v: str) -> str:
        cleaned = re.sub(r"\s+", " ", v).strip()
        if len(cleaned) < 5:
            raise ValueError("Enunciado muito curto ou inválido.")
        return cleaned

    @model_validator(mode="after")
    def sync_annulled_flag(self) -> "QuestionSchema":
        if self.answer == "ANULADA":
            self.annulled = True
        elif self.annulled and self.answer is None:
            self.answer = "ANULADA"
        if self.has_image and not self.image_path:
            raise ValueError("has_image=True requer image_path preenchido.")
        return self


class ExamParsedSchema(BaseModel):
    """Schema do documento final processado de uma prova."""

    concurso_slug: str
    cargo_slug: str
    concurso_title: str
    cargo_title: str
    exam_type: str = "Tipo 1"
    total_questions: int
    questions: List[QuestionSchema]


@dataclass
class TextBlockItem:
    """Bloco de texto posicionado na página do PDF."""

    page_index: int  # 0-based
    column: int  # 0 = esquerda, 1 = direita
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    page_width: float
    page_height: float


class FGVParser:
    """Parser de provas objetivas (2 colunas) e gabaritos da FGV Conhecimento."""

    HEADER_RATIO = 0.065
    FOOTER_RATIO = 0.935

    QUESTION_START_RE = re.compile(
        r"^(?:QUEST[ÃA]O\s+)?(\d{1,3})(?:\s*[\.\-\)]\s*|\s+|\n|$)(.*)$",
        re.IGNORECASE | re.DOTALL,
    )

    ALT_SPLIT_RE = re.compile(
        r"(?:^|\n|\s)(?:\(([A-E])\)|([A-E])\s*[\)\.\-])\s+",
        re.MULTILINE,
    )

    def __init__(
        self,
        raw_dir: Path = Path("./data/raw"),
        processed_dir: Path = Path("./data/processed"),
        image_dpi: int = 200,
    ) -> None:
        self.raw_dir = Path(raw_dir)
        self.processed_dir = Path(processed_dir)
        self.images_dir = self.processed_dir / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.image_dpi = image_dpi

    def parse_all(self) -> List[Path]:
        """Percorre todos os diretórios em `./data/raw/{concurso}/{cargo}` e executa o parser."""
        output_files: List[Path] = []
        if not self.raw_dir.exists():
            logger.warning("Diretório raw não encontrado: %s", self.raw_dir)
            return output_files

        prova_files = sorted(self.raw_dir.glob("*/*/prova_tipo1.pdf"))
        logger.info("Encontrados %d cadernos de prova para parsing.", len(prova_files))

        for prova_path in prova_files:
            cargo_dir = prova_path.parent
            try:
                out_path = self.parse_cargo_dir(cargo_dir)
                if out_path:
                    output_files.append(out_path)
            except Exception as exc:
                logger.error("Falha ao processar diretório %s: %s", cargo_dir, exc, exc_info=True)

        return output_files

    def parse_cargo_dir(self, cargo_dir: Path) -> Optional[Path]:
        """
        Processa um diretório de cargo contendo `prova_tipo1.pdf`, `gabarito.pdf` e `metadata.json`.
        Se o PDF não contiver nenhuma questão objetiva válida (ex: documento administrativo ou discursivo),
        não gera JSON vazio em `./data/processed`.
        """
        prova_path = cargo_dir / "prova_tipo1.pdf"
        gabarito_path = cargo_dir / "gabarito.pdf"
        metadata_path = cargo_dir / "metadata.json"

        if not prova_path.exists():
            logger.warning("Arquivo %s não encontrado.", prova_path)
            return None

        metadata: Dict[str, str] = {}
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Não foi possível ler metadata.json em %s: %s", cargo_dir, exc)

        concurso_slug = metadata.get("concurso_slug") or cargo_dir.parent.name
        cargo_slug = metadata.get("cargo_slug") or cargo_dir.name
        concurso_title = metadata.get("concurso_title") or concurso_slug
        cargo_title = metadata.get("cargo_title") or cargo_slug
        exam_type = metadata.get("exam_type") or "Tipo 1"
        combined_slug = f"{concurso_slug}_{cargo_slug}"

        # 1. Extrair mapa de gabarito (Tipo 1) pareado com o cargo se o PDF existir
        answers_map: Dict[int, ValidAnswer] = {}
        if gabarito_path.exists():
            answers_map = self.parse_gabarito_pdf(
                gabarito_path,
                target_type="1",
                cargo_hint=cargo_title,
            )
            logger.info(
                "Gabarito extraído (%s): %d respostas mapeadas para Tipo 1.",
                combined_slug,
                len(answers_map),
            )

        # 2. Extrair questões da prova em 2 colunas + recortar imagens
        questions = self.parse_prova_pdf(
            prova_path=prova_path,
            slug_prefix=combined_slug,
            answers_map=answers_map,
        )

        if not questions:
            logger.info(
                "Ignorando %s: nenhuma questão objetiva (A-E) detectada no PDF.",
                combined_slug,
            )
            return None

        exam_doc = ExamParsedSchema(
            concurso_slug=concurso_slug,
            cargo_slug=cargo_slug,
            concurso_title=concurso_title,
            cargo_title=cargo_title,
            exam_type=exam_type,
            total_questions=len(questions),
            questions=questions,
        )

        self.processed_dir.mkdir(parents=True, exist_ok=True)
        output_json = self.processed_dir / f"{combined_slug}.json"
        output_json.write_text(
            exam_doc.model_dump_json(indent=2),
            encoding="utf-8",
        )
        logger.info(
            "Prova processada com sucesso: %s (%d questões válidas, %d com gabarito)",
            output_json.name,
            len(questions),
            sum(1 for q in questions if q.answer is not None),
        )
        return output_json

    def parse_prova_pdf(
        self,
        prova_path: Path,
        slug_prefix: str,
        answers_map: Optional[Dict[int, ValidAnswer]] = None,
    ) -> List[QuestionSchema]:
        """
        Extrai as questões do caderno de prova usando PyMuPDF (`fitz`),
        ordenando blocos em duas colunas e recortando regiões gráficas.
        """
        answers_map = answers_map or {}
        doc = fitz.open(prova_path)
        try:
            ordered_blocks = self._extract_two_column_blocks(doc)
            raw_questions = self._segment_questions(ordered_blocks)

            validated_questions: List[QuestionSchema] = []
            for rq in raw_questions:
                q_num = rq["question_number"]
                page_idx = rq["page_index"]
                page = doc[page_idx]

                image_path_str: Optional[str] = None
                has_image = False
                if rq.get("alt_a_y0") is not None and rq["column"] == rq.get("alt_a_col"):
                    cropped = self._check_and_crop_visuals(
                        page=page,
                        column=rq["column"],
                        q_y0=rq["q_y0"],
                        alt_a_y0=rq["alt_a_y0"],
                        slug_prefix=slug_prefix,
                        question_number=q_num,
                    )
                    if cropped:
                        has_image = True
                        image_path_str = str(cropped.as_posix())

                ans = answers_map.get(q_num)
                annulled = ans == "ANULADA"

                try:
                    q_schema = QuestionSchema(
                        question_number=q_num,
                        discipline=rq.get("discipline"),
                        statement=rq["statement"],
                        alternatives=AlternativesSchema(**rq["alternatives"]),
                        answer=ans,
                        annulled=annulled,
                        has_image=has_image,
                        image_path=image_path_str,
                        page_number=page_idx + 1,
                    )
                    validated_questions.append(q_schema)
                except Exception as val_err:
                    logger.debug(
                        "Questão %d descartada na validação Pydantic (%s): %s",
                        q_num,
                        slug_prefix,
                        val_err,
                    )

            return validated_questions
        finally:
            doc.close()

    def _extract_two_column_blocks(self, doc: fitz.Document) -> List[TextBlockItem]:
        all_blocks: List[TextBlockItem] = []
        repeated_lines = self._detect_repeated_header_footer_lines(doc)

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            rect = page.rect
            width, height = rect.width, rect.height
            mid_x = width * 0.48

            raw_blocks = page.get_text("blocks")
            page_items: List[TextBlockItem] = []

            for b in raw_blocks:
                x0, y0, x1, y1, text, _block_no, block_type = b
                if block_type != 0:
                    continue

                if y0 < height * self.HEADER_RATIO or y1 > height * self.FOOTER_RATIO:
                    continue

                cleaned_lines = []
                for line in text.splitlines():
                    s_line = line.strip()
                    if not s_line:
                        continue
                    if s_line.lower() in repeated_lines:
                        continue
                    cleaned_lines.append(s_line)

                cleaned_text = "\n".join(cleaned_lines).strip()
                if not cleaned_text:
                    continue

                if page_idx == 0 and self._is_cover_instruction_block(cleaned_text):
                    continue

                col = 0 if x0 < mid_x else 1
                page_items.append(
                    TextBlockItem(
                        page_index=page_idx,
                        column=col,
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                        text=cleaned_text,
                        page_width=width,
                        page_height=height,
                    )
                )

            page_items.sort(key=lambda item: (item.column, round(item.y0, 1), round(item.x0, 1)))
            all_blocks.extend(page_items)

        return all_blocks

    @staticmethod
    def _detect_repeated_header_footer_lines(doc: fitz.Document) -> set[str]:
        counts: Dict[str, int] = {}
        for page in doc:
            seen_on_page: set[str] = set()
            for b in page.get_text("blocks"):
                if b[6] != 0:
                    continue
                for line in b[4].splitlines():
                    norm = line.strip().lower()
                    if len(norm) > 4:
                        seen_on_page.add(norm)
            for norm in seen_on_page:
                counts[norm] = counts.get(norm, 0) + 1

        threshold = max(3, len(doc) // 2)
        return {line for line, cnt in counts.items() if cnt >= threshold}

    @staticmethod
    def _is_cover_instruction_block(text: str) -> bool:
        norm = text.lower()
        cover_markers = (
            "instruções",
            "instrucoes",
            "você recebeu do fiscal",
            "voce recebeu do fiscal",
            "cartão de respostas",
            "cartao de respostas",
            "duração da prova",
            "duracao da prova",
        )
        return any(m in norm for m in cover_markers)

    def _segment_questions(self, blocks: List[TextBlockItem]) -> List[Dict]:
        questions_raw: List[Dict] = []
        current_q: Optional[Dict] = None
        last_q_num = 0

        for blk in blocks:
            match = self.QUESTION_START_RE.match(blk.text.strip())
            is_new_question = False
            q_num = 0
            remainder = ""

            if match:
                candidate_num = int(match.group(1))
                remainder = match.group(2).strip()
                if 1 <= candidate_num <= 200 and (
                    candidate_num == last_q_num + 1
                    or (last_q_num == 0 and candidate_num == 1)
                    or (candidate_num > last_q_num and candidate_num - last_q_num <= 5)
                ):
                    is_new_question = True
                    q_num = candidate_num

            if is_new_question:
                if current_q:
                    parsed = self._finalize_question_dict(current_q)
                    if parsed:
                        questions_raw.append(parsed)

                last_q_num = q_num
                current_q = {
                    "question_number": q_num,
                    "page_index": blk.page_index,
                    "column": blk.column,
                    "q_y0": blk.y0,
                    "blocks": [(blk, remainder if remainder else "")],
                }
            elif current_q is not None:
                current_q["blocks"].append((blk, blk.text))

        if current_q:
            parsed = self._finalize_question_dict(current_q)
            if parsed:
                questions_raw.append(parsed)

        return questions_raw

    def _finalize_question_dict(self, raw_q: Dict) -> Optional[Dict]:
        full_parts: List[str] = []
        alt_a_y0: Optional[float] = None
        alt_a_col: Optional[int] = None

        for blk, text_piece in raw_q["blocks"]:
            if not text_piece:
                continue
            full_parts.append(text_piece)
            if alt_a_y0 is None and re.search(r"(?:^|\n|\s)(?:\(A\)|A\s*[\)\.\-])\s+", text_piece):
                alt_a_y0 = blk.y0
                alt_a_col = blk.column

        combined_text = "\n".join(full_parts).strip()
        if not combined_text:
            return None

        matches = list(self.ALT_SPLIT_RE.finditer(combined_text))
        if len(matches) < 5:
            return None

        expected_letters = ["A", "B", "C", "D", "E"]
        selected_matches = []
        search_idx = 0

        for letter in expected_letters:
            found = None
            for m in matches[search_idx:]:
                m_letter = (m.group(1) or m.group(2)).upper()
                if m_letter == letter:
                    found = m
                    break
                search_idx += 1
            if not found:
                return None
            selected_matches.append(found)
            search_idx += 1

        statement = combined_text[: selected_matches[0].start()].strip()
        alternatives: Dict[str, str] = {}

        for i, letter in enumerate(expected_letters):
            start_pos = selected_matches[i].end()
            end_pos = (
                selected_matches[i + 1].start()
                if i + 1 < len(selected_matches)
                else len(combined_text)
            )
            alternatives[letter] = combined_text[start_pos:end_pos].strip()

        return {
            "question_number": raw_q["question_number"],
            "page_index": raw_q["page_index"],
            "column": raw_q["column"],
            "q_y0": raw_q["q_y0"],
            "alt_a_y0": alt_a_y0,
            "alt_a_col": alt_a_col,
            "statement": statement,
            "alternatives": alternatives,
        }

    def _check_and_crop_visuals(
        self,
        page: fitz.Page,
        column: int,
        q_y0: float,
        alt_a_y0: float,
        slug_prefix: str,
        question_number: int,
    ) -> Optional[Path]:
        if alt_a_y0 <= q_y0 + 15:
            return None

        page_rect = page.rect
        mid_x = page_rect.width * 0.5
        col_x0 = 0.0 if column == 0 else mid_x - 10
        col_x1 = mid_x + 10 if column == 0 else page_rect.width

        search_rect = fitz.Rect(col_x0, q_y0, col_x1, alt_a_y0)
        visual_rects: List[fitz.Rect] = []

        for img_info in page.get_images(full=True):
            xref = img_info[0]
            for img_rect in page.get_image_rects(xref):
                if img_rect.intersects(search_rect) and img_rect.height > 18 and img_rect.width > 18:
                    visual_rects.append(img_rect & search_rect)

        for drawing in page.get_drawings():
            d_rect: fitz.Rect = drawing.get("rect")
            if not d_rect:
                continue
            if d_rect.width < 12 and d_rect.height > page_rect.height * 0.4:
                continue
            if d_rect.height < 4 or d_rect.width < 4:
                continue
            if d_rect.intersects(search_rect):
                visual_rects.append(d_rect & search_rect)

        if not visual_rects:
            return None

        union_rect = visual_rects[0]
        for r in visual_rects[1:]:
            union_rect |= r

        if union_rect.width < 30 or union_rect.height < 25:
            return None

        clip_rect = fitz.Rect(
            max(col_x0, union_rect.x0 - 8),
            max(q_y0, union_rect.y0 - 8),
            min(col_x1, union_rect.x1 + 8),
            min(alt_a_y0, union_rect.y1 + 8),
        )

        pix = page.get_pixmap(clip=clip_rect, dpi=self.image_dpi)
        image_filename = f"{slug_prefix}_q{question_number}.png"
        image_path = self.images_dir / image_filename
        pix.save(str(image_path))
        return image_path

    def parse_gabarito_pdf(
        self,
        gabarito_path: Path,
        target_type: str = "1",
        cargo_hint: str = "",
    ) -> Dict[int, ValidAnswer]:
        """
        Extrai o gabarito do PDF usando `pdfplumber`, suportando:
        1. Tabelas e blocos segmentados por Cargo e Tipo de Prova (ex: "Assistente Financeiro – Tipo 1"),
           evitando puxar gabaritos de Tipo 2, 3, 4 ou de outros cargos no mesmo PDF.
        2. Tabelas FGV com células mescladas/deslocadas (compactando células não-vazias de questão e gabarito).
        3. Tabelas colunares multi-tipo ([Questão | Tipo 1 | Tipo 2 ...]).
        """
        candidate_sections: List[Tuple[int, Dict[int, ValidAnswer]]] = []
        cargo_tokens = {
            t
            for t in _norm(cargo_hint).split()
            if len(t) > 2
            and t not in {"arquivos", "concurso", "navegacao", "principal", "cargo", "geral", "prova"}
        }

        with pdfplumber.open(gabarito_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""

                # 1. Extração por seções de texto/grade FGV (suporta múltiplos cargos/tipos na mesma página)
                section_maps = self._extract_sectioned_grids_from_text(
                    page_text, target_type=target_type, cargo_tokens=cargo_tokens
                )
                candidate_sections.extend(section_maps)

                # 2. Extração por tabelas do pdfplumber (quando não houve seção textual ou como complemento)
                if not section_maps:
                    if self._is_page_exclusively_other_type(page_text, target_type):
                        continue
                    tables = page.extract_tables() or []
                    table_ans = self._extract_answers_from_tables(tables, target_type)
                    if table_ans:
                        score = self._score_cargo_match(page_text, cargo_tokens)
                        candidate_sections.append((score, table_ans))

        if not candidate_sections:
            return {}

        # Ordena pela maior pontuação de compatibilidade com o Cargo (e maior número de questões)
        candidate_sections.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
        return candidate_sections[0][1]

    def _extract_sectioned_grids_from_text(
        self,
        page_text: str,
        target_type: str,
        cargo_tokens: set[str],
    ) -> List[Tuple[int, Dict[int, ValidAnswer]]]:
        """
        Divide o texto da página de gabarito em seções por cabeçalho de Cargo/Tipo
        (padrão clássico da FGV onde cada tabela é precedida por 'Nome do Cargo – Tipo 1')
        e extrai os pares de linhas (números de questão seguidos de letras A-E / *).
        """
        lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
        if not lines:
            return []

        results: List[Tuple[int, Dict[int, ValidAnswer]]] = []
        current_header_lines: List[str] = []
        current_answers: Dict[int, ValidAnswer] = {}
        pending_q_nums: Optional[List[int]] = None

        def flush_section() -> None:
            nonlocal current_header_lines, current_answers, pending_q_nums
            if current_answers:
                header_text = " ".join(current_header_lines)
                if not self._is_page_exclusively_other_type(header_text, target_type):
                    score = self._score_cargo_match(header_text, cargo_tokens)
                    if re.search(
                        rf"\b(?:tipo|caderno)\s*0?{re.escape(target_type)}\b|\bbranca\b",
                        _norm(header_text),
                    ):
                        score += 10
                    results.append((score, dict(current_answers)))
            current_answers = {}
            pending_q_nums = None

        for line in lines:
            # Verifica se a linha é uma sequência de números de questões (ex: "1 2 3 4 ... 20")
            tokens = line.split()
            q_nums = [self._parse_question_int(t) for t in tokens]
            if len(tokens) >= 3 and all(q is not None for q in q_nums):
                # Se a questão voltou para 1 e já tínhamos respostas, começou um novo bloco/cargo!
                if q_nums[0] == 1 and current_answers:
                    flush_section()
                pending_q_nums = [q for q in q_nums if q is not None]
                continue

            # Verifica se a linha é uma sequência de respostas (ex: "C C E A * B ...")
            ans_tokens = [self._parse_answer_token(t) for t in tokens]
            if (
                pending_q_nums is not None
                and len(tokens) >= 3
                and all(a is not None for a in ans_tokens)
            ):
                for q_n, a_v in zip(pending_q_nums, ans_tokens):
                    if a_v is not None:
                        current_answers[q_n] = a_v
                pending_q_nums = None
                continue

            # Caso contrário, é uma linha de título/cabeçalho de seção
            if current_answers:
                flush_section()
                current_header_lines = [line]
            else:
                current_header_lines.append(line)
                if len(current_header_lines) > 4:
                    current_header_lines = current_header_lines[-4:]

        flush_section()
        return results

    @staticmethod
    def _score_cargo_match(text: str, cargo_tokens: set[str]) -> int:
        if not cargo_tokens:
            return 1
        norm_tokens = set(_norm(text).split())
        return len(cargo_tokens & norm_tokens) * 5

    @staticmethod
    def _is_page_exclusively_other_type(page_text: str, target_type: str) -> bool:
        norm = _norm(page_text)
        has_target = bool(
            re.search(rf"\b(?:tipo|caderno)\s*0?{re.escape(target_type)}\b|\bbranca\b", norm)
        )
        has_other = bool(
            re.search(
                r"\b(?:tipo|caderno)\s*(?:0?[2-9]|ii|iii|iv|v)\b|\b(?:amarela|verde|azul|rosa)\b",
                norm,
            )
        )
        return has_other and not has_target

    def _extract_answers_from_tables(
        self,
        tables: List[List[List[Optional[str]]]],
        target_type: str = "1",
    ) -> Dict[int, ValidAnswer]:
        """
        Extrai respostas de tabelas do `pdfplumber`:
        - Suporta tabelas colunares ([Questão | Tipo 1 | Tipo 2 ...]) identificando a coluna exata do Tipo 1.
        - Suporta grades horizontais FGV mesmo quando `pdfplumber` gera colunas vazias (`None` / `''`)
          deslocadas entre a linha de números e a linha de letras.
        """
        result: Dict[int, ValidAnswer] = {}

        for table in tables:
            cleaned_rows = [
                [(cell or "").strip() for cell in row]
                for row in table
                if row and any((c or "").strip() for c in row)
            ]
            if not cleaned_rows:
                continue

            header_blob = " ".join(" ".join(r) for r in cleaned_rows[:2])
            if self._is_page_exclusively_other_type(header_blob, target_type):
                continue

            # Formato A: Tabela colunar com cabeçalho "Tipo 1"
            col_mapping = self._detect_type1_column_mapping(cleaned_rows[:3], target_type)
            if col_mapping is not None:
                q_col, ans_col, start_row = col_mapping
                for row in cleaned_rows[start_row:]:
                    if max(q_col, ans_col) >= len(row):
                        continue
                    q_val = self._parse_question_int(row[q_col])
                    a_val = self._parse_answer_token(row[ans_col])
                    if q_val is not None and a_val is not None:
                        result[q_val] = a_val
                continue

            # Formato B: Grade horizontal FGV (compactando células não-vazias para evitar desalinhamento de merge)
            for idx in range(len(cleaned_rows) - 1):
                non_empty_q = [c for c in cleaned_rows[idx] if c]
                non_empty_a = [c for c in cleaned_rows[idx + 1] if c]
                if len(non_empty_q) >= 3 and len(non_empty_q) == len(non_empty_a):
                    q_vals = [self._parse_question_int(c) for c in non_empty_q]
                    a_vals = [self._parse_answer_token(c) for c in non_empty_a]
                    if all(q is not None for q in q_vals) and all(a is not None for a in a_vals):
                        for q_v, a_v in zip(q_vals, a_vals):
                            if q_v is not None and a_v is not None:
                                result[q_v] = a_v

        return result

    @staticmethod
    def _detect_type1_column_mapping(
        header_rows: List[List[str]],
        target_type: str = "1",
    ) -> Optional[Tuple[int, int, int]]:
        type1_re = re.compile(
            rf"\b(?:tipo|caderno|prova)\s*0?{re.escape(target_type)}\b|\bbranca\b|^0?{re.escape(target_type)}$",
            re.IGNORECASE,
        )
        other_type_re = re.compile(
            r"\b(?:tipo|caderno|prova)\s*0?[2-9]\b|\b(?:amarela|verde|azul|rosa)\b",
            re.IGNORECASE,
        )
        q_re = re.compile(r"\b(?:quest[aã]o|quest[oõ]es|n[º°o]|item)\b", re.IGNORECASE)

        for r_idx, row in enumerate(header_rows):
            has_any_type_header = any(
                type1_re.search(cell) or other_type_re.search(cell) for cell in row
            )
            if not has_any_type_header:
                continue

            q_col: Optional[int] = None
            t1_col: Optional[int] = None

            for c_idx, cell in enumerate(row):
                if q_col is None and q_re.search(cell):
                    q_col = c_idx
                elif t1_col is None and type1_re.search(cell) and not other_type_re.search(cell):
                    t1_col = c_idx

            if t1_col is not None:
                if q_col is None:
                    q_col = 0 if t1_col > 0 else 1
                return (q_col, t1_col, r_idx + 1)

        return None

    @staticmethod
    def _parse_question_int(cell_text: str) -> Optional[int]:
        cleaned = re.sub(r"^(?:quest[aã]o|q\.?)\s*", "", cell_text.strip(), flags=re.IGNORECASE)
        if re.fullmatch(r"\d{1,3}", cleaned):
            val = int(cleaned)
            if 1 <= val <= 250:
                return val
        return None

    @staticmethod
    def _parse_answer_token(cell_text: str) -> Optional[ValidAnswer]:
        cleaned = cell_text.strip().upper()
        if cleaned in {"A", "B", "C", "D", "E"}:
            return cleaned  # type: ignore[return-value]
        if cleaned in {"*", "ANULADA", "ANULADO"}:
            return "ANULADA"
        return None
