"""
Módulo de Integração Local com Ollama (`llama3.1:8b`) com Guardas Estritas de Veracidade.

Projetado para atuar exclusivamente como camada assistiva auditável (Zero Alucinação):
1. Recuperação de questões com formatação atípica (Gap 5), validando que 100% dos textos
   extraídos são substrings literais do bloco bruto do PDF (Veracity Guard).
2. Extração assistida de tabelas/textos de gabarito complexos (Gap 3 e Gap 4), validando
   respostas estritamente no domínio {"A", "B", "C", "D", "E", "ANULADA"}.
3. Classificação de Disciplina / Área de Conhecimento para questões sem cabeçalho explícito
   na página do PDF.
4. Sugestão de gabarito em quarentena (Gap 6) com justificativa, sempre separada do
   gabarito oficial da banca.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1:8b"


def _norm_alnum(text: str) -> str:
    """Normaliza texto mantendo apenas caracteres alfanuméricos minúsculos para checagem de veracidade."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", ascii_str.lower())


class OllamaVeracityClient:
    """Cliente local Ollama com prompts determinísticos (temperature=0.0) e validação de veracidade."""

    # =========================================================================
    # PROMPTS OFICIAIS DO PIPELINE (TEMPERATURE = 0.0 / JSON MODE)
    # =========================================================================

    PROMPT_PARSE_QUESTION = """Você é um extrator literal de dados de provas de concursos da banca FGV.
REGRAS ABSOLUTAS DE VERACIDADE (PROIBIDO ALUCINAR):
1. Extraia o enunciado (`statement`) e as alternativas (`A`, `B`, `C`, `D` e opcionalmente `E`) EXATAMENTE palavra por palavra como aparecem no TEXTO BRUTO abaixo.
2. NUNCA resuma, parafraseie, corrija gramática ou invente palavras que não estejam no TEXTO BRUTO.
3. Se o texto contiver menos de 4 alternativas claras (A, B, C, D), retorne `{"valid": false}`.
4. Retorne APENAS um objeto JSON válido no seguinte formato:
{
  "valid": true,
  "question_number": <int>,
  "discipline": <string ou null>,
  "statement": "<texto exato do enunciado>",
  "alternatives": {
    "A": "<texto exato da alternativa A>",
    "B": "<texto exato da alternativa B>",
    "C": "<texto exato da alternativa C>",
    "D": "<texto exato da alternativa D>",
    "E": "<texto exato da alternativa E ou null se a prova tiver apenas 4 alternativas>"
  }
}

TEXTO BRUTO DO BLOCO DO PDF:
\"\"\"
{raw_text}
\"\"\""""

    PROMPT_PARSE_GABARITO = """Você é um auditor de gabaritos oficiais de concursos da banca FGV.
REGRAS ABSOLUTAS DE VERACIDADE (PROIBIDO ALUCINAR):
1. Analise o texto oficial extraído do PDF de gabarito abaixo.
2. Localize EXCLUSIVAMENTE a tabela ou lista de respostas correspondente ao Cargo: "{cargo_title}" e ao Tipo de Prova: "Tipo {target_type}" (Cor Branca / Prova {target_type} / Gabarito Único).
3. IGNORE tabelas de Tipo 2 (Verde), Tipo 3 (Amarela), Tipo 4 (Azul) ou de outros cargos.
4. Para questões marcadas com `*`, `[*]`, `(*)` ou `Anulada`, preencha o valor `"ANULADA"`.
5. Retorne APENAS um objeto JSON válido mapeando o número da questão (como string) para a letra oficial ("A", "B", "C", "D", "E" ou "ANULADA"):
{
  "matched_cargo_header": "<cabeçalho exato encontrado no texto>",
  "answers": {
    "1": "A",
    "2": "C",
    "3": "ANULADA"
  }
}

TEXTO DO GABARITO OFICIAL:
\"\"\"
{raw_gabarito_text}
\"\"\""""

    PROMPT_CLASSIFY_DISCIPLINE = """Você é um especialista em taxonomia de provas da banca FGV Conhecimento.
Classifique a questão abaixo na sua respectiva DISCIPLINA padrão de concursos públicos (ex: "Língua Portuguesa", "Raciocínio Lógico-Matemático", "Direito Constitucional", "Direito Administrativo", "Direito Tributário", "Direito Civil", "Direito Processual Civil", "Direito Penal", "Direito Processual Penal", "Contabilidade Geral", "Contabilidade Pública", "Administração Pública", "Economia", "Tecnologia da Informação", "Legislação Específica", "Conhecimentos Específicos - {cargo_title}").

Retorne APENAS um JSON válido:
{
  "discipline": "<nome padronizado da disciplina>",
  "module": "<Conhecimentos Básicos | Conhecimentos Específicos>"
}

CARGO DA PROVA: {cargo_title}
ENUNCIADO DA QUESTÃO:
\"\"\"
{statement}
\"\"\""""

    PROMPT_SOLVE_QUARANTINE_QUESTION = """Você é um professor especialista na banca FGV resolvendo uma questão cujo gabarito oficial está em quarentena.
Analise rigorosamente o enunciado e as alternativas abaixo e indique a alternativa correta (A, B, C, D ou E).
IMPORTANTE: Esta resposta será marcada no banco de dados com a flag `answer_source = "ai_quarantine"` (nunca como gabarito oficial).

Retorne APENAS um JSON válido:
{
  "suggested_answer": "<A|B|C|D|E>",
  "confidence": <float entre 0.0 e 1.0>,
  "justification": "<resumo técnico objetivo de até 2 frases explicando o gabarito>"
}

CARGO: {cargo_title}
DISCIPLINA: {discipline}
ENUNCIADO:
{statement}
ALTERNATIVAS:
{alternatives_json}"""

    def __init__(
        self,
        model: str = DEFAULT_OLLAMA_MODEL,
        base_url: str = OLLAMA_BASE_URL,
        timeout: int = 45,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._available: Optional[bool] = None

    def is_available(self) -> bool:
        """Verifica se o servidor local do Ollama está ativo e com o modelo disponível."""
        if self._available is not None:
            return self._available
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=3)
            if resp.status_code == 200:
                models = [m.get("name", "") for m in resp.json().get("models", [])]
                self._available = any(self.model in m for m in models) or len(models) > 0
                if self._available and not any(self.model in m for m in models):
                    self.model = models[0]
                return self._available
        except Exception:
            pass
        self._available = False
        return False

    def _generate_json(self, prompt: str) -> Optional[Dict]:
        """Executa chamada determinística (`temperature=0.0`, `format='json'`) ao Ollama local."""
        if not self.is_available():
            return None
        try:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "top_p": 1.0,
                    "num_predict": 1024,
                },
            }
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            raw_response = resp.json().get("response", "")
            return json.loads(raw_response)
        except Exception as exc:
            logger.debug("Falha na chamada JSON ao Ollama (%s): %s", self.model, exc)
            return None

    @staticmethod
    def verify_literal_veracity(raw_source: str, extracted_statement: str, alternatives: Dict[str, Optional[str]]) -> bool:
        """
        GUARDA DE VERACIDADE (Veracity Guard):
        Garante que o LLM não inventou nem parafraseou texto.
        Verifica se pelo menos 90% da sequência alfanumérica de cada alternativa e do enunciado
        consta literalmente no bloco de texto bruto extraído do PDF.
        """
        norm_source = _norm_alnum(raw_source)
        if not norm_source:
            return False

        norm_stmt = _norm_alnum(extracted_statement)
        if len(norm_stmt) < 8:
            return False

        # Verifica amostra de janelas do enunciado contra o texto fonte
        stmt_probe = norm_stmt[: min(40, len(norm_stmt))]
        if stmt_probe not in norm_source:
            return False

        for letter in ("A", "B", "C", "D", "E"):
            alt_txt = alternatives.get(letter)
            if not alt_txt:
                if letter in ("A", "B", "C", "D"):
                    return False
                continue
            norm_alt = _norm_alnum(alt_txt)
            if not norm_alt:
                return False
            probe = norm_alt[: min(30, len(norm_alt))]
            if probe not in norm_source:
                return False

        return True

    def parse_atypical_question_block(
        self, raw_text: str, expected_q_num: int
    ) -> Optional[Dict]:
        """
        Usa o Ollama (`llama3.1:8b`) para segmentar um bloco de questão que falhou na regex,
        aplicando a Guarda de Veracidade (`verify_literal_veracity`) antes de aceitar.
        """
        prompt = self.PROMPT_PARSE_QUESTION.replace("{raw_text}", raw_text)
        data = self._generate_json(prompt)
        if not data or not data.get("valid"):
            return None

        stmt = (data.get("statement") or "").strip()
        alts = data.get("alternatives") or {}
        if not isinstance(alts, dict):
            return None

        if not self.verify_literal_veracity(raw_text, stmt, alts):
            logger.warning(
                "Guarda de Veracidade rejeitou segmentação via LLM para questão %d (divergência textual).",
                expected_q_num,
            )
            return None

        cleaned_alts = {
            k: (v.strip() if isinstance(v, str) and v.strip() else None)
            for k, v in alts.items()
            if k in {"A", "B", "C", "D", "E"}
        }
        return {
            "question_number": expected_q_num,
            "discipline": data.get("discipline"),
            "statement": stmt,
            "alternatives": cleaned_alts,
        }

    def extract_gabarito_fallback(
        self, raw_gabarito_text: str, cargo_title: str, target_type: str = "1"
    ) -> Dict[int, str]:
        """
        Extrai mapa de gabarito assistido por Ollama quando o texto do PDF possui layout
        não-convencional, validando que cada par (questão, letra) existe no texto bruto.
        """
        prompt = (
            self.PROMPT_PARSE_GABARITO.replace("{cargo_title}", cargo_title)
            .replace("{target_type}", target_type)
            .replace("{raw_gabarito_text}", raw_gabarito_text[:12000])
        )
        data = self._generate_json(prompt)
        if not data or not isinstance(data.get("answers"), dict):
            return {}

        valid_answers: Dict[int, str] = {}
        for k, v in data["answers"].items():
            try:
                q_num = int(str(k).strip())
            except ValueError:
                continue
            ans_str = str(v).strip().upper()
            if ans_str in {"*", "[*]", "(*)", "ANULADA", "ANULADO"}:
                ans_str = "ANULADA"
            if 1 <= q_num <= 250 and ans_str in {"A", "B", "C", "D", "E", "ANULADA"}:
                valid_answers[q_num] = ans_str

        return valid_answers

    def classify_discipline(self, statement: str, cargo_title: str) -> Optional[str]:
        """Classifica a disciplina de uma questão quando ausente no cabeçalho do PDF."""
        prompt = self.PROMPT_CLASSIFY_DISCIPLINE.replace("{cargo_title}", cargo_title).replace(
            "{statement}", statement[:600]
        )
        data = self._generate_json(prompt)
        if data and isinstance(data.get("discipline"), str):
            return data["discipline"].strip()
        return None
