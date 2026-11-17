"""
Módulo de Parsing de Alta Veracidade para Provas e Gabaritos da FGV Conhecimento.

Implementa a solução completa para todos os 6 Gaps identificados na auditoria:
1. Suporte a provas objetivas de 5 alternativas (A–E) e de 4 alternativas (A–D, ex: CFC, DNIT, FBN),
   bem como cadernos de módulos específicos iniciados fora da questão 1 (ex: NAV Brasil Q41..70).
2. Isolamento estrito da capa (Página 1) e detecção de cadernos discursivos/administrativos pelo conteúdo,
   com suporte a fallback de OCR/LLM com Guarda de Veracidade.
3. Extração unificada dos 4 formatos de gabarito da FGV (grades horizontais, grades verticais,
   pares inline hifenizados `1 - D` / `1-B - 2-E` de Diários Oficiais e pares verticais alternados),
   incluindo suporte a questões com asteriscos de nota de rodapé (`16**`) e anulações (`[*]`, `(*)`, `*`).
4. Desambiguação automática de "Cargo Geral" lendo o cargo real na capa/rodapé da prova e merge
   de gabaritos divididos por módulo (Conhecimentos Básicos + Conhecimentos Específicos).
5. Extração em 2 colunas com sub-blocos desmembrados (`_split_merged_block_lines`), trava contra
   números de tabelas internas, limpeza de textos-base pós-alternativa e extração automática
   da disciplina (`discipline`) de cada questão.
6. Integração com Ollama local (`llama3.1:8b`) via `OllamaVeracityClient` (temperature=0.0 + Veracity Guard).
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

from src.ollama_helper import OllamaVeracityClient

logger = logging.getLogger(__name__)

ValidAnswer = Literal["A", "B", "C", "D", "E", "ANULADA"]


def _norm(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_str).strip().lower()


# Palavras-chave canônicas de disciplinas e módulos em provas da FGV
DISCIPLINE_KEYWORDS = (
    "lingua portuguesa",
    "portugues",
    "lingua inglesa",
    "ingles",
    "lingua espanhola",
    "espanhol",
    "raciocinio logico",
    "matematica",
    "estatistica",
    "informatica",
    "tecnologia da informacao",
    "nocoes de informatica",
    "atualidades",
    "etica",
    "administracao",
    "gestao",
    "direito",
    "legislacao",
    "contabilidade",
    "auditoria",
    "economia",
    "financas",
    "orcamento",
    "conhecimentos gerais",
    "conhecimentos basicos",
    "conhecimentos especificos",
    "modulo i",
    "modulo ii",
    "modulo iii",
    "ciencias",
    "historia",
    "geografia",
    "fisica",
    "quimica",
    "biologia",
    "pedagogia",
    "didatica",
    "psicologia",
    "servico social",
    "enfermagem",
    "medicina",
    "odontologia",
    "farmacia",
    "nutricao",
    "fisioterapia",
    "engenharia",
    "arquitetura",
    "biblioteconomia",
    "arquivologia",
    "comunicacao",
    "jornalismo",
    "relacoes publicas",
    "redacao oficial",
    "direitos humanos",
    "criminologia",
    "processo civil",
    "processo penal",
    "processo do trabalho",
    "seguridade social",
    "previdenciario",
    "tributario",
    "financeiro",
    "empresarial",
    "ambiental",
    "eleitoral",
    "internacional",
    "eca",
    "estatuto",
    "regimento interno",
    "realidade",
    "saude publica",
    "sus",
    "politicas publicas",
)


class AlternativesSchema(BaseModel):
    """Schema Pydantic para alternativas da FGV (suporta 5 alternativas A-E ou 4 alternativas A-D)."""

    A: str = Field(..., min_length=1, description="Texto da alternativa A")
    B: str = Field(..., min_length=1, description="Texto da alternativa B")
    C: str = Field(..., min_length=1, description="Texto da alternativa C")
    D: str = Field(..., min_length=1, description="Texto da alternativa D")
    E: Optional[str] = Field(
        default=None,
        description="Texto da alternativa E (opcional em exames de 4 alternativas A-D)",
    )

    @field_validator("A", "B", "C", "D")
    @classmethod
    def clean_required_whitespace(cls, v: str) -> str:
        cleaned = re.sub(r"\s+", " ", v).strip()
        if not cleaned:
            raise ValueError("O texto da alternativa A-D não pode ser vazio.")
        return cleaned

    @field_validator("E")
    @classmethod
    def clean_optional_e(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = re.sub(r"\s+", " ", v).strip()
        return cleaned if cleaned else None


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
    """Parser de alta precisão para provas objetivas (2 colunas) e gabaritos da FGV Conhecimento."""

    HEADER_TOP_CUTOFF = 0.052
    FOOTER_TOP_CUTOFF = 0.935

    # Início de questão: exige que o número esteja em sua própria linha (padrão FGV),
    # ou precedido por "Questão", ou seguido de pontuação/duplo espaço + letra maiúscula/aspas
    QUESTION_START_RE = re.compile(
        r"^(?:(QUEST[ÃA]O)\s+0*(\d{1,3})(?:[ \t]*[\.\-\):]?[ \t]*)(?:\n|$|(?=[A-ZÀ-Ý“\"«]))"
        r"|0*(\d{1,3})[ \t]*([\.\-\)])?[ \t]*(?:\n|$|(?=[A-ZÀ-Ý“\"«])))(.*)$",
        re.IGNORECASE | re.DOTALL,
    )

    # Linha isolada que inicia uma questão dentro de um bloco mesclado
    STANDALONE_Q_LINE_RE = re.compile(
        r"^(?:QUEST[ÃA]O\s+)?0*(\d{1,3})(?:\s*[\.\-\)]\s*$|\s*$)",
        re.IGNORECASE,
    )

    ALT_SPLIT_RE = re.compile(
        r"(?:^|\n|\s)(?:\(([A-E])\)|([A-E])\s*[\)\.\-])\s+",
        re.MULTILINE,
    )

    # Par inline "1 - C", "1 – D", "6-*", "47 - [*]"
    INLINE_PAIR_RE = re.compile(
        r"(?<!\d)(\d{1,3})\s*[-–—:]\s*(\[\*\]|\(\*\)|\*|[A-Ea-e])(?![A-Za-z0-9])"
    )

    def __init__(
        self,
        raw_dir: Path = Path("./data/raw"),
        processed_dir: Path = Path("./data/processed"),
        image_dpi: int = 200,
        use_ollama_fallback: bool = False,
    ) -> None:
        self.raw_dir = Path(raw_dir)
        self.processed_dir = Path(processed_dir)
        self.images_dir = self.processed_dir / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.image_dpi = image_dpi
        self.use_ollama_fallback = use_ollama_fallback
        self.ollama = OllamaVeracityClient()

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
        Desambigua automaticamente o nome real do cargo a partir da capa da prova quando necessário.
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
        raw_cargo_title = metadata.get("cargo_title") or cargo_slug
        exam_type = metadata.get("exam_type") or "Tipo 1"
        combined_slug = f"{concurso_slug}_{cargo_slug}"

        # 0. Desambiguar cargo_title a partir da capa/rodapé da prova quando for "Cargo Geral"
        cargo_title = self._resolve_real_cargo_title(
            prova_path=prova_path,
            raw_cargo_title=raw_cargo_title,
            prova_url=metadata.get("prova_url") or "",
        )

        # 1. Extrair questões da prova em 2 colunas + recortar imagens + extrair disciplinas
        questions = self.parse_prova_pdf(
            prova_path=prova_path,
            slug_prefix=combined_slug,
            answers_map={},
            cargo_title=cargo_title,
        )

        if not questions:
            logger.info(
                "Ignorando %s: nenhuma questão objetiva (A-D/A-E) detectada no PDF.",
                combined_slug,
            )
            return None

        expected_q_nums = {q.question_number for q in questions}

        # 2. Extrair mapa de gabarito (Tipo 1) pareado com o cargo real e as questões da prova
        answers_map: Dict[int, ValidAnswer] = {}
        if gabarito_path.exists():
            answers_map = self.parse_gabarito_pdf(
                gabarito_path,
                target_type="1",
                cargo_hint=cargo_title,
                expected_q_nums=expected_q_nums,
            )
            logger.info(
                "Gabarito extraído (%s): %d respostas mapeadas para Tipo 1.",
                combined_slug,
                len(answers_map),
            )

        # 3. Sincronizar gabarito nas questões extraídas
        for q in questions:
            ans = answers_map.get(q.question_number)
            if ans is not None:
                q.answer = ans
                q.annulled = ans == "ANULADA"

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

    @staticmethod
    def _resolve_real_cargo_title(
        prova_path: Path, raw_cargo_title: str, prova_url: str = ""
    ) -> str:
        """
        Quando `raw_cargo_title` é genérico ("Cargo Geral", "Tipo 1", "Manhã", "Tarde"),
        inspeciona a capa (Página 1) e o rodapé da Página 3 da prova para extrair o nome
        oficial do Cargo impresso pela FGV.
        """
        norm_raw = _norm(raw_cargo_title)
        is_generic = (
            not norm_raw
            or norm_raw in {"cargo geral", "cargo-geral", "tipo 1", "branca", "manha", "tarde"}
            or norm_raw.startswith("arquivos do concurso")
        )
        if not is_generic:
            return raw_cargo_title

        try:
            doc = fitz.open(prova_path)
            try:
                if len(doc) == 0:
                    return raw_cargo_title

                # 1. Tenta extrair do rodapé/cabeçalho de página interna (ex: "Nível Superior – Auditor \n Tipo 1")
                for p_idx in range(min(2, len(doc) - 1), 0, -1):
                    p_txt = doc[p_idx].get_text("text")
                    for line in p_txt.splitlines():
                        s = line.strip()
                        norm_s = _norm(s)
                        if (
                            ("nivel superior" in norm_s or "nivel medio" in norm_s or "nivel fundamental" in norm_s)
                            and ("–" in s or "-" in s)
                            and "pagina" not in norm_s
                        ):
                            parts = re.split(r"\s*[–\-]\s*", s)
                            candidates = [
                                pt.strip()
                                for pt in parts
                                if _norm(pt)
                                not in {"nivel superior", "nivel medio", "nivel fundamental", "manha", "tarde", "tipo 1", "cor branca"}
                                and len(pt.strip()) > 3
                            ]
                            if candidates:
                                return " - ".join(candidates)

                # 2. Tenta extrair da capa (Página 1), entre o turno/nível e "TIPO 1" / "PROVA OBJETIVA"
                cover_lines = [ln.strip() for ln in doc[0].get_text("text").splitlines() if ln.strip()]
                ignore_cover = {
                    "manha", "tarde", "noite", "tipo 1", "tipo 1 - branca", "tipo 1 – branca",
                    "cor branca", "prova objetiva", "prova escrita objetiva", "prova preambular",
                    "nivel superior", "nivel medio", "nivel fundamental", "sua prova",
                    "informacoes gerais", "instrucoes", "tempo", "fgv conhecimento", "fgv projetos",
                }
                extracted_cover: List[str] = []
                for ln in cover_lines[:18]:
                    n_ln = _norm(ln)
                    if any(
                        stop in n_ln
                        for stop in (
                            "sua prova",
                            "informacoes gerais",
                            "alem deste caderno",
                            "voce recebera",
                            "voce recebeu",
                            "tipo 1",
                            "tipo 01",
                            "tipo unico",
                        )
                    ):
                        if extracted_cover:
                            break
                        continue
                    if (
                        n_ln in ignore_cover
                        or len(n_ln) <= 2
                        or re.fullmatch(r"[\d\s\-/\.]+", n_ln)
                        or any(
                            org in n_ln
                            for org in (
                                "concurso publico",
                                "processo seletivo",
                                "edital",
                                "governo do estado",
                                "prefeitura municipal",
                                "secretaria de",
                                "tribunal de",
                                "ministerio publico",
                                "assembleia legislativa",
                                "controladoria",
                                "defensoria publica",
                                "camara municipal",
                                "policia militar",
                            )
                        )
                    ):
                        continue
                    cleaned_ln = re.sub(
                        r"(?i)\b(?:prova\s+objetiva|n[ií]vel\s+(?:superior|m[eé]dio|fundamental)|manh[aã]|tarde)\b",
                        "",
                        ln,
                    ).strip(" –-—")
                    if len(cleaned_ln) >= 4:
                        extracted_cover.append(cleaned_ln)

                if extracted_cover:
                    return " - ".join(extracted_cover[:2])
            finally:
                doc.close()
        except Exception:
            pass

        return raw_cargo_title

    def parse_prova_pdf(
        self,
        prova_path: Path,
        slug_prefix: str,
        answers_map: Optional[Dict[int, ValidAnswer]] = None,
        cargo_title: str = "",
    ) -> List[QuestionSchema]:
        """
        Extrai as questões do caderno de prova usando PyMuPDF (`fitz`),
        ordenando blocos em duas colunas, identificando disciplinas e recortando regiões gráficas.
        """
        answers_map = answers_map or {}
        doc = fitz.open(prova_path)
        try:
            if self._is_discursive_only_pdf(doc):
                return []

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
                disc = rq.get("discipline")

                try:
                    q_schema = QuestionSchema(
                        question_number=q_num,
                        discipline=disc,
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

    @staticmethod
    def _is_discursive_only_pdf(doc: fitz.Document) -> bool:
        """Verifica se o PDF é exclusivamente uma prova discursiva/subjetiva/peça prática."""
        if len(doc) == 0:
            return True
        sample_pages = "\n".join(doc[i].get_text("text") for i in range(min(4, len(doc))))
        # Se houver alternativas (A)..(D) nas primeiras páginas, trata-se de prova objetiva
        if len(re.findall(r"\([A-D]\)", sample_pages)) >= 4:
            return False
        cover_norm = _norm(doc[0].get_text("text"))
        if any(
            k in cover_norm
            for k in (
                "prova discursiva",
                "prova escrita discursiva",
                "prova escrita subjetiva",
                "peca processual",
                "peca tecnica",
                "peca pratica",
                "questoes discursivas",
                "questoes subjetivas",
            )
        ) and not any(
            ok in cover_norm
            for ok in ("questoes objetivas", "multipla escolha", "cinco alternativas", "quatro alternativas", "5 (cinco) opcoes", "4 (quatro) opcoes")
        ):
            return True
        return False

    @staticmethod
    def _is_cover_page(page_text: str) -> bool:
        """Verifica se a página 0 é uma capa de instruções padrão da FGV."""
        norm = _norm(page_text)
        cover_markers = (
            "sua prova",
            "informacoes gerais",
            "instrucoes",
            "voce recebeu do fiscal",
            "voce recebera do fiscal",
            "cartao de respostas",
            "folha de respostas",
            "duracao da prova",
            "periodo disponivel para a realizacao",
            "fraude ou tentativa de fraude",
        )
        return sum(1 for m in cover_markers if m in norm) >= 2

    def _extract_two_column_blocks(self, doc: fitz.Document) -> List[TextBlockItem]:
        """
        Extrai blocos de texto em duas colunas, pulando a capa (Página 1) e
        desmembrando blocos que contenham início de nova questão ou título de disciplina.
        """
        all_blocks: List[TextBlockItem] = []
        repeated_lines = self._detect_repeated_header_footer_lines(doc)

        start_page = 1 if (len(doc) > 1 and self._is_cover_page(doc[0].get_text("text"))) else 0

        for page_idx in range(start_page, len(doc)):
            page = doc[page_idx]
            rect = page.rect
            width, height = rect.width, rect.height
            mid_x = width * 0.48

            raw_blocks = page.get_text("blocks")
            # Detecta se a página possui fatiamento vertical cortando palavras (ex: FBN12 e ALBA onde palavras são cortadas em sílabas de 2-4 letras)
            text_blocks = [b for b in raw_blocks if b[6] == 0 and b[4].strip()]
            has_sliced_words = any(
                5.0 < (b[2] - b[0]) < 25.0
                and len(b[4].strip().splitlines()) >= 4
                and sum(
                    1
                    for ln in b[4].splitlines()
                    if re.fullmatch(r"[A-Za-zÀ-ÿ“”\"']{2,4}", ln.replace("\xa0", " ").strip())
                )
                >= 2
                for b in text_blocks
            )
            if has_sliced_words:
                raw_blocks = self._reconstruct_tiled_page_blocks(page, mid_x)

            page_items: List[TextBlockItem] = []

            for b in raw_blocks:
                x0, y0, x1, y1, text, _block_no, block_type = b
                if block_type != 0:
                    continue

                # Descarta apenas blocos cujo TOPO está no rodapé ou cujo FIM está no cabeçalho superior
                if y1 < height * self.HEADER_TOP_CUTOFF or y0 > height * self.FOOTER_TOP_CUTOFF:
                    continue

                cleaned_lines: List[str] = []
                for line in text.splitlines():
                    # Normaliza espaços não-quebráveis (\xa0) comuns em provas antigas da FGV (ex: ALBA 2014)
                    s_line = line.replace("\xa0", " ").strip()
                    if not s_line:
                        continue
                    low_line = s_line.lower()
                    if low_line in repeated_lines:
                        continue
                    if re.search(
                        r"(?:tipo\s*\d+|cor\s+branca|tipo\s+[uú]nico).*p[áa]gina\s*\d+|^p[áa]gina\s*\d+\s*(?:de\s*\d+)?$",
                        low_line,
                    ):
                        continue
                    cleaned_lines.append(s_line)

                if not cleaned_lines:
                    continue

                if page_idx == 0 and self._is_cover_instruction_block("\n".join(cleaned_lines)):
                    continue

                col = 0 if x0 < mid_x else 1

                # Desmembra blocos onde PyMuPDF uniu o fim da questão anterior com o início da próxima
                # ou uniu um título de disciplina com o número da questão
                sub_chunks = self._split_merged_block_lines(cleaned_lines, y0, y1)
                for chunk_text, c_y0, c_y1 in sub_chunks:
                    page_items.append(
                        TextBlockItem(
                            page_index=page_idx,
                            column=col,
                            x0=x0,
                            y0=c_y0,
                            x1=x1,
                            y1=c_y1,
                            text=chunk_text,
                            page_width=width,
                            page_height=height,
                        )
                    )

            page_items.sort(key=lambda item: (item.column, round(item.y0, 1), round(item.x0, 1)))
            all_blocks.extend(page_items)

        return all_blocks

    @staticmethod
    def _reconstruct_tiled_page_blocks(page: fitz.Page, mid_x: float) -> List[Tuple]:
        """
        Reconstrói linhas horizontais limpas em páginas cujo fluxo PDF foi fatiado em tiras
        verticais pela impressora gráfica da FGV, removendo caracteres duplicados nas bordas.
        """
        raw = page.get_text("rawdict")
        chars_by_col: Dict[int, List[Tuple[float, float, float, float, str]]] = {0: [], 1: []}
        for b in raw.get("blocks", []):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    for ch in sp.get("chars", []):
                        cx0, cy0, cx1, cy1 = ch["bbox"]
                        c = ch["c"].replace("\xa0", " ")
                        col = 0 if cx0 < mid_x else 1
                        chars_by_col[col].append((cx0, cy0, cx1, cy1, c))

        reconstructed: List[Tuple] = []
        for col in (0, 1):
            cs = sorted(chars_by_col[col], key=lambda item: (round(item[1] / 3.0), item[0]))
            lines: List[List[Tuple[float, float, float, float, str]]] = []
            cur_line: List[Tuple[float, float, float, float, str]] = []
            cur_y: Optional[float] = None
            for ch in cs:
                if cur_y is None or abs(ch[1] - cur_y) <= 3.5:
                    cur_line.append(ch)
                    if cur_y is None:
                        cur_y = ch[1]
                else:
                    lines.append(cur_line)
                    cur_line = [ch]
                    cur_y = ch[1]
            if cur_line:
                lines.append(cur_line)

            for idx, ln in enumerate(lines):
                ln.sort(key=lambda item: item[0])
                out_chars: List[str] = []
                prev_x1: Optional[float] = None
                prev_c: Optional[str] = None
                for cx0, cy0, cx1, cy1, c in ln:
                    if prev_x1 is not None and c == prev_c and cx0 < prev_x1 - 0.8:
                        continue
                    if (
                        prev_x1 is not None
                        and (cx0 - prev_x1) > 2.2
                        and (not out_chars or out_chars[-1] != " ")
                        and c != " "
                    ):
                        out_chars.append(" ")
                    out_chars.append(c)
                    prev_x1 = max(prev_x1 or cx1, cx1)
                    prev_c = c
                txt = "".join(out_chars).strip()
                if txt:
                    lx0 = min(c[0] for c in ln)
                    ly0 = min(c[1] for c in ln)
                    lx1 = max(c[2] for c in ln)
                    ly1 = max(c[3] for c in ln)
                    reconstructed.append((lx0, ly0, lx1, ly1, txt + "\n", idx, 0))

        return reconstructed

    @classmethod
    def _split_merged_block_lines(
        cls, lines: List[str], y0: float, y1: float
    ) -> List[Tuple[str, float, float]]:
        """
        Se um bloco de texto contiver múltiplas linhas onde uma linha intermediária é um
        número isolado de questão (ex: `'62'`, `'Questão 15'`) ou um título de disciplina,
        divide o bloco em sub-blocos para que `_segment_questions` reconheça a fronteira.
        """
        if len(lines) <= 1:
            return [(lines[0], y0, y1)]

        split_indices: List[int] = [0]
        for idx in range(1, len(lines)):
            ln = lines[idx]
            if cls.STANDALONE_Q_LINE_RE.match(ln):
                split_indices.append(idx)
            elif cls._looks_like_discipline_header(ln) and (
                idx + 1 < len(lines) and cls.STANDALONE_Q_LINE_RE.match(lines[idx + 1])
            ):
                split_indices.append(idx)

        if len(split_indices) == 1:
            # Verifica também se a primeira linha é uma disciplina e a segunda é o número da questão
            if len(lines) >= 2 and cls._looks_like_discipline_header(lines[0]) and cls.STANDALONE_Q_LINE_RE.match(lines[1]):
                split_indices.append(1)

        if len(split_indices) == 1:
            return [("\n".join(lines), y0, y1)]

        total_lines = len(lines)
        height_span = max(1.0, y1 - y0)
        chunks: List[Tuple[str, float, float]] = []

        for s_i, start_idx in enumerate(split_indices):
            end_idx = split_indices[s_i + 1] if s_i + 1 < len(split_indices) else total_lines
            sub_lines = lines[start_idx:end_idx]
            if not sub_lines:
                continue
            sub_y0 = y0 + (start_idx / total_lines) * height_span
            sub_y1 = y0 + (end_idx / total_lines) * height_span
            chunks.append(("\n".join(sub_lines).strip(), sub_y0, sub_y1))

        return chunks

    @staticmethod
    def _looks_like_discipline_header(text: str) -> bool:
        """Identifica se um bloco/linha curta corresponde a um título de Disciplina ou Módulo da FGV."""
        s = text.strip()
        if not s or "\n" in s or len(s) < 4 or len(s) > 85:
            return False
        if s.endswith((".", ":", ";", "?", "!", ",")):
            return False
        if re.match(r"^(?:\([A-E]\)|[A-E][\)\.\-]|\d)", s, re.I):
            return False
        norm_s = _norm(s)
        if any(
            ignore in norm_s
            for ignore in (
                "pagina",
                "tipo 1",
                "cor branca",
                "fgv",
                "texto ",
                "leia o",
                "analise",
                "assinale",
                "considere",
                "com base",
                "segundo o",
                "de acordo",
                "observe",
                "figura",
                "tabela",
                "grafico",
                "disponivel em",
                "adaptado de",
                "questao",
            )
        ):
            return False
        return any(kw in norm_s for kw in DISCIPLINE_KEYWORDS)

    @staticmethod
    def _detect_repeated_header_footer_lines(doc: fitz.Document) -> set[str]:
        counts: Dict[str, int] = {}
        for page in doc:
            seen_on_page: set[str] = set()
            for b in page.get_text("blocks"):
                if b[6] != 0:
                    continue
                for line in b[4].splitlines():
                    norm = line.replace("\xa0", " ").strip().lower()
                    if len(norm) > 4:
                        seen_on_page.add(norm)
            for norm in seen_on_page:
                counts[norm] = counts.get(norm, 0) + 1

        threshold = max(3, len(doc) // 2)
        return {line for line, cnt in counts.items() if cnt >= threshold}

    @staticmethod
    def _is_cover_instruction_block(text: str) -> bool:
        norm = _norm(text)
        cover_markers = (
            "instrucoes",
            "informacoes gerais",
            "sua prova",
            "voce recebeu do fiscal",
            "voce recebera do fiscal",
            "cartao de respostas",
            "folha de respostas",
            "duracao da prova",
        )
        return any(m in norm for m in cover_markers)

    def _has_complete_alternatives_so_far(self, raw_q: Dict) -> bool:
        """Verifica se `raw_q` já acumulou pelo menos as alternativas (A), (B), (C) e (D)."""
        combined = "\n".join(t for _, t in raw_q["blocks"] if t)
        matches = [
            (m.group(1) or m.group(2)).upper()
            for m in self.ALT_SPLIT_RE.finditer(combined)
        ]
        if len(matches) < 4:
            return False
        # Verifica se A, B, C, D aparecem em ordem
        idx = 0
        for target in ("A", "B", "C", "D"):
            while idx < len(matches) and matches[idx] != target:
                idx += 1
            if idx >= len(matches):
                return False
            idx += 1
        return True

    def _segment_questions(self, blocks: List[TextBlockItem]) -> List[Dict]:
        questions_raw: List[Dict] = []
        current_q: Optional[Dict] = None
        last_q_num = 0
        current_discipline: Optional[str] = None

        for blk in blocks:
            stripped_txt = blk.text.strip()

            # 1. Verifica se o bloco inteiro é um cabeçalho de Disciplina / Módulo
            if self._looks_like_discipline_header(stripped_txt):
                if current_q is None or self._has_complete_alternatives_so_far(current_q):
                    if current_q is not None:
                        parsed = self._finalize_question_dict(current_q)
                        if parsed:
                            questions_raw.append(parsed)
                            last_q_num = parsed["question_number"]
                        current_q = None
                    current_discipline = stripped_txt
                    continue

            match = self.QUESTION_START_RE.match(stripped_txt)
            is_new_question = False
            q_num = 0
            remainder = ""

            if match:
                has_q_word = bool(match.group(1))
                candidate_num = int(match.group(2) or match.group(3))
                punct = (match.group(4) or "").strip()
                remainder = (match.group(5) or "").strip()
                first_line_only_num = bool(
                    re.match(r"^(?:QUEST[ÃA]O\s+)?0*\d{1,3}[ \t]*[\.\-\)]?[ \t]*$", stripped_txt.splitlines()[0], re.I)
                )

                # Se `current_q` já está aberta mas ainda NÃO completou (A)-(D),
                # um número seguido de ponto (ex: "1. item" ou célula de tabela) NÃO é nova questão
                current_incomplete = (
                    current_q is not None and not self._has_complete_alternatives_so_far(current_q)
                )

                # Verifica se o número está alinhado à margem esquerda da coluna (não indentado no meio da coluna)
                col_left_base = 0.0 if blk.column == 0 else blk.page_width * 0.48
                is_near_col_margin = (blk.x0 - col_left_base) < 68.0

                ref_q_num = (
                    current_q["question_number"]
                    if (current_q is not None and not current_incomplete)
                    else last_q_num
                )

                if 1 <= candidate_num <= 220 and is_near_col_margin and (has_q_word or first_line_only_num or punct in {".", "-", ")"}):
                    if ref_q_num == 0 and candidate_num == 1 and (has_q_word or punct != "."):
                        # A Questão 1 real sempre prevalece sobre qualquer texto prévio antes da Q1
                        is_new_question = True
                        q_num = 1
                    elif current_incomplete and not has_q_word:
                        # Enquanto a questão atual ainda não completou (A)-(D), nunca aceita salto > +1
                        # nem item com ponto ("1.") ou fragmento numérico/matemático
                        if (
                            punct != "."
                            and first_line_only_num
                            and candidate_num == current_q["question_number"] + 1
                            and (remainder == "" or (len(remainder) >= 12 and not re.match(r"^[,;:\+\-\*/=0-9]", remainder)))
                        ):
                            is_new_question = True
                            q_num = candidate_num
                        else:
                            is_new_question = False
                    elif ref_q_num == 0:
                        # Primeira questão de caderno específico iniciado fora do 1 (ex: Q21, Q31, Q41)
                        if has_q_word or (punct == "" and first_line_only_num and candidate_num in {11, 16, 21, 26, 31, 36, 41, 46, 51, 61}):
                            is_new_question = True
                            q_num = candidate_num
                    elif candidate_num == ref_q_num + 1:
                        is_new_question = True
                        q_num = candidate_num
                    elif (
                        first_line_only_num
                        and candidate_num > ref_q_num
                        and candidate_num - ref_q_num <= 5
                    ):
                        is_new_question = True
                        q_num = candidate_num

            if is_new_question:
                if current_q:
                    parsed = self._finalize_question_dict(current_q)
                    if parsed:
                        questions_raw.append(parsed)
                        last_q_num = parsed["question_number"]
                    else:
                        # Se current_q não tinha alternativas válidas (A)-(D), e candidate_num
                        # não é 1 nem tem "Questão", verifica se current_q realmente devia ser descartada
                        pass

                current_q = {
                    "question_number": q_num,
                    "discipline": current_discipline,
                    "page_index": blk.page_index,
                    "column": blk.column,
                    "q_y0": blk.y0,
                    "blocks": [(blk, remainder if remainder else "")],
                }
            elif current_q is not None:
                # Se current_q já completou (A)-(E) e aparece um bloco de "Texto II" / "Leia o texto a seguir"
                # para as próximas questões, fecha current_q para não poluir a última alternativa!
                if self._has_complete_alternatives_so_far(current_q) and self._is_next_section_intro_block(stripped_txt):
                    parsed = self._finalize_question_dict(current_q)
                    if parsed:
                        questions_raw.append(parsed)
                        last_q_num = parsed["question_number"]
                    current_q = None
                else:
                    current_q["blocks"].append((blk, blk.text))

        if current_q:
            parsed = self._finalize_question_dict(current_q)
            if parsed:
                questions_raw.append(parsed)

        return questions_raw

    @staticmethod
    def _is_next_section_intro_block(text: str) -> bool:
        """Detecta blocos introdutórios de textos-base ("Texto I", "Texto 2", "Leia o texto a seguir para responder às questões...")."""
        norm = _norm(text)
        if re.match(r"^texto\s+(?:[i1-9v]+|um|dois|tres)\b", norm):
            return True
        if re.search(r"responder\s+as\s+questoes\s+(?:de\s+)?\d+\s+a\s+\d+", norm):
            return True
        if re.match(r"^aten[cç][aã]o:\s*para\s+responder", norm):
            return True
        return False

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
        if len(matches) < 4:
            if self.use_ollama_fallback and len(combined_text) >= 60:
                llm_res = self.ollama.parse_atypical_question_block(
                    raw_text=combined_text,
                    expected_q_num=raw_q["question_number"],
                )
                if llm_res:
                    return {
                        **llm_res,
                        "discipline": raw_q.get("discipline") or llm_res.get("discipline"),
                        "page_index": raw_q["page_index"],
                        "column": raw_q["column"],
                        "q_y0": raw_q["q_y0"],
                        "alt_a_y0": alt_a_y0,
                        "alt_a_col": alt_a_col,
                    }
            return None

        required_letters = ["A", "B", "C", "D"]
        selected_matches = []
        search_idx = 0

        for letter in required_letters:
            found = None
            for m in matches[search_idx:]:
                m_letter = (m.group(1) or m.group(2)).upper()
                if m_letter == letter:
                    found = m
                    break
                search_idx += 1
            if not found:
                if self.use_ollama_fallback and len(combined_text) >= 60:
                    llm_res = self.ollama.parse_atypical_question_block(
                        raw_text=combined_text,
                        expected_q_num=raw_q["question_number"],
                    )
                    if llm_res:
                        return {
                            **llm_res,
                            "discipline": raw_q.get("discipline") or llm_res.get("discipline"),
                            "page_index": raw_q["page_index"],
                            "column": raw_q["column"],
                            "q_y0": raw_q["q_y0"],
                            "alt_a_y0": alt_a_y0,
                            "alt_a_col": alt_a_col,
                        }
                return None
            selected_matches.append(found)
            search_idx += 1

        # Verifica se existe alternativa E após D
        found_e = None
        for m in matches[search_idx:]:
            m_letter = (m.group(1) or m.group(2)).upper()
            if m_letter == "E":
                found_e = m
                break
        if found_e is not None:
            selected_matches.append(found_e)
            active_letters = ["A", "B", "C", "D", "E"]
        else:
            active_letters = ["A", "B", "C", "D"]

        statement = combined_text[: selected_matches[0].start()].strip()
        alternatives: Dict[str, Optional[str]] = {"A": None, "B": None, "C": None, "D": None, "E": None}

        for i, letter in enumerate(active_letters):
            start_pos = selected_matches[i].end()
            end_pos = (
                selected_matches[i + 1].start()
                if i + 1 < len(selected_matches)
                else len(combined_text)
            )
            alt_val = combined_text[start_pos:end_pos].strip()
            if i == len(active_letters) - 1:
                alt_val = self._strip_trailing_section_headers(alt_val)
            alternatives[letter] = alt_val

        return {
            "question_number": raw_q["question_number"],
            "discipline": raw_q.get("discipline"),
            "page_index": raw_q["page_index"],
            "column": raw_q["column"],
            "q_y0": raw_q["q_y0"],
            "alt_a_y0": alt_a_y0,
            "alt_a_col": alt_a_col,
            "statement": statement,
            "alternatives": alternatives,
        }

    @classmethod
    def _strip_trailing_section_headers(cls, last_alt_text: str) -> str:
        """Remove eventuais cabeçalhos de disciplina ou 'Texto II' que tenham ficado na última linha da alternativa."""
        lines = [ln.strip() for ln in last_alt_text.splitlines() if ln.strip()]
        while len(lines) > 1 and (
            cls._looks_like_discipline_header(lines[-1])
            or cls._is_next_section_intro_block(lines[-1])
        ):
            lines.pop()
        return "\n".join(lines).strip()

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

        image_filename = f"{slug_prefix}_q{question_number}.png"
        image_path = self.images_dir / image_filename
        if image_path.exists() and image_path.stat().st_size > 100:
            return image_path

        pix = page.get_pixmap(clip=clip_rect, dpi=self.image_dpi)
        pix.save(str(image_path))
        return image_path

    # =========================================================================
    # PARSER UNIFICADO DE GABARITOS (SUPORTA TODOS OS 4 PADRÕES FGV + MERGE)
    # =========================================================================

    def parse_gabarito_pdf(
        self,
        gabarito_path: Path,
        target_type: str = "1",
        cargo_hint: str = "",
        expected_q_nums: Optional[set[int]] = None,
    ) -> Dict[int, ValidAnswer]:
        """
        Extrai o gabarito do PDF suportando:
        1. Grades Horizontais FGV (`1 2 ... 20` / `A B ... E`).
        2. Grades Verticais FGV (`1\\n2...\\n20\\nA\\nB...\\nE`).
        3. Pares Inline Hifenizados (`1 - C`, `1 – D`, `1-B - 2-E - 6-*`).
        4. Pares Verticais Alternados (`1\\nC\\n2\\nE...`).
        5. Tabelas Colunares Multi-Tipo (`[Questão | Tipo 1 | Tipo 2 ...]`).
        6. Merge automático de seções complementares (Módulo Básico + Específico).
        """
        candidate_sections: List[Tuple[int, str, Dict[int, ValidAnswer]]] = []
        cargo_tokens = {
            t
            for t in _norm(cargo_hint).split()
            if len(t) > 2
            and t
            not in {
                "arquivos",
                "concurso",
                "navegacao",
                "principal",
                "cargo",
                "geral",
                "prova",
                "nivel",
                "superior",
                "medio",
                "fundamental",
                "area",
                "manha",
                "tarde",
            }
        }

        # Coleta texto de cada página via fitz (ultrarrápido)
        fitz_texts: List[str] = []
        try:
            fdoc = fitz.open(gabarito_path)
            fitz_texts = [pg.get_text("text") or "" for pg in fdoc]
            fdoc.close()
        except Exception:
            pass

        for f_text in fitz_texts:
            if f_text.strip():
                candidate_sections.extend(
                    self._extract_sectioned_grids_from_text(
                        f_text, target_type=target_type, cargo_tokens=cargo_tokens
                    )
                )

        if len(fitz_texts) > 1:
            joined_fitz = "\n".join(fitz_texts)
            if joined_fitz.strip():
                candidate_sections.extend(
                    self._extract_sectioned_grids_from_text(
                        joined_fitz, target_type=target_type, cargo_tokens=cargo_tokens
                    )
                )

        # Verifica se o fitz já encontrou casamento 100% completo para `expected_q_nums`
        fitz_complete = False
        if expected_q_nums and candidate_sections:
            for _sc, _hdr, amap in candidate_sections:
                if expected_q_nums.issubset(set(amap.keys())):
                    fitz_complete = True
                    break

        if not fitz_complete:
            try:
                with pdfplumber.open(gabarito_path) as pdf:
                    for p_idx, page in enumerate(pdf.pages):
                        plumber_text = page.extract_text() or ""
                        f_text = fitz_texts[p_idx] if p_idx < len(fitz_texts) else ""

                        if plumber_text.strip():
                            sec_maps = self._extract_sectioned_grids_from_text(
                                plumber_text, target_type=target_type, cargo_tokens=cargo_tokens
                            )
                            candidate_sections.extend(sec_maps)

                        if not self._is_page_exclusively_other_type(plumber_text or f_text, target_type):
                            tables = page.extract_tables() or []
                            table_ans = self._extract_answers_from_tables(tables, target_type)
                            if table_ans:
                                header_ref = (plumber_text or f_text)[:400]
                                score = self._score_cargo_match(header_ref, cargo_tokens)
                                candidate_sections.append((score, header_ref, table_ans))
            except Exception as exc:
                logger.debug("Erro no pdfplumber para %s: %s", gabarito_path, exc)

        if not candidate_sections:
            if self.use_ollama_fallback and fitz_texts:
                full_raw = "\n".join(fitz_texts)
                if len(full_raw.strip()) >= 40:
                    return self.ollama.extract_gabarito_fallback(  # type: ignore[return-value]
                        full_raw, cargo_title=cargo_hint, target_type=target_type
                    )
            return {}

        # Ajusta score pela compatibilidade de quantidade de questões com `expected_q_nums`
        scored_sections: List[Tuple[int, str, Dict[int, ValidAnswer]]] = []
        max_exp_q = max(expected_q_nums) if expected_q_nums else 0
        for base_score, hdr, ans_map in candidate_sections:
            adj_score = base_score
            if expected_q_nums and ans_map:
                overlap = len(expected_q_nums & set(ans_map.keys()))
                excess = len(set(ans_map.keys()) - expected_q_nums)
                max_ans_q = max(ans_map.keys())
                if overlap == len(expected_q_nums) and max_ans_q == max_exp_q:
                    adj_score += 35  # Casamento exato da questão máxima e de 100% das questões extraídas
                elif overlap == len(expected_q_nums) and excess == 0:
                    adj_score += 25
                elif overlap > 0 and max_ans_q == max_exp_q:
                    adj_score += 20
                elif overlap < len(expected_q_nums) and max_ans_q < max_exp_q:
                    adj_score -= 15
            scored_sections.append((adj_score, hdr, ans_map))

        scored_sections.sort(key=lambda item: (item[0], len(item[2])), reverse=True)
        best_score, _best_hdr, best_map = scored_sections[0]
        merged_result = dict(best_map)

        # Se `merged_result` cobrir apenas parte de `expected_q_nums` (ex: 21..80),
        # procura seção complementar de Conhecimentos Básicos/Gerais (ex: 1..20) para mesclar
        if expected_q_nums and len(merged_result) < len(expected_q_nums):
            missing_qs = expected_q_nums - set(merged_result.keys())
            for sc, hdr, sec_map in scored_sections[1:]:
                sec_keys = set(sec_map.keys())
                # Se a seção cobre exatamente (ou parte disjunta) das questões faltantes
                if sec_keys and sec_keys.issubset(missing_qs):
                    merged_result.update(sec_map)
                    missing_qs = expected_q_nums - set(merged_result.keys())
                    if not missing_qs:
                        break

        return merged_result

    def _extract_sectioned_grids_from_text(
        self,
        page_text: str,
        target_type: str,
        cargo_tokens: set[str],
    ) -> List[Tuple[int, str, Dict[int, ValidAnswer]]]:
        """
        Divide o texto da página em seções por cabeçalho de Cargo/Tipo e aplica um parser
        unificado capaz de ler grades horizontais, grades verticais, pares inline (`1 - C`)
        e pares verticais alternados (`1 \\n C`).
        """
        lines = [ln.replace("\xa0", " ").strip() for ln in page_text.splitlines() if ln.strip()]
        if not lines:
            return []

        results: List[Tuple[int, str, Dict[int, ValidAnswer]]] = []
        current_header_lines: List[str] = []
        current_answers: Dict[int, ValidAnswer] = {}
        pending_q_nums: List[int] = []
        pending_ans: List[ValidAnswer] = []

        def sync_pending() -> None:
            nonlocal pending_q_nums, pending_ans, current_answers
            if pending_q_nums and len(pending_q_nums) == len(pending_ans):
                for q_n, a_v in zip(pending_q_nums, pending_ans):
                    current_answers[q_n] = a_v
                pending_q_nums = []
                pending_ans = []

        def flush_section() -> None:
            nonlocal current_header_lines, current_answers, pending_q_nums, pending_ans
            sync_pending()
            if len(current_answers) >= 5:
                header_text = " ".join(current_header_lines)
                if not self._is_page_exclusively_other_type(header_text, target_type):
                    score = self._score_cargo_match(header_text, cargo_tokens)
                    if re.search(
                        rf"\b(?:tipo|caderno|prova)\s*0?{re.escape(target_type)}\b|\bbranca\b|-\s*{re.escape(target_type)}\s*$",
                        _norm(header_text),
                    ):
                        score += 15
                    results.append((score, header_text, dict(current_answers)))
            current_answers = {}
            pending_q_nums = []
            pending_ans = []

        for line in lines:
            # Ignora rodapés e legendas de página no meio da tabela (ex: Diário Oficial ou "* Questão anulada")
            norm_line = _norm(line)
            if (
                re.match(r"^\*?\[?\*?\]?\s*-?\s*quest[aã]o\s+anulada", norm_line)
                or re.match(r"^\*+\s*l[ií]ngua\s+estrangeira", norm_line)
                or re.match(r"^p[aá]gina\s+\d+", norm_line)
                or re.match(r"^conte[uú]do\s*-\s*tipo\s*\d+", norm_line)
                or "diario da justica eletronico" in norm_line
                or "publicacao oficial do tribunal" in norm_line
                or ("campo grande, ano" in norm_line and "edicao" in norm_line)
            ):
                continue

            # Caso 1: Pares inline hifenizados na linha ("1 - D", "1-B - 2-E - 3-A ...")
            inline_matches = list(self.INLINE_PAIR_RE.finditer(line))
            if inline_matches and (
                len(inline_matches) >= 2
                or re.fullmatch(r"\s*\d{1,3}\s*[-–—:]\s*(?:\[\*\]|\(\*\)|\*|[A-Ea-e])\s*", line)
            ):
                for m in inline_matches:
                    q_n = int(m.group(1))
                    a_v = self._parse_answer_token(m.group(2))
                    if 1 <= q_n <= 250 and a_v is not None:
                        if q_n == 1 and len(current_answers) >= 10:
                            flush_section()
                        current_answers[q_n] = a_v
                continue

            tokens = line.split()
            q_nums = [self._parse_question_int(t) for t in tokens]
            ans_tokens = [self._parse_answer_token(t) for t in tokens]

            # Caso 2: Linha contendo exclusivamente números de questão (1 ou mais números)
            if tokens and all(q is not None for q in q_nums):
                clean_qs = [q for q in q_nums if q is not None]
                # Se já tínhamos um par pendente completo ou se voltou para a questão 1 após acumular respostas:
                if pending_q_nums and pending_ans:
                    sync_pending()
                # Ignora número isolado de página (ex: "7" de rodapé do DJE no meio de uma grade de 20 questões)
                if (
                    len(clean_qs) == 1
                    and len(pending_q_nums) >= 10
                    and clean_qs[0] != pending_q_nums[-1] + 1
                    and clean_qs[0] != 1
                ):
                    continue
                if clean_qs[0] == 1 and (len(current_answers) >= 10 or (pending_q_nums and len(pending_q_nums) >= 10)):
                    flush_section()
                pending_q_nums.extend(clean_qs)
                continue

            # Caso 3: Linha contendo exclusivamente tokens de resposta (A-E / * / [*])
            if tokens and all(a is not None for a in ans_tokens):
                clean_as = [a for a in ans_tokens if a is not None]
                if pending_q_nums:
                    pending_ans.extend(clean_as)
                    if len(pending_ans) == len(pending_q_nums):
                        sync_pending()
                    continue

            # Caso 4: Linha de cabeçalho de seção (Cargo / Tipo)
            # Verifica se é um cabeçalho explícito de novo Cargo/Tipo (ex: "Contador - TIPO 2" ou "Analista - 1")
            is_new_section_header = bool(
                re.search(
                    r"\b(?:tipo|caderno|prova)\s*0?[1-9]\b|\b(?:branca|verde|amarela|azul)\b|-\s*[1-4]\s*$",
                    norm_line,
                )
            )
            if is_new_section_header and (current_answers or pending_q_nums):
                flush_section()
                current_header_lines = [line]
            elif current_answers and not pending_q_nums:
                # Se já temos um bloco completo de respostas e aparece um título textual
                flush_section()
                current_header_lines = [line]
            elif not pending_q_nums:
                current_header_lines.append(line)
                if len(current_header_lines) > 5:
                    current_header_lines = current_header_lines[-5:]

        flush_section()
        return results

    @staticmethod
    def _score_cargo_match(text: str, cargo_tokens: set[str]) -> int:
        if not cargo_tokens:
            return 1
        norm_text = _norm(text)
        norm_tokens = set(norm_text.split())
        overlap = len(cargo_tokens & norm_tokens)
        score = overlap * 10
        # Bônus se todos os tokens do cargo estiverem presentes no cabeçalho da tabela
        if overlap == len(cargo_tokens):
            score += 20
        return score

    @staticmethod
    def _is_page_exclusively_other_type(page_text: str, target_type: str) -> bool:
        norm = _norm(page_text)
        has_target = bool(
            re.search(
                rf"\b(?:tipo|caderno|prova)\s*0?{re.escape(target_type)}\b|\bbranca\b|-\s*{re.escape(target_type)}(?:\s|$)",
                norm,
            )
        )
        has_other = bool(
            re.search(
                r"\b(?:tipo|caderno|prova)\s*(?:0?[2-9]|ii|iii|iv|v)\b|\b(?:amarela|verde|azul|rosa)\b|-\s*[2-5](?:\s|$)",
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
        Extrai respostas de tabelas do `pdfplumber` (incluindo células com múltiplos pares `1 – D\\n2 – B`).
        """
        result: Dict[int, ValidAnswer] = {}

        for table in tables:
            cleaned_rows = [
                [(cell or "").replace("\xa0", " ").strip() for cell in row]
                for row in table
                if row and any((c or "").strip() for c in row)
            ]
            if not cleaned_rows:
                continue

            header_blob = " ".join(" ".join(r) for r in cleaned_rows[:2])
            if self._is_page_exclusively_other_type(header_blob, target_type):
                continue

            # Verifica pares inline dentro das células (ex: mps12)
            inline_found = 0
            for row in cleaned_rows:
                for cell in row:
                    for m in self.INLINE_PAIR_RE.finditer(cell):
                        q_v = int(m.group(1))
                        a_v = self._parse_answer_token(m.group(2))
                        if 1 <= q_v <= 250 and a_v is not None:
                            result[q_v] = a_v
                            inline_found += 1
            if inline_found >= 10:
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

            # Formato B: Grade horizontal FGV
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
        """Converte string de número de questão em int, tolerando asteriscos de nota de rodapé (ex: '16**')."""
        cleaned = re.sub(r"^(?:quest[aã]o|q\.?)\s*", "", cell_text.strip(), flags=re.IGNORECASE)
        cleaned = cleaned.rstrip("*").strip()
        if re.fullmatch(r"\d{1,3}", cleaned):
            val = int(cleaned)
            if 1 <= val <= 250:
                return val
        return None

    @staticmethod
    def _parse_answer_token(cell_text: str) -> Optional[ValidAnswer]:
        """Converte token de resposta em A-E ou ANULADA (suportando `*`, `[*]`, `(*)`, `ANULADA`)."""
        cleaned = cell_text.strip().upper().strip("[]()")
        if cleaned in {"A", "B", "C", "D", "E"}:
            return cleaned  # type: ignore[return-value]
        if cleaned in {"*", "**", "ANULADA", "ANULADO", "X", "-"}:
            if cleaned in {"*", "**", "ANULADA", "ANULADO"}:
                return "ANULADA"
        return None
