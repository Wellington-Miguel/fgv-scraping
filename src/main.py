"""
CLI Principal para Coleta (Crawler) e Processamento (Parser) de Provas da FGV Conhecimento.

Exemplos de uso:
    # Coletar até 5 concursos e processar os PDFs em tempo real
    python -m src.main --limit 5

    # Coletar e processar um concurso específico pela URL
    python -m src.main --url https://conhecimento.fgv.br/concursos/cnu2024

    # Executar apenas o parser nos PDFs já baixados em ./data/raw
    python -m src.main --only-parse
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.parser_fgv import FGVParser


def configure_logging(verbose: bool = False) -> None:
    """Configura o sistema de logs com formatação padronizada."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fgv-scraper",
        description="Coletor e Parser de Provas Objetivas (Tipo 1) e Gabaritos da FGV Conhecimento.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limite máximo de concursos a coletar da listagem da FGV.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Número máximo de páginas da listagem de concursos a percorrer (padrão: sem limite).",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="URL de um concurso específico na FGV (ex: https://conhecimento.fgv.br/concursos/...).",
    )
    parser.add_argument(
        "--only-parse",
        action="store_true",
        help="Pula a etapa de download (crawler) e executa apenas o parser nos PDFs em ./data/raw.",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("./data/raw"),
        help="Diretório base para salvar/ler os PDFs brutos (padrão: ./data/raw).",
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=Path("./data/processed"),
        help="Diretório base para salvar os JSONs e imagens recortadas (padrão: ./data/processed).",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Executa o navegador Chromium com interface visível (útil para depuração).",
    )
    parser.add_argument(
        "--ollama-fallback",
        action="store_true",
        help="Ativa o fallback local via Ollama (llama3.1:8b) com Guarda de Veracidade para blocos atípicos.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Ativa logs detalhados em nível DEBUG.",
    )
    return parser


def main() -> int:
    arg_parser = build_arg_parser()
    args = arg_parser.parse_args()
    configure_logging(verbose=args.verbose)

    logger = logging.getLogger("src.main")
    fgv_parser = FGVParser(
        raw_dir=args.raw_dir,
        processed_dir=args.processed_dir,
        use_ollama_fallback=args.ollama_fallback,
    )

    try:
        if not args.only_parse:
            from src.crawler_fgv import FGVCrawler

            logger.info("Iniciando etapa de coleta + parsing incremental (FGVCrawler)...")
            crawler = FGVCrawler(
                raw_dir=args.raw_dir,
                headless=not args.no_headless,
                max_pages=args.max_pages,
            )
            saved_dirs = crawler.run(
                limit=args.limit,
                specific_url=args.url,
                on_cargo_downloaded=fgv_parser.parse_cargo_dir,
            )
            logger.info("Coleta finalizada. %d diretórios de cargos salvos.", len(saved_dirs))
        else:
            logger.info("Modo --only-parse ativado: processando PDFs existentes em %s...", args.raw_dir)
            processed_files = fgv_parser.parse_all()
            logger.info("Processamento concluído. %d arquivos JSON válidos gerados.", len(processed_files))

        return 0

    except KeyboardInterrupt:
        logger.warning("Execução interrompida pelo usuário.")
        return 130
    except Exception as exc:
        logger.critical("Erro fatal durante a execução: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
