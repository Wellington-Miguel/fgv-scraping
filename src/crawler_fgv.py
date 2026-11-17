"""
Módulo de Coleta (Crawler) Resiliente para o Portal FGV Conhecimento.

Utiliza Playwright (Chromium headless) para contornar renderização dinâmica,
mapeia documentos PDF aos seus respectivos cargos/áreas subindo na árvore DOM,
aplica filtros de segurança contra provas discursivas/orais/práticas/administrativas
e garante o download estrito do Caderno Tipo 1 (Branca) pareado com seu
Gabarito Definitivo/Preliminar.

Requisito prévio:
    playwright install chromium
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright
from tqdm import tqdm

logger = logging.getLogger(__name__)

FGV_BASE_URL = "https://conhecimento.fgv.br"
FGV_CONCURSOS_URL = f"{FGV_BASE_URL}/concursos"

# Termos de exclusão obrigatórios (discursivas, redações, sentenças, provas orais, títulos, etc.)
EXCLUDED_KEYWORDS = (
    "discursiva",
    "discursivo",
    "redacao",
    "redação",
    "peca pratica",
    "peça prática",
    "parecer",
    "prova pratica",
    "prova prática",
    "escrita e pratica",
    "escrita pratica",
    "sentenca",
    "sentença",
    "prova oral",
    "prova de tribuna",
    "prova de titulos",
    "avaliacao de titulos",
    "estudo de caso",
    "questao discursiva",
    "questão discursiva",
    "espelho",
    "resposta esperada",
    "padrao de resposta",
    "padrão de resposta",
)

# Termos administrativos que NUNCA são cadernos de prova nem tabelas de gabarito
ADMIN_KEYWORDS = (
    "edital",
    "retificacao",
    "retificação",
    "homologacao",
    "homologação",
    "resultado",
    "convocacao",
    "convocação",
    "comunicado",
    "cronograma",
    "isencao",
    "isenção",
    "inscricao",
    "inscrição",
    "deferimento",
    "indeferimento",
    "interposicao",
    "interposição",
    "recurso",
    "demanda",
    "concorrencia",
    "concorrência",
    "local de prova",
    "locais de prova",
    "comissao examinadora",
    "banca examinadora",
    "membros da banca",
    "sessao publica",
    "sorteio",
    "ordem de arguicao",
    "pontos sorteados",
    "ponto para a prova",
    "programa especifico",
    "relacao definitiva",
    "relacao preliminar",
    "portaria",
    "aviso",
    "sub judice",
    "anulacao",
)

# Rótulos genéricos de layout do Drupal que devem ser ignorados ao extrair nome do cargo
GENERIC_DOM_HEADERS = {
    "navegacao principal",
    "arquivos do concurso",
    "arquivos",
    "documentos",
    "provas e gabaritos",
    "editais",
    "publicacoes",
    "menu principal",
    "busca",
    "concursos",
    "em andamento",
    "encerrados",
}


def normalize_text(text: str) -> str:
    """Remove acentos, converte para minúsculas e normaliza espaços."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_str).strip().lower()


def slugify(text: str, max_length: int = 80) -> str:
    """Converte um título de concurso ou cargo em um slug seguro para diretórios."""
    norm = normalize_text(text)
    slug = re.sub(r"[^a-z0-9]+", "-", norm).strip("-")
    if not slug:
        slug = "cargo-geral"
    return slug[:max_length].rstrip("-")


@dataclass
class PdfCandidate:
    """Representa um link de PDF encontrado na página do concurso."""

    title: str
    url: str
    cargo_raw: str
    doc_category: str  # "prova" | "gabarito_definitivo" | "gabarito_preliminar"
    exam_type: Optional[str] = None  # Ex: "Tipo 1", "Branca", "Tipo Único"


@dataclass
class CargoBundle:
    """Agrupamento de Prova Tipo 1 + Gabarito para um Cargo específico."""

    concurso_title: str
    concurso_slug: str
    concurso_url: str
    cargo_title: str
    cargo_slug: str
    exam_type: str = "Tipo 1"
    exam_color: str = "Branca"
    prova_url: Optional[str] = None
    prova_title: Optional[str] = None
    gabarito_url: Optional[str] = None
    gabarito_title: Optional[str] = None
    gabarito_status: Optional[str] = None  # "definitivo" | "preliminar"
    downloaded_files: Dict[str, str] = field(default_factory=dict)


class FGVCrawler:
    """Crawler resiliente para coleta de provas e gabaritos da FGV Conhecimento."""

    def __init__(
        self,
        raw_dir: Path = Path("./data/raw"),
        headless: bool = True,
        timeout_ms: int = 45000,
        max_retries: int = 3,
        max_pages: Optional[int] = None,
    ) -> None:
        self.raw_dir = Path(raw_dir)
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.max_retries = max_retries
        self.max_pages = max_pages
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept": "application/pdf,application/xhtml+xml,text/html,*/*;q=0.8",
            }
        )

    def run(
        self,
        limit: Optional[int] = None,
        specific_url: Optional[str] = None,
        on_cargo_downloaded=None,
    ) -> List[Path]:
        """
        Executa o fluxo de coleta de concursos da FGV.
        Se `on_cargo_downloaded` for fornecido, executa o callback imediatamente após
        baixar cada cargo (permitindo parsing incremental em tempo real).
        """
        saved_dirs: List[Path] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(
                user_agent=self.session.headers["User-Agent"],
                viewport={"width": 1366, "height": 900},
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)

            try:
                if specific_url:
                    concurso_urls = [specific_url]
                else:
                    concurso_urls = self.discover_concursos(page, limit=limit)

                logger.info("Total de concursos selecionados para coleta: %d", len(concurso_urls))

                for idx, url in enumerate(concurso_urls, start=1):
                    logger.info("[%d/%d] Inspecionando concurso: %s", idx, len(concurso_urls), url)
                    try:
                        bundles = self.extract_concurso_bundles(page, url)
                        if not bundles:
                            logger.info(
                                "Nenhum caderno de prova objetiva (Tipo 1) disponível em %s", url
                            )
                            continue

                        for bundle in tqdm(
                            bundles,
                            desc=f"Baixando cargos ({bundles[0].concurso_slug[:28]})",
                            leave=False,
                        ):
                            cargo_dir = self._download_bundle(bundle)
                            if cargo_dir:
                                saved_dirs.append(cargo_dir)
                                if on_cargo_downloaded is not None:
                                    try:
                                        on_cargo_downloaded(cargo_dir)
                                    except Exception as cb_err:
                                        logger.error(
                                            "Erro no callback pós-download para %s: %s",
                                            cargo_dir,
                                            cb_err,
                                        )
                    except Exception as exc:
                        logger.error("Erro ao processar concurso %s: %s", url, exc, exc_info=True)
            finally:
                context.close()
                browser.close()

        return saved_dirs

    def discover_concursos(self, page: Page, limit: Optional[int] = None) -> List[str]:
        """
        Navega em todas as listagens de concursos da FGV (geral, em andamento e encerrados)
        usando Playwright, paginando até esgotar todos os concursos disponíveis.
        """
        urls: List[str] = []
        seen: set[str] = set()
        listing_roots = [
            FGV_CONCURSOS_URL,
            f"{FGV_CONCURSOS_URL}/em-andamento",
            f"{FGV_CONCURSOS_URL}/encerrados",
        ]

        for root_url in listing_roots:
            page_num = 0
            while self.max_pages is None or page_num < self.max_pages:
                target_url = root_url if page_num == 0 else f"{root_url}?page={page_num}"
                logger.info(
                    "Vasculhando listagem (página %d, acumulado: %d concursos): %s",
                    page_num + 1,
                    len(urls),
                    target_url,
                )

                if not self._navigate_with_retry(page, target_url):
                    break

                try:
                    page.wait_for_selector("a[href*='/concursos/']", timeout=15000)
                except PlaywrightTimeoutError:
                    logger.warning("Timeout aguardando seletor de concursos na página %d", page_num)
                    break

                html = page.content()
                soup = BeautifulSoup(html, "html.parser")

                found_on_page = 0
                for a_tag in soup.select("a[href*='/concursos/']"):
                    href = a_tag.get("href", "").strip()
                    if not href:
                        continue

                    full_url = (
                        urljoin(FGV_BASE_URL, href).split("#")[0].split("?")[0].rstrip("/")
                    )
                    parsed = urlparse(full_url)
                    path_parts = [p for p in parsed.path.split("/") if p]

                    if len(path_parts) == 2 and path_parts[0] == "concursos":
                        slug = path_parts[1].lower()
                        if slug in {
                            "em-andamento",
                            "encerrados",
                            "proximos",
                            "todos",
                            "concursos",
                        }:
                            continue
                        if full_url not in seen:
                            seen.add(full_url)
                            urls.append(full_url)
                            found_on_page += 1
                            if limit and len(urls) >= limit:
                                return urls[:limit]

                if found_on_page == 0:
                    break

                page_num += 1
                if limit and len(urls) >= limit:
                    return urls[:limit]

        return urls[:limit] if limit else urls

    def extract_concurso_bundles(self, page: Page, concurso_url: str) -> List[CargoBundle]:
        """
        Acessa a página de um concurso específico, expande eventuais acordeões/abas
        e extrai os documentos PDF associando-os ao seu Cargo/Área e filtrando
        estritamente Prova Objetiva Tipo 1 + Gabarito.
        """
        if not self._navigate_with_retry(page, concurso_url):
            return []

        try:
            page.evaluate(
                """() => {
                    document.querySelectorAll('details').forEach(d => d.open = true);
                    document.querySelectorAll('.collapse').forEach(c => c.classList.add('show'));
                }"""
            )
        except Exception:
            pass

        html = page.content()
        soup = BeautifulSoup(html, "html.parser")

        concurso_title = self._extract_concurso_title(soup, concurso_url)
        concurso_slug = slugify(urlparse(concurso_url).path.split("/")[-1] or concurso_title)

        # Restringe busca à área principal de conteúdo se existir
        main_container = (
            soup.select_one("main, .region-content, #block-system-main, article") or soup
        )

        candidates: List[PdfCandidate] = []
        for a_tag in main_container.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not self._is_pdf_link(href):
                continue

            full_pdf_url = urljoin(concurso_url, href)
            link_text = " ".join(a_tag.stripped_strings)
            filename = Path(urlparse(full_pdf_url).path).name
            combined_label = f"{link_text} {filename}".strip()

            # 1. Filtro de Segurança: Ignorar Discursivas / Redações / Orais / Sentenças
            if self._is_excluded_document(combined_label):
                logger.debug("Ignorando documento discursivo/excluído: %s", combined_label)
                continue

            # 2. Classificar entre Prova Objetiva (Tipo 1 / Branca) e Gabarito
            classification = self._classify_pdf(link_text, filename)
            if not classification:
                continue

            # 3. Subir na árvore DOM para identificar o Cargo/Área associado ao link
            cargo_raw = self._resolve_cargo_from_dom(a_tag, link_text)
            if self._is_excluded_document(cargo_raw):
                continue

            doc_category, exam_type = classification
            candidates.append(
                PdfCandidate(
                    title=link_text or filename,
                    url=full_pdf_url,
                    cargo_raw=cargo_raw,
                    doc_category=doc_category,
                    exam_type=exam_type,
                )
            )

        return self._pair_candidates_by_cargo(
            concurso_title=concurso_title,
            concurso_slug=concurso_slug,
            concurso_url=concurso_url,
            candidates=candidates,
        )

    def _navigate_with_retry(self, page: Page, url: str) -> bool:
        """Navega para uma URL com tentativas de repetição em caso de falha de rede."""
        for attempt in range(1, self.max_retries + 1):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=6000)
                except PlaywrightTimeoutError:
                    pass
                return True
            except Exception as exc:
                logger.warning(
                    "Tentativa %d/%d falhou ao abrir %s: %s",
                    attempt,
                    self.max_retries,
                    url,
                    exc,
                )
                time.sleep(1.5 * attempt)
        logger.error("Falha definitiva ao carregar URL: %s", url)
        return False

    @staticmethod
    def _extract_concurso_title(soup: BeautifulSoup, fallback_url: str) -> str:
        for selector in ("h1.page-header", "h1.title", "main h1", "h1", "title"):
            el = soup.select_one(selector)
            if el and el.get_text(strip=True):
                return el.get_text(strip=True)
        return urlparse(fallback_url).path.split("/")[-1]

    @staticmethod
    def _is_pdf_link(href: str) -> bool:
        parsed = urlparse(href)
        return parsed.path.lower().endswith(".pdf")

    @staticmethod
    def _is_excluded_document(text: str) -> bool:
        """Verifica se o texto contém indicadores de prova discursiva, redação, peça, sentença ou oral."""
        norm = normalize_text(text)
        return any(keyword in norm for keyword in EXCLUDED_KEYWORDS)

    def _resolve_cargo_from_dom(self, a_tag: Tag, link_text: str) -> str:
        """
        Sobe na árvore do DOM a partir do link `<a>` para descobrir o Cargo/Área,
        filtrando cabeçalhos genéricos de navegação do Drupal ("Navegação principal",
        "Arquivos do concurso", etc.).
        """
        context_parts: List[str] = []

        # 1. Contexto de linha de tabela <tr>
        tr = a_tag.find_parent("tr")
        if tr:
            tds = tr.find_all(["td", "th"])
            for td in tds:
                if a_tag in td.descendants:
                    continue
                cell_text = " ".join(td.stripped_strings)
                if (
                    cell_text
                    and not self._looks_like_date_only(cell_text)
                    and normalize_text(cell_text) not in GENERIC_DOM_HEADERS
                ):
                    context_parts.append(cell_text)
                    break

            table = tr.find_parent("table")
            if table:
                caption = table.find("caption")
                if caption and caption.get_text(strip=True):
                    cap_txt = caption.get_text(" ", strip=True)
                    if normalize_text(cap_txt) not in GENERIC_DOM_HEADERS:
                        context_parts.insert(0, cap_txt)
                else:
                    prev_heading = table.find_previous(["h3", "h4", "h5"])
                    if prev_heading and prev_heading.get_text(strip=True):
                        h_txt = prev_heading.get_text(" ", strip=True)
                        if normalize_text(h_txt) not in GENERIC_DOM_HEADERS:
                            context_parts.insert(0, h_txt)

        # 2. Contexto de item de lista ou cabeçalho de seção
        if not context_parts:
            li = a_tag.find_parent("li")
            if li:
                li_clone_text = self._extract_text_outside_link(li, a_tag)
                if (
                    li_clone_text
                    and not self._looks_like_date_only(li_clone_text)
                    and normalize_text(li_clone_text) not in GENERIC_DOM_HEADERS
                ):
                    context_parts.append(li_clone_text)

            prev_heading = a_tag.find_previous(["h3", "h4", "h5"])
            if prev_heading:
                heading_text = prev_heading.get_text(" ", strip=True)
                if heading_text and normalize_text(heading_text) not in GENERIC_DOM_HEADERS:
                    context_parts.insert(0, heading_text)

        # 3. Extração a partir do próprio texto do link
        cleaned_link_cargo = self._strip_doc_metadata_from_title(link_text)
        if cleaned_link_cargo and normalize_text(cleaned_link_cargo) not in GENERIC_DOM_HEADERS:
            context_parts.append(cleaned_link_cargo)

        deduped: List[str] = []
        for part in context_parts:
            cleaned = self._strip_doc_metadata_from_title(part)
            norm_c = normalize_text(cleaned)
            if (
                cleaned
                and norm_c not in GENERIC_DOM_HEADERS
                and norm_c not in {normalize_text(p) for p in deduped}
            ):
                deduped.append(cleaned)

        return " - ".join(deduped) if deduped else "Cargo Geral"

    @staticmethod
    def _extract_text_outside_link(container: Tag, link: Tag) -> str:
        texts: List[str] = []
        for child in container.strings:
            if child.find_parent("a") == link:
                continue
            stripped = child.strip(" -–—:\t\n\r")
            if stripped:
                texts.append(stripped)
        return " ".join(texts).strip()

    @staticmethod
    def _looks_like_date_only(text: str) -> bool:
        norm = normalize_text(text)
        cleaned = re.sub(r"\b(publicado|data|em|atualizado)\b", "", norm)
        cleaned = re.sub(r"[\d/\-.:,\s]+", "", cleaned)
        return len(cleaned) < 3

    @staticmethod
    def _strip_doc_metadata_from_title(text: str) -> str:
        """
        Remove termos como 'Gabarito Definitivo', 'Prova Objetiva', 'Tipo 1', 'Branca',
        datas e cabeçalhos genéricos para isolar o nome do Cargo/Área.
        """
        if not text:
            return ""
        cleaned = text
        patterns = [
            r"(?i)\b(?:navega[çc][aã]o\s+principal|arquivos\s+do\s+concurso)\b",
            r"(?i)\bgabarito[s]?\s+(?:oficial\s+)?(?:definitivo[s]?|preliminar(?:es)?|oficial|ap[oó]s\s+recursos)\b",
            r"(?i)\bgabarito[s]?\b",
            r"(?i)\bcaderno[s]?\s+de\s+(?:prova[s]?|quest[oõ]es)\b",
            r"(?i)\bda\s+prova\s+(?:escrita\s+)?objetiva(?:\s+seletiva|\s+preliminar)?\b",
            r"(?i)\bprova[s]?\s+(?:escrita\s+)?objetiva[s]?(?:\s+seletiva|\s+preliminar)?\b",
            r"(?i)\bprova[s]?\s+preambular\b",
            r"(?i)\bprova[s]?\s+de\s+m[uú]ltipla\s+escolha\b",
            r"(?i)\b(?:tipo|caderno)\s*(?:0?1|i|um|[2-9]|ii|iii|iv|v|unico|único)\b",
            r"(?i)\bcor\s+branca\b",
            r"(?i)\b(?:branca|verde|amarela|azul|rosa|cinza)\b",
            r"(?i)\b(?:publicado|atualizado|retificado)\s+em\b",
            r"\b\d{2}/\d{2}/\d{4}\b",
            r"\b\d{2}h(?:\d{2})?\b",
            r"(?i)\.pdf$",
        ]
        for pat in patterns:
            cleaned = re.sub(pat, " ", cleaned)
        cleaned = re.sub(r"[\(\)\[\]]", " ", cleaned)
        cleaned = re.sub(r"(?:\s*[-–—:/|]\s*)+", " - ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—:/|")
        return cleaned

    def _classify_pdf(self, link_text: str, filename: str) -> Optional[tuple[str, Optional[str]]]:
        """
        Classifica um link PDF estritamente em:
        - ('gabarito_definitivo', exam_type)
        - ('gabarito_preliminar', exam_type)
        - ('prova', 'Tipo 1') -> APENAS se for Caderno de Prova Objetiva (Tipo 1 / Branca / Único).
        Rejeita editais, comunicados, atas, listas, avisos e provas de outros tipos.
        """
        combined = f"{link_text} {filename}"
        norm = normalize_text(combined)

        if self._is_excluded_document(combined):
            return None

        exam_type = self._detect_exam_type(norm)

        # Caso 1: Gabarito (verificado ANTES de ADMIN_KEYWORDS para não bloquear
        # links oficiais como "Edital de Gabarito Definitivo" ou "Comunicado - Gabarito")
        if "gabarito" in norm:
            if exam_type == "OUTRO_TIPO":
                return None
            if any(
                k in norm
                for k in (
                    "interposicao",
                    "resposta aos recursos",
                    "respostas aos recursos",
                    "respostas publicas",
                )
            ):
                return None

            if any(k in norm for k in ("definitivo", "oficial", "apos recurso", "pos recurso", "final")):
                return ("gabarito_definitivo", exam_type or "Tipo 1")
            return ("gabarito_preliminar", exam_type or "Tipo 1")

        # Bloqueia editais, avisos, portarias, convocações, bancas, etc. para cadernos de prova
        if any(admin_kw in norm for admin_kw in ADMIN_KEYWORDS):
            return None

        # Caso 2: Caderno de Prova Objetiva
        # Não aceita links que sejam apenas "Nota", "Programa", "Pontos", etc.
        if re.search(r"\b(nota|programa|ponto|pontos|relacao|lista|ata|termo)\b", norm):
            return None

        if exam_type == "OUTRO_TIPO":
            logger.debug("Ignorando prova de outro tipo (não Tipo 1): %s", combined)
            return None

        has_explicit_prova_marker = bool(
            re.search(
                r"\b(caderno\s+de\s+prova|caderno\s+de\s+questoes|prova\s+objetiva|"
                r"prova\s+escrita\s+objetiva|prova\s+preambular|prova\s+avaliativa|"
                r"multipla\s+escolha|conhecimentos\s+gerais|conhecimentos\s+especificos)\b",
                norm,
            )
            or exam_type == "Tipo 1"
            or re.search(r"prova[_-]?(?:objetiva|tipo|caderno|0?1)", norm)
        )

        # No portal FGV, quando o link está na seção de provas, às vezes o título do link
        # é o próprio nome do cargo + "(Manhã)" ou o filename contém "prova" / "tipo1" / "caderno"
        has_filename_prova_hint = bool(
            re.search(r"(?:prova|caderno|tipo[-_]?0?1|branca|objetiva)", normalize_text(filename))
        )

        if has_explicit_prova_marker or has_filename_prova_hint:
            return ("prova", exam_type or "Tipo 1")

        return None

    @staticmethod
    def _detect_exam_type(norm_text: str) -> Optional[str]:
        """
        Detecta o tipo de prova no texto normalizado:
        - Retorna 'OUTRO_TIPO' se encontrar Tipo 2..9, II..V, Amarela, Verde, Azul, Rosa.
        - Retorna 'Tipo 1' se encontrar Tipo 1, Tipo 01, Tipo I, Caderno 1 ou Branca.
        - Retorna 'Tipo Único' se indicar caderno único.
        """
        other_type_pattern = (
            r"\b(?:tipo|caderno|banca|grupo)\s*(?:0?[2-9]|ii|iii|iv|v|b|c|d|e)\b"
            r"|\b(?:amarel[ao]|verde|azul|rosa|cinza)\b"
            r"|\btipo[-_]?0?[2-9]\b"
        )
        if re.search(other_type_pattern, norm_text):
            return "OUTRO_TIPO"

        type1_pattern = (
            r"\b(?:tipo|caderno)\s*(?:0?1|i|um|a)\b"
            r"|\bbranc[ao]\b"
            r"|\btipo[-_]?0?1\b"
        )
        if re.search(type1_pattern, norm_text):
            return "Tipo 1"

        if "unico" in norm_text:
            return "Tipo Único"

        return None

    def _pair_candidates_by_cargo(
        self,
        concurso_title: str,
        concurso_slug: str,
        concurso_url: str,
        candidates: List[PdfCandidate],
    ) -> List[CargoBundle]:
        """
        Agrupa os candidatos por Cargo/Área e pareia a Prova Tipo 1 com o melhor
        Gabarito correspondente (priorizando Gabarito Definitivo sobre Preliminar).
        """
        by_cargo: Dict[str, Dict[str, List[PdfCandidate]]] = {}
        cargo_titles: Dict[str, str] = {}

        for cand in candidates:
            c_slug = slugify(cand.cargo_raw or "cargo-geral")
            if c_slug not in by_cargo:
                by_cargo[c_slug] = {
                    "prova": [],
                    "gabarito_definitivo": [],
                    "gabarito_preliminar": [],
                }
                cargo_titles[c_slug] = cand.cargo_raw or "Cargo Geral"
            by_cargo[c_slug][cand.doc_category].append(cand)

        global_def = [c for c in candidates if c.doc_category == "gabarito_definitivo"]
        global_pre = [c for c in candidates if c.doc_category == "gabarito_preliminar"]

        bundles: List[CargoBundle] = []
        for c_slug, groups in by_cargo.items():
            provas = groups["prova"]
            if not provas:
                continue

            provas_sorted = sorted(
                provas,
                key=lambda p: 0 if p.exam_type == "Tipo 1" else 1,
            )
            chosen_prova = provas_sorted[0]

            chosen_gab: Optional[PdfCandidate] = None
            gab_status: Optional[str] = None

            if groups["gabarito_definitivo"]:
                chosen_gab = groups["gabarito_definitivo"][0]
                gab_status = "definitivo"
            elif groups["gabarito_preliminar"]:
                chosen_gab = groups["gabarito_preliminar"][0]
                gab_status = "preliminar"
            elif global_def:
                chosen_gab = self._find_best_matching_gabarito(cargo_titles[c_slug], global_def)
                gab_status = "definitivo"
            elif global_pre:
                chosen_gab = self._find_best_matching_gabarito(cargo_titles[c_slug], global_pre)
                gab_status = "preliminar"

            bundles.append(
                CargoBundle(
                    concurso_title=concurso_title,
                    concurso_slug=concurso_slug,
                    concurso_url=concurso_url,
                    cargo_title=cargo_titles[c_slug],
                    cargo_slug=c_slug,
                    exam_type=chosen_prova.exam_type or "Tipo 1",
                    exam_color="Branca",
                    prova_url=chosen_prova.url,
                    prova_title=chosen_prova.title,
                    gabarito_url=chosen_gab.url if chosen_gab else None,
                    gabarito_title=chosen_gab.title if chosen_gab else None,
                    gabarito_status=gab_status,
                )
            )

        return bundles

    @staticmethod
    def _find_best_matching_gabarito(
        cargo_title: str, gabaritos: List[PdfCandidate]
    ) -> PdfCandidate:
        norm_cargo = normalize_text(cargo_title)
        cargo_tokens = {
            t
            for t in norm_cargo.split()
            if len(t) > 2 and t not in {"cargo", "geral", "prova", "objetiva", "tipo"}
        }
        is_cargo_cf = "formacao" in norm_cargo

        best = gabaritos[0]
        best_score = -999
        for gab in gabaritos:
            norm_gab = normalize_text(f"{gab.cargo_raw} {gab.title} {Path(gab.url).name}")
            gab_tokens = set(norm_gab.split())
            score = len(cargo_tokens & gab_tokens) * 5

            # Evita parear Gabarito de Curso/Programa de Formação com Prova Objetiva regular
            if ("curso de formacao" in norm_gab or "programa de formacao" in norm_gab) and not is_cargo_cf:
                score -= 25

            # Penaliza gabarito específico de outro cargo entre parênteses quando não bate com o cargo atual
            for paren_match in re.findall(r"\(([^)]+)\)", gab.title.lower()):
                norm_p = normalize_text(paren_match)
                if (
                    len(norm_p) > 4
                    and not any(
                        k in norm_p
                        for k in ("retific", "atualiz", "manha", "tarde", "tipo", "branca", "edital", "reaplic", "reabert")
                    )
                    and not re.search(r"\d", norm_p)
                    and norm_p not in norm_cargo
                ):
                    score -= 15

            if score > best_score:
                best_score = score
                best = gab
        return best

    def _download_bundle(self, bundle: CargoBundle) -> Optional[Path]:
        """
        Baixa `prova_tipo1.pdf`, `gabarito.pdf` e grava `metadata.json` em:
        `./data/raw/{slug_concurso}/{slug_cargo}/`
        """
        if not bundle.prova_url:
            return None

        target_dir = self.raw_dir / bundle.concurso_slug / bundle.cargo_slug
        target_dir.mkdir(parents=True, exist_ok=True)

        prova_path = target_dir / "prova_tipo1.pdf"
        if not self._download_file(bundle.prova_url, prova_path):
            return None
        bundle.downloaded_files["prova"] = str(prova_path.as_posix())

        if bundle.gabarito_url:
            gabarito_path = target_dir / "gabarito.pdf"
            if self._download_file(bundle.gabarito_url, gabarito_path):
                bundle.downloaded_files["gabarito"] = str(gabarito_path.as_posix())
        else:
            logger.warning(
                "Gabarito não encontrado para %s / %s",
                bundle.concurso_slug,
                bundle.cargo_slug,
            )

        metadata = {
            **asdict(bundle),
            "exam_type_suffix": "tipo1",
            "exam_type_mapped": bundle.exam_type,
            "exam_color_mapped": bundle.exam_color,
            "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        metadata_path = target_dir / "metadata.json"
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target_dir

    def _download_file(self, url: str, dest: Path) -> bool:
        """Realiza o download resiliente de um arquivo PDF verificando assinatura mágica."""
        if dest.exists() and dest.stat().st_size > 1024:
            logger.debug("Arquivo já existe, pulando download: %s", dest)
            return True

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.get(url, stream=True, timeout=30)
                response.raise_for_status()

                chunks = []
                for chunk in response.iter_content(chunk_size=16384):
                    if chunk:
                        chunks.append(chunk)
                content = b"".join(chunks)

                if not content.startswith(b"%PDF"):
                    logger.warning("Conteúdo baixado de %s não é um PDF válido.", url)
                    return False

                dest.write_bytes(content)
                return True
            except requests.RequestException as exc:
                logger.warning(
                    "Erro de rede (%d/%d) ao baixar %s: %s",
                    attempt,
                    self.max_retries,
                    url,
                    exc,
                )
                time.sleep(1.5 * attempt)

        # Fallback oficial via Internet Archive (Wayback Machine) para links legados netstorage.fgv.br
        if "netstorage.fgv.br" in url:
            wb_url = f"https://web.archive.org/web/2023id_/{url}"
            try:
                resp = self.session.get(wb_url, timeout=30)
                if resp.status_code == 200 and resp.content.startswith(b"%PDF"):
                    dest.write_bytes(resp.content)
                    logger.info("Recuperado PDF legado via Wayback Machine: %s", wb_url)
                    return True
            except Exception as wb_exc:
                logger.debug("Fallback Wayback Machine falhou para %s: %s", url, wb_exc)

        logger.error("Falha definitiva ao baixar PDF: %s", url)
        return False
