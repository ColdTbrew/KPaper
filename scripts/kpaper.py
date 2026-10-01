#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import html
import http.server
import importlib.util
import json
import os
import re
import shlex
import socketserver
import sys
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import apply_paper_viewer_style  # noqa: E402
import pdf_layout  # noqa: E402
import translate_html_blocks  # noqa: E402


DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_MAX_CHARS = 5000


def write_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def emit(args: argparse.Namespace, payload: dict[str, Any], message: str) -> None:
    if args.json:
        write_json(payload)
    else:
        print(message)


def emit_progress(
    enabled: bool,
    stage: str,
    current: int,
    total: int,
    label: str,
    detail: str = "",
) -> None:
    if not enabled:
        return
    payload = {
        "stage": stage,
        "current": current,
        "total": total,
        "label": label,
        "detail": detail,
    }
    print("KPAPER_PROGRESS " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def fail(message: str, hint: str | None = None, code: int = 2) -> None:
    print(f"error: {message}", file=sys.stderr)
    if hint:
        print(f"hint: {hint}", file=sys.stderr)
    raise SystemExit(code)


def load_env_file(path: Path) -> None:
    translate_html_blocks.load_env_file(path)


def slug_from_input(path: Path) -> str:
    name = path.name
    if name.endswith(".source.html"):
        return name[: -len(".source.html")]
    if name.endswith(".html"):
        return name[: -len(".html")]
    return path.stem


def safe_filename_stem(text: str, fallback: str, max_length: int = 140) -> str:
    normalized = html.unescape(" ".join(text.split())).strip().lower()
    mapped = [char if char.isalnum() else "-" for char in normalized]
    collapsed = re.sub(r"-+", "-", "".join(mapped)).strip("-")
    if not collapsed:
        collapsed = fallback
    return collapsed[:max_length].rstrip("-") or fallback


def title_from_html(path: Path) -> str:
    if not path.exists():
        return ""
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "lxml")
    for selector in ("title", "h1.ltx_title_document", ".ltx_title_document"):
        element = soup.select_one(selector)
        if element:
            title = element.get_text(" ", strip=True)
            if title:
                return title
    return ""


def output_stem_from_source(input_path: Path, paper_id: str) -> str:
    title = title_from_html(input_path)
    return safe_filename_stem(title, fallback=paper_id)


def bilingual_path_for_output(output_path: Path) -> Path:
    name = output_path.name
    if name.endswith(".ko-en.paper.html"):
        return output_path
    if name.endswith(".ko.paper.html"):
        stem = name[: -len(".ko.paper.html")]
    elif name.endswith(".html"):
        stem = name[: -len(".html")]
    else:
        stem = output_path.stem
    return output_path.with_name(f"{stem}.ko-en.paper.html")


def resolve_paths(args: argparse.Namespace) -> dict[str, Path]:
    paper_id = args.paper_id
    input_path = Path(args.input) if args.input else None
    if not paper_id and input_path:
        paper_id = slug_from_input(input_path)
    if not paper_id:
        fail(
            "--paper-id is required when --input is not provided",
            "try: scripts/kpaper.py translate --paper-id my-paper --source-url https://ar5iv.labs.arxiv.org/html/...",
        )

    input_path = input_path or Path("inputs") / f"{paper_id}.source.html"
    output_path = (
        Path(args.output)
        if args.output
        else Path("outputs") / f"{output_stem_from_source(input_path, paper_id)}.ko-en.paper.html"
    )
    bilingual_output = (
        Path(args.bilingual_output)
        if args.bilingual_output
        else bilingual_path_for_output(output_path)
    )
    output_path = translate_html_blocks.canonical_output_path(bilingual_output)
    bilingual_output = output_path
    cache_arg = getattr(args, "cache", "")
    progress_arg = getattr(args, "progress_log", "")
    cache_path = Path(cache_arg) if cache_arg else Path("outputs/cache") / f"{paper_id}.masked.translation.jsonl"
    progress_path = (
        Path(progress_arg)
        if progress_arg
        else Path("outputs/cache") / f"{paper_id}.masked.progress.log"
    )
    return {
        "input": input_path,
        "output": output_path,
        "bilingual_output": bilingual_output,
        "cache": cache_path,
        "progress_log": progress_path,
    }


def fetch_source(source_url: str, output_path: Path, force: bool, dry_run: bool) -> dict[str, Any]:
    if output_path.exists() and not force:
        return {
            "status": "exists",
            "source_url": source_url,
            "output": str(output_path),
            "bytes": output_path.stat().st_size,
        }
    if dry_run:
        return {
            "status": "dry_run",
            "source_url": source_url,
            "output": str(output_path),
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(source_url, timeout=60)
    response.raise_for_status()
    output_path.write_text(response.text, encoding="utf-8")
    return {
        "status": "wrote",
        "source_url": source_url,
        "output": str(output_path),
        "bytes": output_path.stat().st_size,
    }


def normalize_huggingface_pdf_url(url: str) -> str:
    return re.sub(r"/blob/([^?#]+)", r"/resolve/\1", url)


def download_binary(source_url: str, output_path: Path, force: bool, dry_run: bool) -> dict[str, Any]:
    url = normalize_huggingface_pdf_url(source_url)
    if output_path.exists() and not force:
        return {
            "status": "exists",
            "source_url": source_url,
            "resolved_url": url,
            "output": str(output_path),
            "bytes": output_path.stat().st_size,
        }
    if dry_run:
        return {
            "status": "dry_run",
            "source_url": source_url,
            "resolved_url": url,
            "output": str(output_path),
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, timeout=120, stream=True) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "pdf" not in content_type.lower() and not url.lower().split("?", 1)[0].endswith(".pdf"):
            fail(
                f"URL did not return a PDF content type: {content_type or 'unknown'}",
                "for Hugging Face files use the /resolve/main/... URL or pass the original /blob/main/... URL to pdf-import",
            )
        with output_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    return {
        "status": "wrote",
        "source_url": source_url,
        "resolved_url": url,
        "output": str(output_path),
        "bytes": output_path.stat().st_size,
    }


class ArxivRef(NamedTuple):
    arxiv_id: str
    version: str  # "" or "v7"

    @property
    def versioned(self) -> str:
        return f"{self.arxiv_id}{self.version}"

    @property
    def paper_id(self) -> str:
        return "arxiv-" + re.sub(r"[./]", "-", self.arxiv_id)


ARXIV_HOSTS = {
    "arxiv.org",
    "www.arxiv.org",
    "export.arxiv.org",
    "ar5iv.org",
    "www.ar5iv.org",
    "ar5iv.labs.arxiv.org",
}
ARXIV_ID_RE = re.compile(
    r"^(?P<id>\d{4}\.\d{4,5}|[a-z][a-z\-]*(?:\.[A-Za-z]{2})?/\d{7})(?P<version>v\d+)?$"
)
ARXIV_PATH_RE = re.compile(r"^/(?:abs|pdf|html)/(?P<ref>.+?)(?:\.pdf)?/?$")
# A converted paper is a LaTeXML article holding at least an abstract plus one body
# paragraph; error and placeholder pages do not.
MIN_PAPER_HTML_PARAGRAPHS = 2


def parse_arxiv_reference(value: str) -> ArxivRef | None:
    """Accept an arXiv abs/pdf/html/ar5iv URL, `arXiv:<id>`, or a bare id."""
    text = re.sub(r"^arxiv:", "", value.strip(), flags=re.IGNORECASE)
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"}:
        if (parsed.hostname or "").lower() not in ARXIV_HOSTS:
            return None
        path_match = ARXIV_PATH_RE.match(parsed.path)
        if not path_match:
            return None
        text = path_match.group("ref")
    match = ARXIV_ID_RE.match(text)
    if not match:
        return None
    return ArxivRef(match.group("id"), match.group("version") or "")


def arxiv_html_candidates(ref: ArxivRef) -> list[tuple[str, str]]:
    """Official arXiv HTML first, then the ar5iv conversion of the same paper."""
    return [
        ("arxiv-html", f"https://arxiv.org/html/{ref.versioned}"),
        ("ar5iv", f"https://ar5iv.labs.arxiv.org/html/{ref.versioned}"),
    ]


def arxiv_pdf_url(ref: ArxivRef) -> str:
    return f"https://arxiv.org/pdf/{ref.versioned}"


def looks_like_paper_html(text: str) -> bool:
    if "ltx_document" not in text:
        return False
    article = BeautifulSoup(text, "lxml").select_one("article.ltx_document")
    if article is None:
        return False
    paragraphs = [node for node in article.select(".ltx_p") if node.get_text(strip=True)]
    return len(paragraphs) >= MIN_PAPER_HTML_PARAGRAPHS


def try_fetch_paper_html(url: str) -> tuple[str | None, str]:
    """Return (html, "") for a usable converted paper, otherwise (None, reason)."""
    try:
        response = requests.get(url, timeout=60)
    except requests.RequestException as exc:
        return None, f"request failed: {exc.__class__.__name__}"
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}"
    text = response.content.decode("utf-8", errors="replace")
    if not looks_like_paper_html(text):
        return None, "response is not converted paper HTML"
    return text, ""


def fetch_arxiv_title(ref: ArxivRef) -> str:
    """Best-effort title from the abstract page; empty when unavailable."""
    try:
        response = requests.get(f"https://arxiv.org/abs/{ref.versioned}", timeout=30)
        response.raise_for_status()
    except requests.RequestException:
        return ""
    meta = BeautifulSoup(response.content, "lxml").find("meta", attrs={"name": "citation_title"})
    content = meta.get("content") if meta else ""
    return " ".join(content.split()) if isinstance(content, str) else ""


def load_liteparse() -> Any:
    try:
        from liteparse import LiteParse  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        fail(
            "pdf-import requires the Python liteparse package, but it is not installed",
            "install it with: uv add liteparse",
            code=1,
        )
    return LiteParse


def classify_pdf_for_ocr(pdf_path: Path) -> dict[str, Any]:
    try:
        import pdf_inspector  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        fail(
            "automatic PDF routing requires pdf-inspector, but it is not installed",
            "install project dependencies with: uv sync",
            code=1,
        )
    result = pdf_inspector.classify_pdf(str(pdf_path))
    return {
        "pdf_type": result.pdf_type,
        "confidence": float(result.confidence),
        "page_count": int(result.page_count),
        "pages_needing_ocr": [int(page) for page in result.pages_needing_ocr],
    }


def split_text_paragraphs(text: str) -> list[str]:
    paragraphs: list[str] = []
    for chunk in re.split(r"\n\s*\n", text):
        normalized = " ".join(line.strip() for line in chunk.splitlines() if line.strip()).strip()
        if normalized:
            paragraphs.append(normalized)
    return paragraphs


def text_blocks_from_liteparse_page(page: Any) -> list[str]:
    text = getattr(page, "text", "")
    if isinstance(text, str) and text.strip():
        return split_text_paragraphs(text)
    items = getattr(page, "text_items", [])
    blocks = [getattr(item, "text", "").strip() for item in items]
    return [block for block in blocks if block]


def extract_image_layout_with_fallback(
    pdf_path: Path,
    page_index: int,
    page_image: Path,
    layout_engine: Any,
) -> tuple[list[pdf_layout.LayoutBlock], str, str]:
    """Fall back to native PDF geometry when ML layout fails for one page."""
    try:
        blocks, raw_layout = layout_engine.parse_image(page_image)
        return blocks, raw_layout, ""
    except RuntimeError as exc:
        fallback_blocks = pdf_layout.extract_native_pdf_layout(pdf_path, page_index)
        return fallback_blocks, "", str(exc)


def write_liteparse_screenshots(
    shots: list[Any],
    assets_dir: Path,
) -> list[Path]:
    assets_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[Path] = []
    for shot in sorted(shots, key=lambda item: getattr(item, "page_num", 0)):
        page_num = getattr(shot, "page_num", len(image_paths) + 1)
        image_name = f"page-{page_num:04d}.png"
        image_path = assets_dir / image_name
        image_path.write_bytes(getattr(shot, "image_bytes"))
        image_paths.append(image_path)
    return image_paths


def pdf_to_source_html(
    pdf_path: Path,
    html_path: Path,
    assets_dir: Path,
    paper_id: str,
    title: str,
    image_dpi: int,
    max_pages: int,
    layout_backend: str,
    layout_model: str,
    layout_max_tokens: int,
    dry_run: bool,
    progress_enabled: bool = False,
) -> dict[str, Any]:
    if dry_run:
        return {
            "status": "dry_run",
            "pdf": str(pdf_path),
            "output": str(html_path),
            "assets_dir": str(assets_dir),
            "image_dpi": image_dpi,
            "max_pages": max_pages,
            "layout_backend": layout_backend,
            "layout_model": layout_model if layout_backend == "unlimited-ocr-mlx" else "",
            "parser": "pdf-inspector+liteparse+pymupdf-layout" if layout_backend == "auto" else (
                "liteparse-python+pymupdf-layout" if layout_backend == "native" else "liteparse-python"
            ),
        }
    if not pdf_path.exists():
        fail(f"PDF not found: {pdf_path}")

    classification: dict[str, Any] | None = None
    ocr_page_indices: set[int] = set()
    effective_layout_backend = layout_backend
    if layout_backend == "auto":
        emit_progress(progress_enabled, "inspect", 0, 1, "PDF 유형 판별 중")
        classification = classify_pdf_for_ocr(pdf_path)
        ocr_page_indices = set(classification["pages_needing_ocr"])
        if not ocr_page_indices:
            effective_layout_backend = "native-text+grounded-visuals"
        elif len(ocr_page_indices) >= classification["page_count"]:
            effective_layout_backend = "unlimited-ocr-mlx"
        else:
            effective_layout_backend = "hybrid"
        emit_progress(
            progress_enabled,
            "inspect",
            1,
            1,
            "PDF 유형 판별 완료",
            f"{classification['pdf_type']} · OCR {len(ocr_page_indices)}페이지",
        )

    LiteParse = load_liteparse()
    parser_kwargs: dict[str, Any] = {
        "dpi": image_dpi,
        "ocr_enabled": False,
        "output_format": "json",
        "quiet": True,
    }
    if max_pages > 0:
        parser_kwargs["max_pages"] = max_pages
    html_path.parent.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)
    page_sections: list[str] = []
    text_blocks = 0
    visual_blocks = 0
    figure_blocks = 0
    table_blocks = 0
    formula_blocks = 0
    layout_fallbacks: list[dict[str, Any]] = []
    layout_engine = (
        pdf_layout.UnlimitedOCRMLX(model_id=layout_model, max_tokens=layout_max_tokens)
        if layout_backend in {"auto", "unlimited-ocr-mlx"}
        else None
    )

    parser = LiteParse(**parser_kwargs)
    parsed = parser.parse(pdf_path)
    page_numbers = list(range(1, max_pages + 1)) if max_pages > 0 else None
    shots = parser.screenshot(pdf_path, page_numbers=page_numbers)
    pages = getattr(parsed, "pages", [])
    image_paths = write_liteparse_screenshots(shots, assets_dir)
    selected_pages = max(len(pages), len(image_paths))
    emit_progress(
        progress_enabled,
        "layout",
        0,
        selected_pages,
        "페이지 구조 분석 시작",
        effective_layout_backend,
    )

    for page_index in range(selected_pages):
        page = pages[page_index] if page_index < len(pages) else {}
        page_image = image_paths[page_index] if page_index < len(image_paths) else None
        if layout_backend != "liteparse" and page_image is not None:
            use_mlx_layout = layout_backend in {"auto", "unlimited-ocr-mlx"}
            if use_mlx_layout and layout_engine is not None:
                grounded_blocks, raw_layout, fallback_reason = extract_image_layout_with_fallback(
                    pdf_path, page_index, page_image, layout_engine
                )
                if fallback_reason:
                    layout_fallbacks.append(
                        {
                            "page": page_index + 1,
                            "from": "unlimited-ocr-mlx",
                            "to": "native" if layout_blocks else "liteparse",
                            "reason": fallback_reason,
                        }
                    )
                grounded_blocks = [
                    block
                    for block in grounded_blocks
                    if not pdf_layout.is_page_number_block(block)
                    and not (
                        page_index > 0
                        and pdf_layout.is_heading_block(block)
                        and block.bbox[1] < 85
                    )
                ]
                if layout_backend == "auto" and page_index not in ocr_page_indices and not fallback_reason:
                    native_blocks = pdf_layout.extract_native_pdf_layout(pdf_path, page_index)
                    layout_blocks = pdf_layout.combine_native_text_with_grounded_visuals(
                        native_blocks, grounded_blocks
                    )
                elif layout_backend == "auto" and page_index not in ocr_page_indices:
                    layout_blocks = [
                        block for block in grounded_blocks if not pdf_layout.is_visual_block(block)
                    ]
                else:
                    layout_blocks = grounded_blocks
            else:
                layout_blocks = pdf_layout.extract_native_pdf_layout(pdf_path, page_index)
                raw_layout = ""
            if layout_blocks:
                table_blocks += sum(1 for block in layout_blocks if "table" in block.kind)
                figure_blocks += sum(
                    1
                    for block in layout_blocks
                    if pdf_layout.is_visual_block(block) and "table" not in block.kind
                    and not pdf_layout.kind_contains(block.kind, {"equation", "formula", "math"})
                )
                formula_blocks += sum(
                    1
                    for block in layout_blocks
                    if any(part in block.kind for part in ("formula", "equation", "math"))
                )
                page_html, page_visuals = pdf_layout.render_layout_page(
                    layout_blocks,
                    page_image=page_image,
                    assets_dir=assets_dir,
                    html_parent=html_path.parent,
                    paper_id=paper_id,
                    page_num=page_index + 1,
                )
                if raw_layout:
                    raw_layout_path = assets_dir / "layout" / f"page-{page_index + 1:04d}.grounding.txt"
                    raw_layout_path.parent.mkdir(parents=True, exist_ok=True)
                    raw_layout_path.write_text(raw_layout, encoding="utf-8")
                text_blocks += sum(1 for block in layout_blocks if not pdf_layout.is_visual_block(block))
                visual_blocks += page_visuals
            else:
                paragraphs = text_blocks_from_liteparse_page(page)
                text_blocks += len(paragraphs)
                image_src = pdf_layout.relative_asset_src(page_image, html_path.parent)
                paragraph_html = "\n".join(
                    f'<div class="ltx_para" id="p{page_index + 1}-{idx + 1}"><p class="ltx_p">{html.escape(text)}</p></div>'
                    for idx, text in enumerate(paragraphs)
                )
                page_html = (
                    f'<figure class="ltx_figure codex_pdf_page_image">'
                    f'<img class="ltx_graphics" src="{html.escape(image_src)}" alt="PDF page {page_index + 1}">'
                    f'</figure>\n{paragraph_html}'
                )
        else:
            paragraphs = text_blocks_from_liteparse_page(page)
            text_blocks += len(paragraphs)
            image_src = pdf_layout.relative_asset_src(page_image, html_path.parent) if page_image else ""
            paragraph_html = "\n".join(
                f'<div class="ltx_para" id="p{page_index + 1}-{idx + 1}"><p class="ltx_p">{html.escape(text)}</p></div>'
                for idx, text in enumerate(paragraphs)
            )
            figure_html = (
                f"""
  <figure class="ltx_figure codex_pdf_page_image">
    <img class="ltx_graphics" src="{html.escape(image_src)}" alt="PDF page {page_index + 1}">
  </figure>
""".rstrip()
                if image_src
                else ""
            )
            page_html = f"{figure_html}\n{paragraph_html}"
        page_label_html = (
            f'<h2 class="ltx_title ltx_title_section">Page {page_index + 1}</h2>'
            if layout_backend == "liteparse"
            else ""
        )
        page_sections.append(
            f"""
<section class="ltx_section codex_pdf_page" id="page-{page_index + 1}">
  {page_label_html}
  {page_html}
</section>
""".strip()
        )
        if layout_backend == "liteparse":
            page_backend = "LiteParse"
        elif layout_backend == "unlimited-ocr-mlx" or page_index in ocr_page_indices:
            page_backend = "Unlimited-OCR"
        elif layout_backend == "auto":
            page_backend = "원문 텍스트 + 원본 그림"
        else:
            page_backend = "원문 텍스트"
        emit_progress(
            progress_enabled,
            "layout",
            page_index + 1,
            selected_pages,
            f"페이지 {page_index + 1}/{selected_pages} 분석",
            page_backend,
        )

    structure_summary = f"그림 {figure_blocks}개 · 표 {table_blocks}개 · 수식 {formula_blocks}개"
    emit_progress(
        progress_enabled,
        "layout",
        selected_pages,
        selected_pages,
        "문서 구조 분석 완료",
        structure_summary,
    )

    document_title = html.escape(title or paper_id)
    page_sections_html = "\n".join(page_sections)
    source_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{document_title}</title>
</head>
<body>
<article class="ltx_document codex_pdf_document">
  <h1 class="ltx_title ltx_title_document">{document_title}</h1>
  {page_sections_html}
</article>
</body>
</html>
"""
    html_path.write_text(source_html, encoding="utf-8")
    return {
        "status": "wrote",
        "pdf": str(pdf_path),
        "output": str(html_path),
        "assets_dir": str(assets_dir),
        "pages": selected_pages,
        "images": len(image_paths),
        "text_blocks": text_blocks,
        "visual_blocks": visual_blocks,
        "figures": figure_blocks,
        "tables": table_blocks,
        "formulas": formula_blocks,
        "parser": "pdf-inspector+liteparse+pymupdf-layout" if layout_backend == "auto" else (
            "liteparse-python+pymupdf-layout" if layout_backend == "native" else "liteparse-python"
        ),
        "ocr_enabled": layout_backend == "unlimited-ocr-mlx" or bool(ocr_page_indices),
        "layout_backend": layout_backend,
        "effective_layout_backend": effective_layout_backend,
        "pdf_classification": classification,
        "layout_model": layout_model if layout_engine is not None else "",
        "layout_fallbacks": layout_fallbacks,
    }


def command_doctor(args: argparse.Namespace) -> None:
    env_path = Path(args.env_file)
    load_env_file(env_path)
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    checks = {
        "cwd": str(Path.cwd()),
        "env_file": str(env_path),
        "env_file_exists": env_path.exists(),
        "openai_api_key_present": bool(api_key),
        "openai_base_url_present": bool(base_url),
        "liteparse_python_present": importlib.util.find_spec("liteparse") is not None,
        "pymupdf_present": importlib.util.find_spec("pymupdf") is not None,
        "pillow_present": importlib.util.find_spec("PIL") is not None,
        "mlx_vlm_present": importlib.util.find_spec("mlx_vlm") is not None,
        "pdf_inspector_present": importlib.util.find_spec("pdf_inspector") is not None,
        "inputs_dir_exists": Path("inputs").exists(),
        "outputs_dir_exists": Path("outputs").exists(),
        "scripts_dir_exists": Path("scripts").exists(),
    }
    ok = all(
        checks[key]
        for key in (
            "env_file_exists",
            "openai_api_key_present",
            "openai_base_url_present",
            "scripts_dir_exists",
        )
    )
    payload = {"ok": ok, "checks": checks}
    if args.json:
        write_json(payload)
    else:
        for key, value in checks.items():
            print(f"{key}: {value}")
        if not ok:
            print("hint: cp .env.example .env && edit .env", file=sys.stderr)
    raise SystemExit(0 if ok else 1)


def command_fetch(args: argparse.Namespace) -> None:
    output = Path(args.output) if args.output else Path("inputs") / f"{args.paper_id}.source.html"
    result = fetch_source(args.source_url, output, args.force, args.dry_run)
    emit(args, {"ok": True, "result": result}, f"{result['status']} {result['output']}")


def import_pdf(
    args: argparse.Namespace,
    paper_id: str,
    pdf_url: str,
    pdf_path: Path,
    html_path: Path,
    assets_dir: Path,
    title: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    fetch_result = None
    if pdf_url:
        fetch_result = download_binary(pdf_url, pdf_path, args.force, args.dry_run)
    import_result = pdf_to_source_html(
        pdf_path=pdf_path,
        html_path=html_path,
        assets_dir=assets_dir,
        paper_id=paper_id,
        title=title,
        image_dpi=args.image_dpi,
        max_pages=args.max_pages,
        layout_backend=args.layout_backend,
        layout_model=args.layout_model,
        layout_max_tokens=args.layout_max_tokens,
        dry_run=args.dry_run,
        progress_enabled=args.progress,
    )
    return fetch_result, import_result


def command_pdf_import(args: argparse.Namespace) -> None:
    paper_id = args.paper_id
    pdf_path = Path(args.pdf) if args.pdf else Path("inputs/pdfs") / f"{paper_id}.pdf"
    html_path = Path(args.output) if args.output else Path("inputs") / f"{paper_id}.source.html"
    assets_dir = Path(args.assets_dir) if args.assets_dir else Path("inputs/assets") / paper_id
    fetch_result, import_result = import_pdf(
        args,
        paper_id=paper_id,
        pdf_url=args.pdf_url,
        pdf_path=pdf_path,
        html_path=html_path,
        assets_dir=assets_dir,
        title=args.title or paper_id,
    )
    payload = {
        "ok": True,
        "dry_run": args.dry_run,
        "fetch": fetch_result,
        "result": import_result,
        "next": {
            "dry_run_translate": f"./kpaper translate --paper-id {paper_id} --dry-run",
            "translate": f"./kpaper translate --paper-id {paper_id}",
        },
    }
    emit(args, payload, f"{import_result['status']} {html_path}")


def command_import(args: argparse.Namespace) -> None:
    ref = parse_arxiv_reference(args.url)
    if ref is None:
        fail(
            f"not an arXiv paper reference: {args.url}",
            "pass an arxiv.org abs/pdf/html or ar5iv URL, or a bare id such as 1706.03762",
        )
    paper_id = args.paper_id or ref.paper_id
    html_path = Path(args.output) if args.output else Path("inputs") / f"{paper_id}.source.html"
    candidates = arxiv_html_candidates(ref) if args.source in {"auto", "html"} else []
    pdf_url = arxiv_pdf_url(ref) if args.source in {"auto", "pdf"} else ""
    base_payload: dict[str, Any] = {
        "ok": True,
        "arxiv_id": ref.versioned,
        "paper_id": paper_id,
        "output": str(html_path),
    }
    translate_command = f"./kpaper translate --paper-id {shlex.quote(paper_id)}"
    if args.output:
        translate_command += f" --input {shlex.quote(str(html_path))}"
    next_steps = {
        "dry_run_translate": f"{translate_command} --dry-run",
        "translate": translate_command,
    }

    if html_path.exists() and not args.force:
        payload = {**base_payload, "status": "exists", "next": next_steps}
        emit(args, payload, f"exists {html_path}")
        return
    if args.dry_run:
        payload = {
            **base_payload,
            "status": "dry_run",
            "html_candidates": [{"route": route, "url": url} for route, url in candidates],
            "pdf_url": pdf_url,
            "next": next_steps,
        }
        emit(args, payload, f"dry_run import {ref.versioned} -> {html_path}")
        return

    attempts: list[dict[str, str]] = []
    for route, url in candidates:
        text, reason = try_fetch_paper_html(url)
        if text is None:
            attempts.append({"route": route, "url": url, "reason": reason})
            continue
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(text, encoding="utf-8")
        payload = {
            **base_payload,
            "status": "wrote",
            "route": route,
            "source_url": url,
            "bytes": html_path.stat().st_size,
            "attempts": attempts,
            "next": next_steps,
        }
        emit(args, payload, f"wrote {html_path} via {route}")
        return

    if not pdf_url:
        details = "; ".join(f"{item['route']}: {item['reason']}" for item in attempts)
        fail(
            f"no HTML version of arXiv:{ref.versioned} is available ({details})",
            "retry without --source html to fall back to the PDF",
        )
    for item in attempts:
        print(f"note: {item['route']} unavailable ({item['reason']}); falling back to PDF", file=sys.stderr)
    fetch_result, import_result = import_pdf(
        args,
        paper_id=paper_id,
        pdf_url=pdf_url,
        pdf_path=Path("inputs/pdfs") / f"{paper_id}.pdf",
        html_path=html_path,
        assets_dir=Path("inputs/assets") / paper_id,
        title=args.title or fetch_arxiv_title(ref) or paper_id,
    )
    payload = {
        **base_payload,
        "status": import_result["status"],
        "route": "pdf",
        "source_url": pdf_url,
        "attempts": attempts,
        "fetch": fetch_result,
        "result": import_result,
        "next": next_steps,
    }
    emit(args, payload, f"{import_result['status']} {html_path} via pdf")


def command_translate(args: argparse.Namespace) -> None:
    paths = resolve_paths(args)
    fetch_result = None
    if args.source_url:
        fetch_result = fetch_source(args.source_url, paths["input"], args.force, args.dry_run)
    if args.dry_run and args.source_url and not paths["input"].exists():
        payload = {
            "ok": True,
            "dry_run": True,
            "note": "source would be fetched before block analysis; run fetch or remove --dry-run to continue",
            "fetch": fetch_result,
            "input": str(paths["input"]),
            "output": str(paths["output"]),
            "bilingual_output": str(paths["bilingual_output"]),
            "cache": str(paths["cache"]),
            "progress_log": str(paths["progress_log"]),
        }
        emit(args, payload, f"dry_run fetch_then_translate {paths['input']} -> {paths['output']}")
        return
    if not paths["input"].exists() and not args.dry_run:
        fail(
            f"input HTML not found: {paths['input']}",
            f"try: scripts/kpaper.py fetch --paper-id {args.paper_id} --source-url https://ar5iv.labs.arxiv.org/html/...",
        )

    translated_args = [
        "--input",
        str(paths["input"]),
        "--output",
        str(paths["output"]),
        "--bilingual-output",
        str(paths["bilingual_output"]),
        "--cache",
        str(paths["cache"]),
        "--progress-log",
        str(paths["progress_log"]),
        "--provider",
        args.provider,
        "--model",
        translate_html_blocks.translation_model(args.provider, args.model),
        "--env-file",
        args.env_file,
        "--max-chars",
        str(args.max_chars),
        "--timeout",
        str(args.timeout),
        "--max-retries",
        str(args.max_retries),
        "--concurrency",
        str(args.concurrency),
    ]
    if args.dry_run:
        translated_args.append("--dry-run")

    if args.json:
        with contextlib.redirect_stdout(sys.stderr):
            translate_html_blocks.main(translated_args)
    else:
        translate_html_blocks.main(translated_args)

    payload = {
        "ok": True,
        "dry_run": args.dry_run,
        "fetch": fetch_result,
        "input": str(paths["input"]),
        "output": str(paths["output"]),
        "bilingual_output": str(paths["bilingual_output"]),
        "cache": str(paths["cache"]),
        "progress_log": str(paths["progress_log"]),
    }
    if args.json:
        write_json(payload)


def command_restyle(args: argparse.Namespace) -> None:
    paths = resolve_paths(args)
    source_path = Path(args.source) if args.source else paths["input"]
    if args.dry_run:
        emit(
            args,
            {
                "ok": True,
                "dry_run": True,
                "input": str(paths["output"]),
                "output": str(paths["output"]),
                "source": str(source_path),
                "bilingual_output": str(paths["bilingual_output"]),
            },
            f"dry_run restyle {paths['output']}",
        )
        return
    restyle_input = paths["output"]
    if not restyle_input.exists():
        legacy = restyle_input.with_name(restyle_input.name.replace(".ko-en.paper.html", ".ko.paper.html"))
        if legacy.exists():
            restyle_input = legacy
    if not restyle_input.exists():
        fail(
            f"translated HTML not found: {paths['output']}",
            f"try: scripts/kpaper.py translate --paper-id {args.paper_id} --input {source_path}",
        )
    if not source_path.exists():
        fail(f"source HTML not found: {source_path}")
    restyle_args = [
        str(restyle_input),
        str(paths["output"]),
        str(source_path),
        "--bilingual-output",
        str(paths["bilingual_output"]),
    ]
    if args.json:
        with contextlib.redirect_stdout(sys.stderr):
            apply_paper_viewer_style.main(restyle_args)
        write_json(
            {
                "ok": True,
                "input": str(paths["output"]),
                "output": str(paths["output"]),
                "source": str(source_path),
                "bilingual_output": str(paths["bilingual_output"]),
            }
        )
    else:
        apply_paper_viewer_style.main(restyle_args)


def command_serve(args: argparse.Namespace) -> None:
    directory = Path(args.directory).resolve()
    if args.json:
        write_json(
            {
                "ok": True,
                "url": f"http://{args.host}:{args.port}/",
                "directory": str(directory),
            }
        )
    handler = lambda *handler_args, **handler_kwargs: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *handler_args,
        directory=str(directory),
        **handler_kwargs,
    )
    with socketserver.TCPServer((args.host, args.port), handler) as httpd:
        if not args.json:
            print(f"serving {directory} at http://{args.host}:{args.port}/")
        httpd.serve_forever()


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="write a deterministic JSON summary to stdout")


def add_path_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--paper-id", default="", help="stable paper id used for default input/output paths")
    parser.add_argument("--input", default="", help="source ar5iv HTML path; defaults to inputs/<paper-id>.source.html")
    parser.add_argument("--output", default="", help="unified reader output path; defaults to outputs/<paper-id>.ko-en.paper.html")
    parser.add_argument(
        "--bilingual-output",
        default="",
        help="bilingual output path; defaults to outputs/<paper-id>.ko-en.paper.html",
    )


def add_pdf_layout_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--progress",
        action="store_true",
        help="stream machine-readable KPAPER_PROGRESS events before the final result",
    )
    parser.add_argument("--title", default="", help="document title for the generated source HTML")
    parser.add_argument("--image-dpi", type=int, default=144)
    parser.add_argument("--max-pages", type=int, default=0, help="limit imported pages; 0 means all pages")
    parser.add_argument(
        "--layout-backend",
        choices=("auto", "native", "liteparse", "unlimited-ocr-mlx"),
        default="auto",
        help="layout source: automatic pdf-inspector routing, Unlimited-OCR MLX, native PDF geometry, or LiteParse",
    )
    parser.add_argument(
        "--layout-model",
        default=pdf_layout.DEFAULT_LAYOUT_MODEL,
        help="Hugging Face model id used by --layout-backend unlimited-ocr-mlx",
    )
    parser.add_argument(
        "--layout-max-tokens",
        type=int,
        default=8192,
        help="maximum grounded layout tokens generated per page",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kpaper",
        description="Agent-aware CLI for structure-preserving Korean paper HTML translation.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor",
        help="check local environment readiness",
        description="Check whether dependencies, directories, and headless API credentials are ready.",
        epilog="example: scripts/kpaper.py doctor --json",
    )
    add_common_flags(doctor)
    doctor.add_argument("--env-file", default=".env")
    doctor.set_defaults(func=command_doctor)

    fetch = subparsers.add_parser(
        "fetch",
        help="download one ar5iv HTML source",
        description="Download a single ar5iv HTML document into inputs/ using explicit CLI flags.",
        epilog="example: scripts/kpaper.py fetch --paper-id my-paper --source-url https://ar5iv.labs.arxiv.org/html/...",
    )
    add_common_flags(fetch)
    fetch.add_argument("--paper-id", required=True)
    fetch.add_argument("--source-url", required=True)
    fetch.add_argument("--output", default="")
    fetch.add_argument("--force", action="store_true", help="overwrite existing input HTML")
    fetch.add_argument("--dry-run", action="store_true", help="show what would be downloaded without writing files")
    fetch.set_defaults(func=command_fetch)

    pdf_import = subparsers.add_parser(
        "pdf-import",
        help="convert a PDF into image-backed source HTML",
        description="Download or read a PDF, render page images with Python LiteParse, extract text blocks, and write inputs/<paper-id>.source.html.",
        epilog=(
            "example: scripts/kpaper.py pdf-import --paper-id deepseek-v4 "
            "--pdf-url https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/resolve/main/DeepSeek_V4.pdf"
        ),
    )
    add_common_flags(pdf_import)
    add_pdf_layout_flags(pdf_import)
    pdf_import.add_argument("--paper-id", required=True)
    pdf_import.add_argument("--pdf-url", default="", help="remote PDF URL; Hugging Face /blob/... URLs are normalized to /resolve/...")
    pdf_import.add_argument("--pdf", default="", help="local PDF path; defaults to inputs/pdfs/<paper-id>.pdf")
    pdf_import.add_argument("--output", default="", help="source HTML path; defaults to inputs/<paper-id>.source.html")
    pdf_import.add_argument("--assets-dir", default="", help="page image directory; defaults to inputs/assets/<paper-id>")
    pdf_import.add_argument("--force", action="store_true", help="overwrite existing downloaded PDF")
    pdf_import.add_argument("--dry-run", action="store_true")
    pdf_import.set_defaults(func=command_pdf_import)

    import_cmd = subparsers.add_parser(
        "import",
        help="import an arXiv paper: HTML first, PDF as fallback",
        description=(
            "Resolve an arXiv link or id to source HTML in inputs/. Tries arxiv.org/html, then ar5iv, "
            "then downloads the PDF and runs the pdf-import layout pipeline."
        ),
        epilog="example: scripts/kpaper.py import https://arxiv.org/abs/1706.03762",
    )
    add_common_flags(import_cmd)
    add_pdf_layout_flags(import_cmd)
    import_cmd.add_argument("url", help="arXiv abs/pdf/html or ar5iv URL, or a bare id such as 1706.03762")
    import_cmd.add_argument("--paper-id", default="", help="defaults to arxiv-<id>, e.g. arxiv-1706-03762")
    import_cmd.add_argument("--output", default="", help="source HTML path; defaults to inputs/<paper-id>.source.html")
    import_cmd.add_argument(
        "--source",
        choices=("auto", "html", "pdf"),
        default="auto",
        help="auto: HTML then PDF fallback; html: never fall back to PDF; pdf: skip HTML",
    )
    import_cmd.add_argument("--force", action="store_true", help="overwrite existing source HTML and downloaded PDF")
    import_cmd.add_argument("--dry-run", action="store_true", help="show the planned routes without any network or file writes")
    import_cmd.set_defaults(func=command_import)

    translate = subparsers.add_parser(
        "translate",
        help="translate one paper into Korean reader HTML",
        description="Translate one source HTML file and write one reader with Korean and parallel views.",
        epilog=(
            "example: scripts/kpaper.py translate --paper-id my-paper "
            "--source-url https://ar5iv.labs.arxiv.org/html/... --dry-run"
        ),
    )
    add_common_flags(translate)
    add_path_flags(translate)
    translate.add_argument("--source-url", default="", help="optional ar5iv URL to fetch before translating")
    translate.add_argument("--force", action="store_true", help="overwrite existing fetched source when --source-url is used")
    translate.add_argument("--cache", default="")
    translate.add_argument("--progress-log", default="")
    translate.add_argument("--provider", choices=("api", "codex"), default="api")
    translate.add_argument("--model", default="", help="Codex default: gpt-6-luna; API default: gpt-5.4-mini")
    translate.add_argument("--env-file", default=".env")
    translate.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    translate.add_argument("--timeout", type=int, default=180)
    translate.add_argument("--max-retries", type=int, default=3)
    translate.add_argument("--concurrency", type=int, default=10)
    translate.add_argument("--dry-run", action="store_true")
    translate.set_defaults(func=command_translate)

    restyle = subparsers.add_parser(
        "restyle",
        help="refresh viewer CSS and restore source tables",
        description="Reapply viewer styling and rebuild bilingual output without making model calls.",
        epilog="example: scripts/kpaper.py restyle --paper-id mmdocrag",
    )
    add_common_flags(restyle)
    add_path_flags(restyle)
    restyle.add_argument("--source", default="", help="source HTML for table restoration; defaults to input path")
    restyle.add_argument("--dry-run", action="store_true")
    restyle.set_defaults(func=command_restyle)

    serve = subparsers.add_parser(
        "serve",
        help="serve the workspace for browser verification",
        description="Start a local static file server for inspecting generated outputs in a browser.",
        epilog="example: scripts/kpaper.py serve --port 8799",
    )
    add_common_flags(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8799)
    serve.add_argument("--directory", default=".")
    serve.set_defaults(func=command_serve)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
