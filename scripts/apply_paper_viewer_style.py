from __future__ import annotations

import sys
from pathlib import Path

from bs4 import BeautifulSoup

from translate_html_blocks import build_bilingual_view, fix_file_viewer_links, inject_style, rebase_local_asset_links, restore_source_references, format_pdf_titles, canonical_output_path, extract_reader_document


def restore_source_tables(translated: BeautifulSoup, source: BeautifulSoup) -> int:
    restored = 0
    source_tables = {
        table.get("id"): table
        for table in source.select("figure.ltx_table")
        if table.get("id")
    }
    for table in translated.select("figure.ltx_table"):
        table_id = table.get("id")
        if not table_id or table_id not in source_tables:
            continue
        table.replace_with(BeautifulSoup(str(source_tables[table_id]), "lxml").find("figure"))
        restored += 1
    return restored


def restore_source_equations(translated: BeautifulSoup, source: BeautifulSoup) -> int:
    """Restore PDF formula crops just as we restore immutable source tables."""
    restored = 0
    for equation in source.select(
        "figure.codex_pdf_layout_equation, figure.codex_pdf_layout_formula, figure.codex_pdf_layout_math"
    ):
        equation_id = equation.get("id")
        target = translated.find(id=equation_id) if equation_id else None
        if target:
            target.replace_with(BeautifulSoup(str(equation), "lxml").find("figure"))
            restored += 1
    return restored


def main(argv: list[str]) -> None:
    bilingual_output = ""
    if "--bilingual-output" in argv:
        idx = argv.index("--bilingual-output")
        try:
            bilingual_output = argv[idx + 1]
        except IndexError as exc:
            raise SystemExit("--bilingual-output requires a path") from exc
        argv = argv[:idx] + argv[idx + 2 :]
    if len(argv) not in {1, 2, 3}:
        raise SystemExit(
            "usage: apply_paper_viewer_style.py INPUT_HTML [OUTPUT_HTML] [SOURCE_HTML_FOR_TABLES] "
            "[--bilingual-output OUT_HTML]"
        )
    input_path = Path(argv[0]).resolve()
    output_path = Path(argv[1]).resolve() if len(argv) == 2 else input_path
    source_path = Path(argv[2]).resolve() if len(argv) == 3 else None
    if len(argv) == 3:
        output_path = Path(argv[1]).resolve()
    output_path = canonical_output_path(Path(bilingual_output).resolve() if bilingual_output else output_path)
    original = BeautifulSoup(input_path.read_text(encoding="utf-8"), "lxml")
    if original.select_one("#codex-panel-ko"):
        soup = extract_reader_document(original, "#codex-panel-ko article.ltx_document")
    else:
        soup = original
    if source_path:
        source_soup = BeautifulSoup(source_path.read_text(encoding="utf-8"), "lxml")
        rebase_local_asset_links(source_soup, source_path.parent, output_path.parent)
    elif original.select_one("#codex-panel-parallel"):
        source_soup = extract_reader_document(original, "#codex-panel-parallel .codex_parallel_column article.ltx_document")
        rebase_local_asset_links(source_soup, input_path.parent, output_path.parent)
    else:
        raise SystemExit("Legacy Korean HTML requires SOURCE_HTML_FOR_TABLES to build the unified reader")
    rebase_local_asset_links(soup, input_path.parent, output_path.parent)
    restored = restore_source_tables(soup, source_soup)
    restore_source_equations(soup, source_soup)
    restored_references = restore_source_references(soup, source_soup)
    format_pdf_titles(soup, source_soup)
    bilingual = build_bilingual_view(soup, source_soup)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(str(bilingual), encoding="utf-8")
    print(f"wrote {output_path} restored_tables={restored} restored_references={restored_references}")


if __name__ == "__main__":
    main(sys.argv[1:])
