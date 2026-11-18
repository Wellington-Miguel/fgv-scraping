"""
Módulo de Consolidação Final, Saneamento e Enriquecimento Taxonômico da Base FGV.

Executa as 5 etapas finais para deixar o banco de dados 100% pronto para produção (Site/App):
1. Restauração do gabarito oficial A-D do 2º Curso de Formação do DNIT (`dnit23`).
2. Reprocessamento direcionado das provas com buracos de sequência (beneficiando-se do filtro
   corrigido de cabeçalho/rodapé que preserva alternativas recorrentes como '(A) I, apenas.')
   e desmembramento cirúrgico de questões engolidas na última alternativa.
3. Higienização completa dos `cargo_title` truncados ou com prefixos do Drupal ('Navegação principal').
4. Normalização taxonômica das 472 strings de `discipline` em uma taxonomia canônica limpa e
   classificação de `module` ('Conhecimentos Básicos' vs. 'Conhecimentos Específicos').
5. Enriquecimento de todos os 1.499 JSONs com metadados para filtros da aplicação:
   `year`, `organ`, `uf`, `level`, `question_type`, `module` e `question_hash` (deduplicação).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from src.parser_fgv import FGVParser

logger = logging.getLogger(__name__)


def _norm(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_str).strip().lower()


# =============================================================================
# 1. MAPEAMENTO EXPLÍCITO DE CARGOS GENÉRICOS ("cargo-geral" / "navegacao-principal")
# =============================================================================
EXPLICIT_CARGO_TITLES: Dict[str, str] = {
    "adab24_cargo-geral": "Fiscal Estadual Agropecuário",
    "agemg22_cargo-geral": "Procurador do Estado",
    "agenersa2022_cargo-geral": "Analista Técnico",
    "agesan25_cargo-geral": "Agente Administrativo",
    "alba_cargo-geral": "Técnico de Nível Superior - Assessoria Legislativa",
    "amazul_cargo-geral": "Engenheiro / Analista (Conhecimentos Básicos)",
    "bancodonordeste_cargo-geral": "Analista Bancário",
    "cbmam21_cargo-geral": "Soldado Bombeiro Militar",
    "cbmerj23_cargo-geral": "Soldado Bombeiro Militar",
    "cfc_cargo-geral": "Bacharel em Ciências Contábeis (Exame de Suficiência)",
    "cfopmesp26_cargo-geral": "Aluno-Oficial da Polícia Militar (CFO-PMESP)",
    "cgepb23_arquivos-do-concurso-curso-de-formacao": "Auditor de Contas Públicas (Curso de Formação)",
    "cgepb23_cargo-geral": "Auditor de Contas Públicas - Auditoria de Obras Públicas",
    "cgesc22_cargo-geral": "Auditor do Estado",
    "cgmrj23_cargo-geral": "Contador",
    "compesa_cargo-geral": "Analista de Gestão / Engenheiro",
    "concursoprefsjc2025ed1_cargo-geral": "Agente Educador",
    "conselhotutelardf_cargo-geral": "Conselheiro Tutelar do Distrito Federal",
    "csjt23_cargo-geral": "Juiz do Trabalho Substituto (ENAMAT)",
    "dpdf_navegacao-principal": "Defensor Público de Segunda Categoria",
    "dperj2026_cargo-geral": "Defensor Público Substituto",
    "dpgems21_cargo-geral": "Defensor Público Substituto",
    "dpgerj2021_cargo-geral": "Defensor Público Substituto",
    "dpgerj2023_cargo-geral": "Defensor Público Substituto",
    "dpmt_cargo-geral": "Analista - Administrador",
    "enam20241_cargo-geral": "Exame Nacional da Magistratura (ENAM)",
    "enam20242_cargo-geral": "Exame Nacional da Magistratura (ENAM)",
    "enam20251_cargo-geral": "Exame Nacional da Magistratura (ENAM)",
    "enam20252_cargo-geral": "Exame Nacional da Magistratura (ENAM)",
    "enam20261_cargo-geral": "Exame Nacional da Magistratura (ENAM)",
    "enac20251_cargo-geral": "Exame Nacional dos Cartórios (ENAC)",
    "enac20252_cargo-geral": "Exame Nacional dos Cartórios (ENAC)",
    "enac20261_cargo-geral": "Exame Nacional dos Cartórios (ENAC)",
    "fempar23_cargo-geral": "Vestibular de Medicina",
    "fempar24_cargo-geral": "Vestibular de Medicina",
    "fempar25_cargo-geral": "Vestibular de Medicina",
    "fhemig23_cargo-geral": "Analista de Gestão e Assistência à Saúde (AGAS)",
    "fms2024_cargo-geral": "Vestibular de Medicina",
    "funarte_navegacao-principal": "Assistente Administrativo",
    "gestorsegurancario_cargo-geral": "Gestor de Segurança Municipal",
    "iphan24_cargo-geral": "Analista I",
    "mpgo23_cargo-geral": "Promotor de Justiça Substituto",
    "mprj2019_cargo-geral": "Analista do Ministério Público - Área Administrativa",
    "mprj2025_cargo-geral": "Analista do Ministério Público",
    "mpsc22_cargo-geral": "Auxiliar do Ministério Público",
    "mre_cargo-geral": "Oficial de Chancelaria",
    "mre_tipo-a": "Oficial de Chancelaria (Curso de Preparação)",
    "nittrans25_cargo-geral": "Assistente Administrativo",
    "pbhadministrativo23_cargo-geral": "Agente Executivo Governamental",
    "pcrn20_navegacao-principal-prova-escrita-objetiva": "Delegado de Polícia Civil Substituto",
    "pgmcmrio_cargo-geral": "Procurador do Município do Rio de Janeiro",
    "pmespsargento24_cargo-geral": "Sargento PM (CFS)",
    "pmespsoldado24_cargo-geral": "Soldado PM de 2ª Classe",
    "pmespsoldado25_cargo-geral": "Soldado PM de 2ª Classe",
    "pmesqos2022_cargo-geral": "2º Tenente Dentista PM Estagiário",
    "pmn20_cargo-geral": "Arquiteto",
    "pmsa22_cargo-geral": "Agente de Políticas Públicas e Gestão Governamental (APPGG)",
    "prefeituradecuiaba2016_cargo-geral": "Auditor Fiscal Tributário da Receita Municipal",
    "prefsjc-administrativo23_cargo-geral": "Assistente em Gestão Municipal",
    "prefsjc-educacao23_cargo-geral": "Professor I",
    "prefsjc-procurador_cargo-geral": "Procurador Municipal",
    "prefsjc-sa-c3-bade23_cargo-geral": "Assistente Técnico em Saúde - Técnico em Enfermagem",
    "prefsjc23_cargo-geral": "Guarda Civil Municipal",
    "pssminc24_cargo-geral": "Técnico de Complexidade Intelectual",
    "psstjba23_cargo-geral": "Conciliador",
    "psstjgo2022_cargo-geral": "Juiz Leigo",
    "psstjsc25_cargo-geral": "Juiz Leigo",
    "seadap22_cargo-geral": "Pedagogo",
    "seappr25_cargo-geral": "Auditor Fiscal",
    "seducsp23_cargo-geral": "Professor de Ensino Fundamental e Médio - Educação Profissional",
    "seducsp24_arquivos-do-concurso": "Coordenador de Gestão Pedagógica",
    "seducsp24_cargo-geral": "Diretor Escolar / Diretor de Escola",
    "seducsp25_arquivos-do-concurso": "Professor de Ensino Fundamental e Médio",
    "seducsp25_cargo-geral": "Professor de Ensino Fundamental e Médio",
    "seducsp26edbasica_cargo-geral": "Professor de Ensino Fundamental e Médio - Artes",
    "seducsp26tecnico_cargo-geral": "Professor de Educação Profissional Técnica",
    "seemg23_cargo-geral": "Analista Educacional (ANE)",
    "sefazes21_cargo-geral": "Auditor Fiscal da Receita Estadual",
    "sefmg22_cargo-geral": "Auditor Fiscal da Receita Estadual - Auditoria e Fiscalização",
    "semevitoria24_cargo-geral": "Professor de Educação Básica I (PEB I) - Educação Infantil",
    "seplag_cargo-geral": "Analista de Políticas Públicas e Gestão Governamental (APPGG)",
    "smacanaacarajas24_cargo-geral": "Agente de Serviços Administrativos",
    "sme-sp16_cargo-geral": "Professor de Ensino Fundamental II e Médio - Arte",
    "smesaolourencodamata24_cargo-geral": "Professor I - Anos Iniciais",
    "smfniteroi_cargo-geral": "Fiscal de Tributos",
    "smfrj-apo_cargo-geral": "Analista de Planejamento e Orçamento (APO)",
    "smfrj-fr_cargo-geral": "Fiscal de Rendas",
    "stn_cargo-geral": "Auditor Federal de Finanças e Controle - Área Econômico-Financeira",
    "tceba23_cargo-geral": "Auditor Estadual de Controle Externo",
    "tcerr24_cargo-geral": "Auditor de Controle Externo",
    "tceto22_cargo-geral": "Auditor de Controle Externo - Ciências Contábeis",
    "tjap21_cargo-geral": "Juiz de Direito Substituto",
    "tjes23_cargo-geral": "Juiz Substituto",
    "tjgo2023_cargo-geral": "Juiz Substituto",
    "tjgo2025_cargo-geral": "Juiz Substituto",
    "tjms22_cargo-geral": "Analista Judiciário - Área Fim",
    "tjms25_cargo-geral": "Analista Judiciário - Área Fim",
    "tjmsjuiz23_cargo-geral": "Juiz Substituto",
    "tjmsjuiz25_cargo-geral": "Juiz Substituto",
    "tjmtjuiz24_cargo-geral": "Juiz Substituto",
    "tjpe22_cargo-geral": "Juiz Substituto",
    "tjpe24_cargo-geral": "Juiz Substituto",
    "tjpe26_cargo-geral": "Juiz Substituto",
    "tjpr2023_cargo-geral": "Juiz Substituto",
    "tjpr25_cargo-geral": "Juiz Substituto",
    "tjrr24_cargo-geral": "Analista Judiciário - Apoio à Gestão",
    "tjrs25_cargo-geral": "Técnico do Poder Judiciário - Área Administrativo-Judiciária",
    "tjsc2023_cargo-geral": "Juiz Substituto",
    "tjscjuiz22_cargo-geral": "Juiz Substituto",
    "tjscjuiz24_cargo-geral": "Juiz Substituto",
    "tjscjuiz25_cargo-geral": "Juiz Substituto",
    "tjse24_cargo-geral": "Juiz Substituto",
    "tjsenotarial23_cargo-geral": "Outorga de Delegações Notariais e Registrais - Provimento",
    "tjseservidor23_cargo-geral": "Técnico Judiciário - Área Administrativa/Judiciária",
    "tjto22_cargo-geral": "Contador / Distribuidor",
    "tjto25_cargo-geral": "Juiz Substituto",
    "trf1_cargo-geral": "Juiz Federal Substituto",
    "trf1juiz25_cargo-geral": "Juiz Federal Substituto",
    "trf1servidor24_cargo-geral": "Analista Judiciário - Área Judiciária",
    "trf2juiz_cargo-geral": "Juiz Federal Substituto",
    "trf5juiz_cargo-geral": "Juiz Federal Substituto",
    "trf6_cargo-geral": "Juiz Federal Substituto",
    "trt13_cargo-geral": "Técnico Judiciário - Área Administrativa",
    "trt16_cargo-geral": "Técnico Judiciário - Área Administrativa",
    "trt24_cargo-geral": "Analista Judiciário - Área Administrativa",
    "vestibularfamema22_cargo-geral": "Vestibular de Medicina e Enfermagem",
}


def clean_cargo_title(stem: str, raw_title: str) -> str:
    """Padroniza e limpa o título do cargo, removendo resíduos do Drupal ou cabeçalhos de estado."""
    if stem in EXPLICIT_CARGO_TITLES:
        return EXPLICIT_CARGO_TITLES[stem]

    cleaned = re.sub(
        r"(?i)^(?:navega[cç][aã]o\s+principal|arquivos\s+do\s+concurso|provas\s+e\s+gabaritos)\s*[-–—:]?\s*",
        "",
        raw_title or "",
    ).strip()

    cleaned = re.sub(
        r"(?i)^(?:do\s+estado\s+d[eao]\s+[a-zà-ÿ\s]+|do\s+munic[ií]pio\s+d[eao]\s+[a-zà-ÿ\s]+|de\s+minas\s+gerais|mato\s+grosso\s+do\s+sul|grande\s+do\s+sul)\s*[-–—]\s*",
        "",
        cleaned,
    ).strip()

    if not cleaned or _norm(cleaned) in {"cargo geral", "cargo-geral", "tipo 1", "navegacao principal"}:
        # Extrai a partir do slug do cargo
        _, c_slug = stem.split("_", 1)
        cleaned = c_slug.replace("-", " ").title()

    return cleaned


# =============================================================================
# 2. NORMALIZADOR TAXONÔMICO CANÔNICO DE DISCIPLINAS
# =============================================================================

def normalize_discipline_and_module(
    raw_disc: Optional[str],
    prev_valid_disc: Optional[str],
    cargo_title: str,
) -> Tuple[str, str, bool]:
    """
    Normaliza `raw_disc` para uma taxonomia canônica limpa de ~50 disciplinas da FGV.
    Retorna `(canonical_discipline, module, is_valid_header)`.
    Se `raw_disc` for um fragmento espúrio de frase/URL/citação, retorna `is_valid_header=False`
    e herda `prev_valid_disc`!
    """
    s = re.sub(r"\s+", " ", (raw_disc or "")).strip()
    n = _norm(s)

    # 1. Detecta fragmentos espúrios (frases de enunciado, URLs, citações de livros)
    is_spurious = (
        not n
        or "http" in n
        or "www." in n
        or ".htm" in n
        or ".gov.br" in n
        or ".com.br" in n
        or "disponivel em" in n
        or "fiscalizacao de contratos" in n
        or "realidade politica e economica global" in n
        or "dimensoes de sua economia" in n
        or "bernaldo nao e professor" in n
        or "substituicao / eliminacao" in n
        or "ramos, graciliano" in n
        or "vidas secas" in n
        or "pds sepetiba" in n
        or "gestao coordenada de fronteiras" in n
        or "concurso publico para a fundacao biblioteca nacional" in n
        or len(s) > 95
    )
    if is_spurious and "lingua portuguesa" not in n:
        fallback = prev_valid_disc or "Conhecimentos Específicos"
        mod = _infer_module(fallback)
        return (fallback, mod, False)

    # 2. Mapeamento direto para a Taxonomia Canônica FGV
    canonical: Optional[str] = None

    if any(
        k in n
        for k in (
            "lingua portuguesa",
            "interpretacao de texto",
            "gramatica",
            "redacao oficial",
            "portugues",
        )
    ):
        canonical = "Redação Oficial" if "redacao oficial" in n else "Língua Portuguesa"
    elif any(k in n for k in ("lingua inglesa", "ingles")):
        canonical = "Língua Inglesa"
    elif any(k in n for k in ("lingua espanhola", "espanhol")):
        canonical = "Língua Espanhola"
    elif any(k in n for k in ("lingua francesa", "frances")):
        canonical = "Língua Francesa"
    elif any(
        k in n
        for k in (
            "raciocinio logico",
            "logico-matematico",
            "logico matematico",
            "logico-quantitativo",
            "rlm",
        )
    ):
        canonical = "Raciocínio Lógico-Matemático"
    elif "matematica financeira" in n:
        canonical = "Matemática Financeira"
    elif "estatistica" in n:
        canonical = "Estatística"
    elif n in {"matematica", "de ensino da matematica"} or "matematica" in n:
        canonical = "Matemática"
    elif any(
        k in n
        for k in (
            "noçoes de informatica",
            "nocoes de informatica",
            "informatica basica",
            "nocoes basicas de informatica",
            "informatica",
        )
    ) and "tecnologia da informacao" not in n:
        canonical = "Informática"
    elif any(
        k in n
        for k in (
            "tecnologia da informacao",
            "banco de dados",
            "dados",
            "analista de sistema",
            "programador",
            "engenharia de software",
            "seguranca da informacao",
            "protecao de dados",
        )
    ):
        canonical = "Tecnologia da Informação"
    elif n == "de mato grosso" or "geografia de mato grosso" in n or "historia e geografia de mato grosso" in n:
        canonical = "História e Geografia de Mato Grosso"
    elif "grosso (lei n" in n or "estatuto dos servidores" in n:
        canonical = "Legislação Estadual / Institucional"
    elif "rondonia" in n:
        canonical = "História e Geografia de Rondônia"
    elif "tocantins" in n:
        canonical = "História e Geografia do Tocantins"
    elif "amapa" in n:
        canonical = "História e Geografia do Amapá"
    elif "amazonas" in n:
        canonical = "Legislação e Realidade do Amazonas"
    elif any(k in n for k in ("atualidades", "realidade brasileira", "realidades brasileira")):
        canonical = "Atualidades"
    elif any(k in n for k in ("direito constitucional", "constituicao federal")):
        canonical = "Direito Constitucional"
    elif any(k in n for k in ("direito administrativo", "nocoes de correicao", "correicao")):
        canonical = "Direito Administrativo"
    elif any(k in n for k in ("direito tributario", "legislacao tributaria", "credito tributario")):
        canonical = "Direito Tributário"
    elif any(k in n for k in ("direito financeiro", "financas publicas", "orcamentaria", "afo", "financeiro)")):
        canonical = "Administração Financeira e Orçamentária (AFO)"
    elif any(k in n for k in ("processo civil", "processual civil")):
        canonical = "Direito Processual Civil"
    elif "direito civil" in n:
        canonical = "Direito Civil"
    elif any(k in n for k in ("processo penal", "processual penal")):
        canonical = "Direito Processual Penal"
    elif any(k in n for k in ("direito penal", "penal e criminologia", "criminologia")):
        canonical = "Direito Penal e Criminologia"
    elif any(k in n for k in ("direito do trabalho", "processo do trabalho", "trabalhista")):
        canonical = "Direito e Processo do Trabalho"
    elif any(k in n for k in ("direito empresarial", "direito comercial", "da concorrencia")):
        canonical = "Direito Empresarial"
    elif any(k in n for k in ("direito eleitoral", "eleitoral")):
        canonical = "Direito Eleitoral"
    elif any(k in n for k in ("direito previdenciario", "seguridade social")):
        canonical = "Direito Previdenciário"
    elif any(k in n for k in ("direito ambiental", "legislacao ambiental", "sustentabilidade")):
        canonical = "Direito Ambiental e Sustentabilidade"
    elif any(k in n for k in ("notarial", "registral")):
        canonical = "Direito Notarial e Registral"
    elif any(k in n for k in ("crianca e do adolescente", "eca")):
        canonical = "Direito da Criança e do Adolescente"
    elif "consumidor" in n:
        canonical = "Direito do Consumidor"
    elif any(k in n for k in ("direitos humanos", "etica", "acessibilidade", "cidadania")):
        canonical = "Direitos Humanos, Ética e Cidadania"
    elif any(k in n for k in ("direito internacional", "servico consular", "relacoes exteriores")):
        canonical = "Direito Internacional e Serviço Consular"
    elif "urbanistico" in n:
        canonical = "Direito Urbanístico"
    elif any(k in n for k in ("nocoes de direito", "fundamentos de direito", "direito")):
        canonical = "Noções de Direito"
    elif any(k in n for k in ("contabilidade publica", "setor publico")):
        canonical = "Contabilidade Pública"
    elif "contabilidade" in n:
        canonical = "Contabilidade Geral"
    elif any(k in n for k in ("auditoria", "controle interno", "controle externo")):
        canonical = "Auditoria e Controle"
    elif any(k in n for k in ("economia", "mercado de capitais")):
        canonical = "Economia e Finanças"
    elif any(
        k in n
        for k in (
            "administracao publica",
            "gestao governamental",
            "politicas publicas",
            "administracao",
            "gestao",
            "agente administrativo",
        )
    ) and "medicina" not in n and "saude" not in n:
        canonical = "Administração Geral e Pública"
    elif any(
        k in n
        for k in (
            "sus",
            "saude publica",
            "legislacao da saude",
            "legislacao de saude",
            "políticas públicas de saúde",
        )
    ):
        canonical = "Legislação do SUS e Saúde Pública"
    elif any(k in n for k in ("medicina", "medico", "clinica medica", "cirurgia", "pediatria")):
        canonical = "Medicina"
    elif "enfermagem" in n:
        canonical = "Enfermagem"
    elif any(k in n for k in ("odontologia", "dentista", "saude bucal")):
        canonical = "Odontologia"
    elif any(k in n for k in ("farmacia", "farmaceutico")):
        canonical = "Farmácia"
    elif "psicologia" in n:
        canonical = "Psicologia"
    elif "servico social" in n:
        canonical = "Serviço Social"
    elif any(k in n for k in ("nutricao", "fisioterapia", "fonoaudiologia", "terapia ocupacional", "veterinario", "defesa sanitaria")):
        canonical = "Ciências da Saúde"
    elif any(
        k in n
        for k in (
            "pedagogic",
            "didatica",
            "educacional",
            "educacao",
            "praticas educativas",
            "ensino",
        )
    ):
        canonical = "Pedagogia e Legislação Educacional"
    elif any(k in n for k in ("engenharia", "engenheiro", "arquitetura", "eletrica", "elétricas", "saneamento", "ambiental")):
        canonical = "Engenharia e Arquitetura"
    elif "biblioteconomia" in n or "bibliotecario" in n or "arquivologia" in n:
        canonical = "Biblioteconomia e Arquivologia"
    elif "fisica" in n:
        canonical = "Física"
    elif "quimica" in n:
        canonical = "Química"
    elif "biologia" in n or "ciencias" in n:
        canonical = "Biologia e Ciências"
    elif "historia" in n:
        canonical = "História"
    elif "geografia" in n:
        canonical = "Geografia"
    elif any(
        k in n
        for k in (
            "legislacao institucional",
            "legislacao especifica",
            "legislacao basica",
            "nocoes de legislacao",
            "legislacao",
            "regimento interno",
        )
    ):
        canonical = "Legislação Específica e Institucional"
    elif any(k in n for k in ("conhecimentos basicos", "conhecimentos gerais", "modulo i")):
        canonical = "Conhecimentos Básicos / Gerais"
    elif any(
        k in n
        for k in (
            "conhecimentos especificos",
            "especificos",
            "modulo ii",
            "modulo iii",
            "avancados",
            "habilitacao",
        )
    ):
        canonical = "Conhecimentos Específicos"
    else:
        canonical = "Conhecimentos Específicos"

    return (canonical, _infer_module(canonical), True)


def specialize_generic_discipline(
    canonical_disc: str,
    cargo_title: str,
    statement: str,
) -> Tuple[str, str]:
    """
    Quando a disciplina capturada do cabeçalho do caderno é genérica
    ('Conhecimentos Específicos' ou 'Conhecimentos Básicos / Gerais'),
    especializa a disciplina combinando o `cargo_title` higienizado e o `statement`.
    """
    if canonical_disc not in {"Conhecimentos Específicos", "Conhecimentos Básicos / Gerais"}:
        return (canonical_disc, _infer_module(canonical_disc))

    nc = _norm(cargo_title)
    ns = _norm(statement)

    # 1. Para Conhecimentos Específicos, primeiro verifica a especialidade explícita do cargo
    if canonical_disc == "Conhecimentos Específicos":
        if any(k in nc for k in ("medico", "medicina", "psiquiatra", "pediatra", "cardiologista", "ginecologista", "obstetra", "cirurgiao", "anestesiologista", "ortopedista", "neurologista", "oftalmologista", "dermatologista", "endocrinologista", "geriatra", "hematologista", "infectologista", "nefrologista", "oncologista", "otorrinolaringologista", "pneumologista", "proctologista", "radiologista", "reumatologista", "urologista", "legista")):
            return ("Medicina", "Conhecimentos Específicos")
        if any(k in nc for k in ("enfermeiro", "enfermagem")):
            return ("Enfermagem", "Conhecimentos Específicos")
        if any(k in nc for k in ("odontologo", "odontologia", "cirurgiao dentista", "dentista", "auxiliar de odontologia")):
            return ("Odontologia", "Conhecimentos Específicos")
        if any(k in nc for k in ("farmaceutico", "farmacia", "bioquimico")):
            return ("Farmácia", "Conhecimentos Específicos")
        if any(k in nc for k in ("psicologo", "psicologia")):
            return ("Psicologia", "Conhecimentos Específicos")
        if any(k in nc for k in ("assistente social", "servico social", "assistencia social")):
            return ("Serviço Social", "Conhecimentos Específicos")
        if any(k in nc for k in ("nutricionista", "nutricao", "fisioterapeuta", "fisioterapia", "fonoaudiologo", "fonoaudiologia", "terapeuta ocupacional", "terapia ocupacional", "veterinario", "medicina veterinaria", "biomedico", "biomedicina", "educacao fisica")):
            return ("Ciências da Saúde", "Conhecimentos Específicos")
        if any(k in nc for k in ("contador", "contabilidade", "ciencias contabeis", "contabil")):
            return ("Contabilidade Geral", "Conhecimentos Específicos")
        if any(k in nc for k in ("economista", "economia", "ciencias economicas")):
            return ("Economia e Finanças", "Conhecimentos Específicos")
        if any(k in nc for k in ("estatistico", "estatistica")):
            return ("Estatística", "Conhecimentos Específicos")
        if any(k in nc for k in ("tecnologia da informacao", "analista de sistemas", "analise de sistemas", "ciencia da computacao", "engenharia de software", "engenheiro de software", "informatica", "desenvolvimento de sistemas", "infraestrutura de ti", "seguranca da informacao", "banco de dados", "redes", "programador", "processamento de dados")):
            return ("Tecnologia da Informação", "Conhecimentos Específicos")
        if any(k in nc for k in ("engenheiro", "engenharia", "arquiteto", "arquitetura", "projetista", "edificacoes", "estradas", "geoprocessamento", "agrimensura", "cartografia", "geologia", "geologo", "eletricista", "eletronica", "mecanica", "mecatronica", "telecomunicacoes", "saneamento")):
            return ("Engenharia e Arquitetura", "Conhecimentos Específicos")
        if any(k in nc for k in ("bibliotecario", "biblioteconomia", "biblioteconomista", "arquivista", "arquivologia")):
            return ("Biblioteconomia e Arquivologia", "Conhecimentos Específicos")
        if any(k in nc for k in ("comunicacao social", "jornalista", "jornalismo", "publicidade", "relacoes publicas", "audiovisual", "cinegrafista", "radialista", "revisor", "redator")):
            return ("Comunicação Social e Jornalismo", "Conhecimentos Específicos")
        if any(k in nc for k in ("professor", "pedagogo", "pedagogia", "educacao", "orientador educacional", "supervisor escolar", "diretores")):
            if "matematica" in nc:
                return ("Matemática", "Conhecimentos Específicos")
            if "historia" in nc:
                return ("História", "Conhecimentos Específicos")
            if "geografia" in nc:
                return ("Geografia", "Conhecimentos Específicos")
            if "fisica" in nc and "educacao fisica" not in nc:
                return ("Física", "Conhecimentos Específicos")
            if "quimica" in nc:
                return ("Química", "Conhecimentos Específicos")
            if "biologia" in nc or "ciencias" in nc:
                return ("Biologia e Ciências", "Conhecimentos Específicos")
            if "ingles" in nc:
                return ("Língua Inglesa", "Conhecimentos Específicos")
            if "espanhol" in nc:
                return ("Língua Espanhola", "Conhecimentos Específicos")
            if "portugues" in nc or "letras" in nc:
                return ("Língua Portuguesa", "Conhecimentos Específicos")
            return ("Pedagogia e Legislação Educacional", "Conhecimentos Específicos")
        if any(k in nc for k in ("biologo", "biologia")):
            return ("Biologia e Ciências", "Conhecimentos Específicos")
        if any(k in nc for k in ("quimico", "quimica")):
            return ("Química", "Conhecimentos Específicos")
        if any(k in nc for k in ("fisico", "fisica")):
            return ("Física", "Conhecimentos Específicos")
        if any(k in nc for k in ("historiador", "historia")):
            return ("História", "Conhecimentos Específicos")
        if any(k in nc for k in ("geografo", "geografia")):
            return ("Geografia", "Conhecimentos Específicos")

    # 2. Inferência de alta precisão por palavras-chave exclusivas do enunciado (statement)
    if any(k in ns for k in ("constituicao federal", "direitos e garantias fundamentais", "controle de constitucionalidade", "poder constituinte", "acao direta de inconstitucionalidade", "argicao de descumprimento", "mandado de seguranca", "habeas corpus", "repercussao geral")):
        return ("Direito Constitucional", "Conhecimentos Específicos")
    if any(k in ns for k in ("ato administrativo", "atos administrativos", "licitacao", "lei n 14 133", "lei 14 133", "lei n 8 666", "improbidade administrativa", "poder de policia", "desapropriacao", "concessao de servico publico", "servidor publico", "processo administrativo disciplinar", "autarquia", "sociedade de economia mista")):
        return ("Direito Administrativo", "Conhecimentos Específicos")
    if any(k in ns for k in ("codigo tributario nacional", "fato gerador", "credito tributario", "lancamento tributario", "imunidade tributaria", "icms", "issqn", "iptu", "ipva", "obrigacao tributaria", "execucao fiscal")):
        return ("Direito Tributário", "Conhecimentos Específicos")
    if any(k in ns for k in ("orcamento publico", "lei de responsabilidade fiscal", "plano plurianual", "diretrizes orcamentarias", "lei orcamentaria anual", "restos a pagar", "creditos adicionais", "despesa publica", "receita publica", "estagios da despesa")):
        return ("Administração Financeira e Orçamentária (AFO)", "Conhecimentos Específicos")
    if any(k in ns for k in ("codigo penal", "tipicidade", "ilicitude", "culpabilidade", "legitima defesa", "estado de necessidade", "concurso de pessoas", "peculato", "corrupcao passiva", "concussao", "prevaricacao", "estelionato", "homicidio", "furto", "roubo")):
        return ("Direito Penal e Criminologia", "Conhecimentos Específicos")
    if any(k in ns for k in ("inquerito policial", "acao penal", "prisao preventiva", "prisao em flagrante", "prisao temporaria", "tribunal do juri", "codigo de processo penal", "juiz das garantias", "interceptacao telefonica")):
        return ("Direito Processual Penal", "Conhecimentos Específicos")
    if any(k in ns for k in ("codigo de processo civil", "tutela provisoria", "tutela de urgencia", "litisconsorcio", "intervencao de terceiros", "coisa julgada", "cumprimento de sentenca", "agravo de instrumento", "apelacao", "embargos de declaracao")):
        return ("Direito Processual Civil", "Conhecimentos Específicos")
    if any(k in ns for k in ("codigo civil", "negocio juridico", "prescricao e decadencia", "responsabilidade civil", "direitos reais", "usucapiao", "sucessao legitima", "testamento", "regime de bens", "inadimplemento das obrigacoes")):
        return ("Direito Civil", "Conhecimentos Específicos")
    if any(k in ns for k in ("consolidacao das leis do trabalho", "clt", "contrato individual de trabalho", "rescisao do contrato de trabalho", "fgts", "aviso previo", "justa causa", "reclamacao trabalhista")):
        return ("Direito e Processo do Trabalho", "Conhecimentos Específicos")
    if any(k in ns for k in ("sociedade anonima", "sociedade limitada", "recuperacao judicial", "falencia", "titulos de credito", "nota promissoria", "duplicata")):
        return ("Direito Empresarial", "Conhecimentos Específicos")
    if any(k in ns for k in ("estatuto da crianca e do adolescente", "conselho tutelar", "medida socioeducativa", "ato infracional")):
        return ("Direito da Criança e do Adolescente", "Conhecimentos Específicos")
    if any(k in ns for k in ("codigo de defesa do consumidor", "fornecedor", "relacao de consumo", "vicio do produto", "fato do produto")):
        return ("Direito do Consumidor", "Conhecimentos Específicos")
    if any(k in ns for k in ("sistema unico de saude", "lei n 8 080", "lei 8 080", "lei n 8 142", "atencao primaria a saude", "vigilancia epidemiologica", "vigilancia sanitaria", "estrategia saude da familia")):
        return ("Legislação do SUS e Saúde Pública", "Conhecimentos Específicos")
    if any(k in ns for k in ("balanco patrimonial", "demonstracao do resultado", "ativo circulante", "passivo circulante", "patrimonio liquido", "depreciacao", "amortizacao", "lancamento contabil", "cpc ")):
        return ("Contabilidade Geral", "Conhecimentos Específicos")
    if any(k in ns for k in ("windows", "linux", "microsoft word", "microsoft excel", "powerpoint", "libreoffice", "calc", "writer", "navegador", "phishing", "malware", "ransomware", "atalho de teclado")):
        return ("Informática", "Conhecimentos Básicos")
    if any(k in ns for k in ("sql", "select ", "banco de dados relacional", "chave primaria", "algoritmo", "orientacao a objetos", "java", "python", "javascript", "html", "css", "tcp/ip", "ipv4", "ipv6", "firewall", "kubernetes", "docker", "scrum", "itil", "cobit")):
        return ("Tecnologia da Informação", "Conhecimentos Específicos")
    if any(k in ns for k in ("probabilidade", "combinatoria", "permutacao", "arranjo", "progressao aritmetica", "progressao geometrica", "proposicao logica", "tabela-verdade", "equivalencia logica", "negacao da proposicao")):
        return ("Raciocínio Lógico-Matemático", "Conhecimentos Básicos")
    if any(k in ns for k in ("juros compostos", "juros simples", "taxa interna de retorno", "valor presente liquido", "sistema price", "sistema sac", "desconto racional", "desconto comercial")):
        return ("Matemática Financeira", "Conhecimentos Específicos")
    if any(k in ns for k in ("base nacional comum curricular", "bncc", "lei de diretrizes e bases da educacao", "ldb", "projeto politico-pedagogico", "avaliacao formativa", "piaget", "vygotsky", "paulo freire")):
        return ("Pedagogia e Legislação Educacional", "Conhecimentos Específicos")
    if any(k in ns for k in ("planejamento estrategico", "matriz swot", "balanced scorecard", "gestao por processos", "gestao de projetos", "pmbok", "cultura organizacional", "lideranca", "motivacao", "maslow", "herzberg")):
        return ("Administração Geral e Pública", "Conhecimentos Específicos")
    if any(k in ns for k in ("auditoria interna", "auditoria externa", "risco de auditoria", "evidencia de auditoria", "controle interno", "coso", "tribunal de contas")):
        return ("Auditoria e Controle", "Conhecimentos Específicos")

    # 3. Se o cargo ainda indicar área jurídica/administrativa ampla
    if canonical_disc == "Conhecimentos Específicos":
        if any(k in nc for k in ("juiz", "promotor", "defensor", "procurador", "advogado", "analista judiciario", "oficial de justica", "delegado", "escrivao")):
            return ("Noções de Direito", "Conhecimentos Específicos")
        if any(k in nc for k in ("auditor fiscal", "agente tributario", "fiscal de tributos", "receita")):
            return ("Direito Tributário", "Conhecimentos Específicos")
        if any(k in nc for k in ("auditor de controle", "analista de controle", "controlador")):
            return ("Auditoria e Controle", "Conhecimentos Específicos")
        if any(k in nc for k in ("administrador", "administracao", "gestao publica", "assistente administrativo", "tecnico administrativo", "agente administrativo", "analista administrativo")):
            return ("Administração Geral e Pública", "Conhecimentos Específicos")

    return (canonical_disc, _infer_module(canonical_disc))


def _infer_module(canonical_discipline: str) -> str:
    basic_set = {
        "Língua Portuguesa",
        "Redação Oficial",
        "Língua Inglesa",
        "Língua Espanhola",
        "Língua Francesa",
        "Raciocínio Lógico-Matemático",
        "Matemática",
        "Informática",
        "Atualidades",
        "Direitos Humanos, Ética e Cidadania",
        "História e Geografia de Mato Grosso",
        "História e Geografia de Rondônia",
        "História e Geografia do Tocantins",
        "História e Geografia do Amapá",
        "Legislação e Realidade do Amazonas",
        "Conhecimentos Básicos / Gerais",
    }
    return "Conhecimentos Básicos" if canonical_discipline in basic_set else "Conhecimentos Específicos"


# =============================================================================
# 3. DESMEMBRADOR DE QUESTÕES ENGOLIDAS NA ÚLTIMA ALTERNATIVA
# =============================================================================

SWALLOWED_Q_SPLIT_RE = re.compile(
    r"^(.*?)\s+"
    r"(?:FGV\s*[‐\-–]?\s*(?:Projetos\s+)?|o\s+da\s+Bahia\s*[–\-].*?FGV\s*[‐\-–]\s*Projetos\s+)?"
    r"(?:(?:Q?uest[aã]o\s+)?(?:[A-Z]{0,2}(\d{1,3})\s*(?:\([A-Z0-9_\-]+\))?|(?:NMCB|NSCE|NMCE|NS|P\d+CE)[A-Z0-9_\-]+))"
    r"\s+([A-ZÀ-Ý“\"«].*)$",
    re.DOTALL,
)

# Caso em que o número da questão engolida não apareceu entre o ponto final da alternativa E e o enunciado da próxima
SWALLOWED_NO_NUM_SPLIT_RE = re.compile(
    r"^([^\n]{5,240}?[\.\!\?;])\s+([A-ZÀ-Ý“\"«].{35,}?\(A\).*?\(B\).*?\(C\).*?\(D\).*)$",
    re.DOTALL,
)

DISCURSIVE_TAIL_RE = re.compile(
    r"^(.*?)(?:\s+|\n)(?:(?:FGV|FUNDA[CÇ][AÃ]O\s+GETULIO\s+VARGAS)\s+)?(?:Prova\s+Discursiva|Quest[aã]o\s+Discursiva|Quest[oõ]es\s+Discursivas|Discursiva\s+1|RASCUNHO\b|M[OÓ]DULO\s+[I123V]+\b|CONHECIMENTOS\s+(?:B[AÁ]SICOS|ESPEC[IÍ]FICOS|GERAIS)\s*$).*$",
    re.IGNORECASE | re.DOTALL,
)


def unmerge_swallowed_questions(
    questions: List[Dict],
    answers_map: Dict[int, str],
) -> Tuple[List[Dict], int]:
    """
    Inspeciona a última alternativa (E ou D) de cada questão.
    1. Remove caudas de 'Prova Discursiva', 'Rascunho' ou 'Módulo II' no fim da alternativa.
    2. Se houver uma questão objetiva engolida (com (A), (B), (C), (D)) cujo número esteja
       faltando na lista de questões da prova, separa-a em uma nova questão válida!
    """
    existing_nums = {q["question_number"] for q in questions}
    new_questions: List[Dict] = []
    unmerged_count = 0

    for q in questions:
        alts = q["alternatives"]
        last_key = "E" if alts.get("E") is not None else ("D" if alts.get("D") is not None else None)
        if not last_key:
            continue
        last_val = alts[last_key] or ""

        # 1. Remove cauda de Prova Discursiva / Rascunho / Módulo se houver
        m_disc = DISCURSIVE_TAIL_RE.match(last_val)
        if m_disc and len(m_disc.group(1).strip()) >= 1:
            alts[last_key] = m_disc.group(1).strip()
            last_val = alts[last_key]
            unmerged_count += 1

        # 2. Verifica se contém (A), (B), (C), (D) engolidos
        if not all(f"({let})" in last_val for let in ("A", "B", "C", "D")):
            continue

        m_sw = SWALLOWED_Q_SPLIT_RE.match(last_val)
        clean_prev_alt = ""
        sw_num_str: Optional[str] = None
        sw_body = ""

        if m_sw:
            clean_prev_alt = m_sw.group(1).strip()
            sw_num_str = m_sw.group(2)
            sw_body = m_sw.group(3).strip()
        else:
            m_no_num = SWALLOWED_NO_NUM_SPLIT_RE.match(last_val)
            if m_no_num:
                clean_prev_alt = m_no_num.group(1).strip()
                sw_body = m_no_num.group(2).strip()

        if len(clean_prev_alt) < 2 or not sw_body:
            continue

        # Determina o número da questão engolida
        sw_num: Optional[int] = int(sw_num_str) if sw_num_str else None
        if sw_num is None or sw_num in existing_nums:
            # Tenta inferir q_number + 1 ou q_number - 1 se estiver faltando
            if (q["question_number"] + 1) not in existing_nums:
                sw_num = q["question_number"] + 1
            elif q["question_number"] > 1 and (q["question_number"] - 1) not in existing_nums:
                sw_num = q["question_number"] - 1

        # Segmenta sw_body em statement + alternatives (A)-(D)/(E)
        alt_matches = list(FGVParser.ALT_SPLIT_RE.finditer(sw_body))
        if len(alt_matches) < 4:
            continue

        req = ["A", "B", "C", "D"]
        sel = []
        s_idx = 0
        for let in req:
            f_m = None
            for m in alt_matches[s_idx:]:
                if (m.group(1) or m.group(2)).upper() == let:
                    f_m = m
                    break
                s_idx += 1
            if f_m:
                sel.append(f_m)
                s_idx += 1
            else:
                break

        if len(sel) < 4:
            continue

        f_e = None
        for m in alt_matches[s_idx:]:
            if (m.group(1) or m.group(2)).upper() == "E":
                f_e = m
                break
        if f_e:
            sel.append(f_e)
            act_lets = ["A", "B", "C", "D", "E"]
        else:
            act_lets = ["A", "B", "C", "D"]

        sw_stmt = sw_body[: sel[0].start()].strip()
        if len(sw_stmt) < 10:
            continue

        # Limpa a alternativa da questão original!
        alts[last_key] = clean_prev_alt
        unmerged_count += 1

        if sw_num is not None and sw_num not in existing_nums and 1 <= sw_num <= 200:
            sw_alts: Dict[str, Optional[str]] = {"A": None, "B": None, "C": None, "D": None, "E": None}
            valid_alts = True
            for i, let in enumerate(act_lets):
                sp = sel[i].end()
                ep = sel[i + 1].start() if i + 1 < len(sel) else len(sw_body)
                av = re.sub(r"\s+", " ", sw_body[sp:ep]).strip()
                if not av:
                    valid_alts = False
                    break
                sw_alts[let] = av

            if valid_alts:
                ans = answers_map.get(sw_num) or q.get("answer")
                new_questions.append(
                    {
                        "question_number": sw_num,
                        "discipline": q.get("discipline"),
                        "module": q.get("module"),
                        "statement": re.sub(r"\s+", " ", sw_stmt).strip(),
                        "alternatives": sw_alts,
                        "answer": ans,
                        "annulled": (ans == "ANULADA"),
                        "has_image": False,
                        "image_path": None,
                        "page_number": q.get("page_number", 2),
                    }
                )
                existing_nums.add(sw_num)

    if new_questions:
        questions.extend(new_questions)
        questions.sort(key=lambda item: item["question_number"])

    return (questions, unmerged_count)


# =============================================================================
# 4. EXTRAÇÃO DE METADADOS PARA O BANCO DO APP (ANO, UF, ÓRGÃO, NÍVEL, HASH)
# =============================================================================

UF_PATTERNS: List[Tuple[str, str]] = [
    (r"\b(?:acre|pgeac|tjac|mpac|sefazac)\b|^(?:pgeac|tjac|mpac|sefazac)", "AC"),
    (r"\b(?:alagoas|tjal|mpal|sefazal)\b|^(?:tjal|mpal|sefazal)", "AL"),
    (r"\b(?:amazonas|manaus|aleam|pmam|cbmam|sefazam|tjam|mpam|semsa)\b|^(?:aleam|pmam|cbmam|sefazam|tjam|mpam|semsa)", "AM"),
    (r"\b(?:amapa|seadap|tjap|mpap)\b|^(?:seadap|tjap|mpap)", "AP"),
    (r"\b(?:bahia|salvador|alba|tjba|tceba|mpba|adab|conder|embasa|sefazba)\b|^(?:alba|tjba|tceba|mpba|adab|conder|embasa|sefazba)", "BA"),
    (r"\b(?:ceara|fortaleza|cmfor|tjce|mpce|sefazce)\b|^(?:cmfor|tjce|mpce|sefazce)", "CE"),
    (r"\b(?:distrito federal|brasilia|dpdf|conselhotutelardf|tjdf|tjdft)\b|^(?:dpdf|conselhotutelardf|tjdf)", "DF"),
    (r"\b(?:espirito santo|vitoria|banestes|sefazes|tjes|mpes|semevitoria|pmes)\b|^(?:banestes|sefazes|tjes|mpes|semevitoria|pmes)", "ES"),
    (r"\b(?:goias|goiania|tjgo|mpgo|tcego)\b|^(?:tjgo|mpgo|tcego)", "GO"),
    (r"\b(?:maranhao|alema|tjma|mpma)\b|^(?:alema|tjma|mpma)", "MA"),
    (r"\b(?:minas gerais|belo horizonte|agemg|sefmg|pbh|fhemig|seemg|codemig|cemig|tjmg|mpmg|tjemg)\b|^(?:agemg|sefmg|pbh|fhemig|seemg|codemig|cemig|tjmg|mpmg|tjemg)", "MG"),
    (r"\b(?:mato grosso do sul|campo grande|mps12|mpms|tjms|dpgems)\b|^(?:mps12|mpms|tjms|dpgems)", "MS"),
    (r"\b(?:mato grosso|cuiaba|mpmt|tjmt|dpmt|sesmt|seducmt|cgmcuiaba|smfcuiaba|sefazmt)\b|^(?:mpmt|tjmt|dpmt|sesmt|seducmt|cgmcuiaba|smfcuiaba|sefazmt)", "MT"),
    (r"\b(?:estado do para|belem|canaa dos carajas|smacanaacarajas|tcepa|tjpa|mppa|sefazpa)\b|^(?:smacanaacarajas|tcepa|tjpa|mppa|sefazpa)", "PA"),
    (r"\b(?:paraiba|joao pessoa|cgepb|trt13|tjpb|mppb)\b|^(?:cgepb|trt13|tjpb|mppb)", "PB"),
    (r"\b(?:pernambuco|recife|caruaru|jaboatao|sao lourenco da mata|compesa|dpe-pe|tcepe|tjpe|camaradorecife|camaracaruaru|smejaboatao|smesaolourencodamata)\b|^(?:compesa|dpe-pe|tcepe|tjpe|camaradorecife|camaracaruaru|smejaboatao|smesaolourencodamata)", "PE"),
    (r"\b(?:piaui|teresina|tjpi|tcepi|mppi)\b|^(?:tjpi|tcepi|mppi)", "PI"),
    (r"\b(?:parana|curitiba|tjpr|mppr|fempar|seappr)\b|^(?:tjpr|mppr|fempar|seappr)", "PR"),
    (r"\b(?:rio de janeiro|niteroi|agenersa|cbmerj|cgmrj|dpgerj|dperj|mprj|tjrj|seplag|nittrans|pmn|smfniteroi|smfrj|pgmcmrio|inea|fbn|tcerj|pcrj|sefazrj)\b|^(?:agenersa|cbmerj|cgmrj|dpgerj|dperj|mprj|tjrj|seplag|nittrans|pmn|smfniteroi|smfrj|pgmcmrio|inea|fbn|tcerj|pcrj|sefazrj)", "RJ"),
    (r"\b(?:rio grande do norte|natal|pcrn|tjrn)\b|^(?:pcrn|tjrn)", "RN"),
    (r"\b(?:rondonia|porto velho|alero|tjro|mpro|seducro)\b|^(?:alero|tjro|mpro|seducro)", "RO"),
    (r"\b(?:roraima|boa vista|tcerr|tjrr|mprr)\b|^(?:tcerr|tjrr|mprr)", "RR"),
    (r"\b(?:rio grande do sul|porto alegre|cage-rs|tjrs|mprs|procempa|selecaoseducrs|dpers)\b|^(?:cage-rs|tjrs|mprs|procempa|selecaoseducrs|dpers)", "RS"),
    (r"\b(?:santa catarina|florianopolis|alesc|cgesc|tcesc|tjsc|mpsc)\b|^(?:alesc|cgesc|tcesc|tjsc|mpsc)", "SC"),
    (r"\b(?:sergipe|aracaju|seadse|tjse|mpse)\b|^(?:seadse|tjse|mpse)", "SE"),
    (r"\b(?:sao paulo|sao jose dos campos|santo andre|paulinia|marilia|cgesp|cfopmesp|pmesp|prefsjc|seducsp|sme-sp|tcm-sp|tjsp|mpsp|pmsa|famema|fms|alesp|pcsp|sefazsp)\b|^(?:cgesp|cfopmesp|pmesp|prefsjc|seducsp|sme-sp|tcm-sp|tjsp|mpsp|pmsa|famema|fms|alesp|pcsp|sefazsp)", "SP"),
    (r"\b(?:tocantins|palmas|tceto|tjto|mpto|sesto|secad)\b|^(?:tceto|tjto|mpto|sesto|secad)", "TO"),
]


def infer_exam_metadata(
    concurso_slug: str,
    concurso_title: str,
    cargo_title: str,
) -> Dict[str, object]:
    """Extrai Ano, UF e Nível de Escolaridade a partir dos identificadores do concurso e cargo."""
    blob = f"{concurso_slug} {concurso_title}"
    norm_blob = _norm(blob)

    # 1. Ano (2012..2026)
    year = 2024
    m4 = re.search(r"\b(201[2-9]|202[0-6])\b", blob)
    if m4:
        year = int(m4.group(1))
    else:
        m2 = re.search(r"(?:1[2-9]|2[0-6])$", re.sub(r"ed\d+$", "", concurso_slug))
        if m2:
            y2 = int(m2.group(0))
            year = 2000 + y2

    # 2. UF
    uf = "BR"
    for pat, state_code in UF_PATTERNS:
        if re.search(pat, norm_blob):
            uf = state_code
            break

    # 3. Nível de escolaridade
    norm_cargo = _norm(cargo_title)
    if any(
        k in norm_cargo
        for k in (
            "fundamental",
            "auxiliar de servicos",
            "cozinheiro",
            "culinaria",
            "operacional",
            "servicos gerais",
        )
    ):
        level = "Fundamental"
    elif any(
        k in norm_cargo
        for k in (
            "tecnico",
            "assistente",
            "agente",
            "soldado",
            "cabo",
            "sargento",
            "guarda",
            "nivel medio",
            "contrarregra",
            "motorista",
            "operador",
        )
    ) and not any(sup in norm_cargo for sup in ("nivel superior", "analista", "auditor", "juiz", "promotor", "defensor", "procurador", "medico", "engenheiro", "professor", "arquiteto", "advogado")):
        level = "Médio"
    else:
        level = "Superior"

    return {
        "year": year,
        "uf": uf,
        "level": level,
    }


def compute_question_hash(statement: str, alternatives: Dict[str, Optional[str]]) -> str:
    """Gera hash único determinístico de 16 caracteres para deduplicação da questão no banco."""
    norm_st = re.sub(r"\W+", "", _norm(statement))[:180]
    first_alt = alternatives.get("A") or alternatives.get("C") or ""
    norm_alt = re.sub(r"\W+", "", _norm(first_alt))[:60]
    digest = hashlib.sha1(f"{norm_st}|{norm_alt}".encode("utf-8")).hexdigest()
    return digest[:16]


TEXT_REF_RE = re.compile(
    r"\b(?:no texto|nesse texto|neste texto|segundo o texto|de acordo com o texto|o texto (?:acima|apresentado|aborda|trata|mostra|defende|informa|afirma)|"
    r"texto\s+(?:I|II|III|IV|1|2|3|4)|primeiro par[aá]grafo|segundo par[aá]grafo|terceiro par[aá]grafo|[uú]ltimo par[aá]grafo|"
    r"na cr[oô]nica|no poema|no fragmento|no trecho|in the text|according to the text|the text|en el texto|seg[uú]n el texto)\b",
    re.IGNORECASE,
)

QUESTION_COMMAND_SPLIT_RE = re.compile(
    r"^(.*?[\.\!\?”\"»\)])\s+("
    r"(?:Assinale|Indique|Marque|Identifique|Aponte|Julgue|Considere|Analise|Avalie|Sobre\s+o\s+texto|De\s+acordo\s+com\s+o\s+texto|Segundo\s+o\s+texto|"
    r"No\s+(?:primeiro|segundo|terceiro|[uú]ltimo)\s+par[aá]grafo|No\s+texto|Nesse\s+texto|O\s+texto\s+acima|A\s+respeito\s+do\s+texto|"
    r"In\s+the\s+text|According\s+to\s+the\s+text|The\s+(?:author|text|main\s+purpose))\b.*)$",
    re.DOTALL,
)


def propagate_shared_base_texts(questions: List[Dict]) -> int:
    """
    Em disciplinas de interpretação textual (Língua Portuguesa, Língua Inglesa, Língua Espanhola, Redação Oficial),
    quando a primeira questão de um bloco traz o texto-base longo junto ao seu enunciado (>320 caracteres) e as
    questões seguintes referenciam 'o texto'/'Texto I'/'primeiro parágrafo' mas possuem enunciado curto (<280 caracteres),
    propaga o texto-base para o campo `base_text` dessas questões dependentes para que possam ser exibidas isoladamente no App!
    """
    current_base_text: Optional[str] = None
    current_disc: Optional[str] = None
    last_q_num = 0
    propagated = 0

    lang_disciplines = {
        "Língua Portuguesa",
        "Língua Inglesa",
        "Língua Espanhola",
        "Língua Francesa",
        "Redação Oficial",
    }

    for q in questions:
        disc = q.get("discipline")
        st = (q.get("statement") or "").strip()
        q_num = q["question_number"]

        if disc not in lang_disciplines or (last_q_num and q_num - last_q_num > 4):
            current_base_text = None
            current_disc = disc

        if disc in lang_disciplines:
            if len(st) >= 340:
                m_split = QUESTION_COMMAND_SPLIT_RE.match(st)
                if m_split and len(m_split.group(1).strip()) >= 180:
                    current_base_text = m_split.group(1).strip()
                else:
                    current_base_text = st
                current_disc = disc
                q["base_text"] = None
            elif (
                current_base_text
                and current_disc == disc
                and len(st) < 280
                and TEXT_REF_RE.search(st)
            ):
                q["base_text"] = current_base_text
                propagated += 1
            else:
                q["base_text"] = q.get("base_text")
        else:
            q["base_text"] = None

        last_q_num = q_num

    return propagated


def run_full_consolidation(
    raw_dir: Path = Path("data/raw"),
    proc_dir: Path = Path("data/processed"),
    skip_reparse: bool = False,
) -> None:
    parser = FGVParser(raw_dir=raw_dir, processed_dir=proc_dir, use_ollama_fallback=False)

    if not skip_reparse:
        # -------------------------------------------------------------------------
        # PASSO 1: Restaurar gabarito A-D do 2º Curso de Formação em dnit23
        # -------------------------------------------------------------------------
        print("[1/5] Restaurando gabarito oficial A-D do 2º Curso de Formação (dnit23)...")
        dnit_cf_url = "https://conhecimento.fgv.br/sites/default/files/concursos/cf-dnit-gabaritos-para-publicacao_definitivo.pdf"
        try:
            resp = requests.get(dnit_cf_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200 and resp.content.startswith(b"%PDF"):
                for c_dir in sorted((raw_dir / "dnit23").iterdir()):
                    if c_dir.is_dir() and c_dir.name != "cargo-geral":
                        (c_dir / "gabarito.pdf").write_bytes(resp.content)
                        parser.parse_cargo_dir(c_dir)
                print("  -> dnit23 atualizado e reprocessado com sucesso!")
        except Exception as exc:
            print(f"  [WARN] Erro ao atualizar dnit23: {exc}")

        # -------------------------------------------------------------------------
        # PASSO 2: Reprocessar as provas que tinham sequence gaps, alts com (A)-(D) ou statements curtos
        # -------------------------------------------------------------------------
        print("[2/5] Reprocessando provas afetadas por iniciais no meio de frase, alternativas visuais ou lacunas...")
        to_reparse: List[Path] = []
        for jpath in sorted(proc_dir.glob("*.json")):
            if jpath.name.startswith("_"):
                continue
            d = json.loads(jpath.read_text("utf-8"))
            qs = d["questions"]
            q_nums = [q["question_number"] for q in qs]
            if not q_nums:
                continue
            start_exp = 1 if min(q_nums) < 10 else min(q_nums)
            has_gap = len(set(range(start_exp, max(q_nums) + 1)) - set(q_nums)) > 0
            has_short = any(len(q["statement"].strip()) < 25 for q in qs)
            has_mid_initial = any(
                f"({let})" in (q["alternatives"].get(k) or "")
                for q in qs
                for k in ("A", "B", "C")
                for let in ("A", "B", "C", "D")
            )
            has_sw = any(
                q["alternatives"].get(k)
                and all(f"({let})" in q["alternatives"][k] for let in ("A", "B", "C", "D"))
                for q in qs
                for k in ("D", "E")
            )
            if has_gap or has_short or has_mid_initial or has_sw:
                c_dir = raw_dir / d["concurso_slug"] / d["cargo_slug"]
                if c_dir.exists():
                    to_reparse.append(c_dir)

        print(f"  -> Reprocessando {len(to_reparse)} provas selecionadas...")
        for idx, c_dir in enumerate(to_reparse, 1):
            parser.parse_cargo_dir(c_dir)
            if idx % 50 == 0:
                print(f"     [{idx}/{len(to_reparse)}] concluídas...")

    # -------------------------------------------------------------------------
    # PASSO 3, 4 e 5: Higienizar cargo_title, desmembrar questões engolidas,
    # especializar disciplinas/módulos, propagar textos-base e gerar _index.json
    # -------------------------------------------------------------------------
    print("[3/5 a 5/5] Higienizando títulos, desmembrando questões, especializando disciplinas, propagando textos-base e gerando _index.json...")
    total_unmerged = 0
    total_base_texts = 0
    total_questions_final = 0
    unique_hashes: Dict[str, List[str]] = {}
    disc_counter: Counter = Counter()
    mod_counter: Counter = Counter()
    uf_counter: Counter = Counter()
    year_counter: Counter = Counter()
    level_counter: Counter = Counter()
    exams_catalog: List[Dict] = []

    for jpath in sorted(proc_dir.glob("*.json")):
        if jpath.name.startswith("_"):
            continue
        d = json.loads(jpath.read_text("utf-8"))
        stem = jpath.stem
        c_slug = d["concurso_slug"]
        g_slug = d["cargo_slug"]

        # 3a. Limpar cargo_title
        clean_ct = clean_cargo_title(stem, d.get("cargo_title", ""))
        d["cargo_title"] = clean_ct

        # 3b. Desmembrar eventuais questões engolidas na última alternativa e limpar caudas
        gab_path = raw_dir / c_slug / g_slug / "gabarito.pdf"
        qs = d["questions"]
        has_full_swallowed_q = any(
            q["alternatives"].get(k)
            and all(f"({let})" in q["alternatives"][k] for let in ("A", "B", "C", "D"))
            for q in qs
            for k in ("D", "E")
        )
        has_disc_tail = any(
            q["alternatives"].get(k)
            and DISCURSIVE_TAIL_RE.match(q["alternatives"][k])
            for q in qs
            for k in ("D", "E")
        )
        if has_full_swallowed_q or has_disc_tail:
            ans_map = {}
            if has_full_swallowed_q and gab_path.exists():
                max_q = max(q["question_number"] for q in qs)
                ans_map = parser.parse_gabarito_pdf(
                    gab_path,
                    target_type="1",
                    cargo_hint=clean_ct,
                    expected_q_nums=set(range(1, max_q + 5)),
                )
            qs, unm = unmerge_swallowed_questions(qs, ans_map)
            total_unmerged += unm

        # 4. Normalizar e especializar disciplinas + inferir módulo
        prev_valid_disc: Optional[str] = None
        exam_disciplines: Counter = Counter()
        for q in qs:
            canon_disc, mod, is_valid = normalize_discipline_and_module(
                raw_disc=q.get("discipline"),
                prev_valid_disc=prev_valid_disc,
                cargo_title=clean_ct,
            )
            if is_valid:
                prev_valid_disc = canon_disc
            # Especializa se ficou em 'Conhecimentos Específicos' ou 'Conhecimentos Básicos / Gerais'
            spec_disc, spec_mod = specialize_generic_discipline(
                canonical_disc=canon_disc,
                cargo_title=clean_ct,
                statement=q.get("statement", ""),
            )
            q["discipline"] = spec_disc
            q["module"] = spec_mod
            disc_counter[spec_disc] += 1
            mod_counter[spec_mod] += 1
            exam_disciplines[spec_disc] += 1

            # 5a. Hash único da questão para deduplicação no banco
            q_hash = compute_question_hash(q["statement"], q["alternatives"])
            q["question_hash"] = q_hash
            unique_hashes.setdefault(q_hash, []).append(f"{stem}#Q{q['question_number']}")

            # Garantia extra: se a prova for 4 alternativas (E is None) e answer=='E', ajusta para ANULADA
            if q["alternatives"].get("E") is None and q["alternatives"].get("A") is not None and q.get("answer") == "E":
                q["answer"] = "ANULADA"
                q["annulled"] = True

        # 4b. Propagar texto-base compartilhado (base_text) nas questões de interpretação
        total_base_texts += propagate_shared_base_texts(qs)

        # 5b. Metadados do exame (year, uf, level, question_type)
        meta_extra = infer_exam_metadata(c_slug, d.get("concurso_title", ""), clean_ct)
        is_ce = all(q["alternatives"].get("A") is None for q in qs)
        is_4alt = (not is_ce) and all(q["alternatives"].get("E") is None for q in qs)
        q_type = "certo_errado" if is_ce else ("multiple_choice_4" if is_4alt else "multiple_choice_5")

        d["year"] = meta_extra["year"]
        d["uf"] = meta_extra["uf"]
        d["level"] = meta_extra["level"]
        d["question_type"] = q_type
        d["total_questions"] = len(qs)
        d["questions"] = qs
        total_questions_final += len(qs)

        year_counter[d["year"]] += 1
        uf_counter[d["uf"]] += 1
        level_counter[d["level"]] += 1

        exams_catalog.append(
            {
                "exam_id": stem,
                "file": jpath.name,
                "concurso_slug": c_slug,
                "concurso_title": d.get("concurso_title", ""),
                "cargo_slug": g_slug,
                "cargo_title": clean_ct,
                "year": d["year"],
                "uf": d["uf"],
                "level": d["level"],
                "question_type": q_type,
                "total_questions": len(qs),
                "disciplines": dict(exam_disciplines),
            }
        )

        jpath.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    # 6. Salvar catálogo mestre data/processed/_index.json para o App
    index_payload = {
        "total_exams": len(exams_catalog),
        "total_questions": total_questions_final,
        "unique_questions": len(unique_hashes),
        "shared_question_appearances": total_questions_final - len(unique_hashes),
        "total_canonical_disciplines": len(disc_counter),
        "modules_distribution": dict(mod_counter),
        "levels_distribution": dict(level_counter),
        "years_distribution": dict(sorted(year_counter.items())),
        "ufs_distribution": dict(uf_counter.most_common()),
        "disciplines_distribution": dict(disc_counter.most_common()),
        "exams": exams_catalog,
    }
    (proc_dir / "_index.json").write_text(
        json.dumps(index_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Consolidação concluída!")
    print(f"  -> Questões desmembradas/limpas de alternativas finais: {total_unmerged}")
    print(f"  -> Questões enriquecidas com texto-base compartilhado (base_text): {total_base_texts}")
    print(f"  -> Total final de questões validadas: {total_questions_final}")
    print(f"  -> Total de questões únicas deduplicadas (question_hash): {len(unique_hashes)}")
    print(f"  -> Total de disciplinas canônicas padronizadas: {len(disc_counter)}")
    print(f"  -> Catálogo mestre gerado em: {proc_dir / '_index.json'}")


if __name__ == "__main__":
    run_full_consolidation()
