from __future__ import annotations

import html
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_LAYOUT_MODEL = "sahilchachra/unlimited-ocr-mxfp8-mlx"
DEFAULT_LAYOUT_INSTRUCTION = "<|grounding|>Convert the document to markdown."
MODEL_INPUT_SIZE = 1024
MODEL_INPUT_MAX_EDGE = 1600


@dataclass(frozen=True)
class LayoutBlock:
    kind: str
    bbox: tuple[float, float, float, float]
    text: str


GROUNDING_PATTERN = re.compile(
    r"(?:<\|ref\|>(?P<ref>.*?)<\|/ref\|>\s*)?"
    r"<\|det\|>\s*"
    r"(?:(?P<label>[^\[<]*?)\s*)?"
    r"\[+\s*(?P<x1>-?\d+(?:\.\d+)?)\s*,\s*"
    r"(?P<y1>-?\d+(?:\.\d+)?)\s*,\s*"
    r"(?P<x2>-?\d+(?:\.\d+)?)\s*,\s*"
    r"(?P<y2>-?\d+(?:\.\d+)?)\s*\]+\s*<\|/det\|>",
    re.DOTALL,
)

VISUAL_KIND_PARTS = {
    "chart",
    "diagram",
    "figure",
    "graph",
    "illustration",
    "image",
    "photo",
    "plot",
    "table",
}
HEADING_KIND_PARTS = {"header", "heading", "section", "title"}
CAPTION_KIND_PARTS = {"caption", "footnote"}
PAGE_NUMBER_KINDS = {"page-number", "page-num", "pagenumber"}


def normalize_kind(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "text"


def parse_grounded_layout(raw_output: str) -> list[LayoutBlock]:
    """Parse both Unlimited-OCR and DeepSeek-OCR grounding token variants."""
    raw_output = normalize_tokenizer_artifacts(raw_output)
    matches = list(GROUNDING_PATTERN.finditer(raw_output))
    blocks: list[LayoutBlock] = []
    for index, match in enumerate(matches):
        label = (match.group("ref") or match.group("label") or "text").strip()
        content_end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_output)
        content = clean_model_text(raw_output[match.end() : content_end])
        bbox = tuple(float(match.group(name)) for name in ("x1", "y1", "x2", "y2"))
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        blocks.append(LayoutBlock(kind=normalize_kind(label), bbox=bbox, text=content))
    return blocks


def clean_model_text(value: str) -> str:
    value = normalize_tokenizer_artifacts(value)
    value = value.replace("<|endofsentence|>", "").replace("<|endoftext|>", "")
    value = re.sub(r"^\s*```(?:markdown|html)?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*```\s*$", "", value)
    return value.strip()


def normalize_tokenizer_artifacts(value: str) -> str:
    return value.replace("Ġ", " ").replace("Ċ", "\n")


def kind_contains(kind: str, candidates: Iterable[str]) -> bool:
    parts = set(kind.split("-"))
    return bool(parts.intersection(candidates))


def is_visual_block(block: LayoutBlock) -> bool:
    return not is_caption_block(block) and kind_contains(block.kind, VISUAL_KIND_PARTS)


def is_heading_block(block: LayoutBlock) -> bool:
    return kind_contains(block.kind, HEADING_KIND_PARTS)


def is_caption_block(block: LayoutBlock) -> bool:
    return kind_contains(block.kind, CAPTION_KIND_PARTS)


def is_page_number_block(block: LayoutBlock) -> bool:
    return block.kind in PAGE_NUMBER_KINDS


def relative_asset_src(path: Path, html_parent: Path) -> str:
    return Path(os.path.relpath(path.resolve(), html_parent.resolve())).as_posix()


def scaled_bbox(
    bbox: tuple[float, float, float, float],
    image_width: int,
    image_height: int,
    coordinate_width: float = MODEL_INPUT_SIZE,
    coordinate_height: float = MODEL_INPUT_SIZE,
    padding: int = 18,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    scale_x = image_width / coordinate_width
    scale_y = image_height / coordinate_height
    left = max(0, int(x1 * scale_x) - padding)
    top = max(0, int(y1 * scale_y) - padding)
    right = min(image_width, int(x2 * scale_x + 0.999) + padding)
    bottom = min(image_height, int(y2 * scale_y + 0.999) + padding)
    return left, top, right, bottom


def pdf_bbox_to_model(
    bbox: tuple[float, float, float, float], page_width: float, page_height: float
) -> tuple[float, float, float, float]:
    return (
        bbox[0] * MODEL_INPUT_SIZE / page_width,
        bbox[1] * MODEL_INPUT_SIZE / page_height,
        bbox[2] * MODEL_INPUT_SIZE / page_width,
        bbox[3] * MODEL_INPUT_SIZE / page_height,
    )


def bbox_area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def intersection_area(
    first: tuple[float, float, float, float], second: tuple[float, float, float, float]
) -> float:
    width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    return width * height


def deduplicate_visual_blocks(blocks: list[LayoutBlock]) -> list[LayoutBlock]:
    kept: list[LayoutBlock] = []
    for block in sorted(blocks, key=lambda item: (item.kind != "table", -bbox_area(item.bbox))):
        duplicate = False
        for existing in kept:
            overlap = intersection_area(block.bbox, existing.bbox)
            smaller = min(bbox_area(block.bbox), bbox_area(existing.bbox))
            if smaller and overlap / smaller >= 0.72:
                duplicate = True
                break
        if not duplicate:
            kept.append(block)
    return kept


def combine_native_text_with_grounded_visuals(
    native_blocks: list[LayoutBlock], grounded_blocks: list[LayoutBlock]
) -> list[LayoutBlock]:
    """Keep selectable PDF text while cropping each detected visual as one intact region."""
    visuals = deduplicate_visual_blocks(
        [block for block in grounded_blocks if is_visual_block(block)]
    )
    native_text = []
    for block in native_blocks:
        if is_visual_block(block):
            continue
        block_area = bbox_area(block.bbox)
        if block_area and any(
            intersection_area(block.bbox, visual.bbox) / block_area >= 0.45
            for visual in visuals
        ):
            continue
        native_text.append(block)
    return order_layout_blocks(native_text + visuals)


def order_layout_blocks(blocks: list[LayoutBlock]) -> list[LayoutBlock]:
    """Reflow common two-column paper pages into article reading order."""
    midpoint = MODEL_INPUT_SIZE / 2
    full_width: list[LayoutBlock] = []
    column: list[LayoutBlock] = []
    for block in blocks:
        width = block.bbox[2] - block.bbox[0]
        crosses_midpoint = block.bbox[0] < midpoint < block.bbox[2]
        if crosses_midpoint and width >= MODEL_INPUT_SIZE * 0.42:
            full_width.append(block)
        else:
            column.append(block)

    def column_order(items: list[LayoutBlock]) -> list[LayoutBlock]:
        left = [item for item in items if (item.bbox[0] + item.bbox[2]) / 2 < midpoint]
        right = [item for item in items if item not in left]
        key = lambda item: (item.bbox[1], item.bbox[0])
        if len(left) >= 2 and len(right) >= 2:
            return sorted(left, key=key) + sorted(right, key=key)
        return sorted(items, key=key)

    ordered: list[LayoutBlock] = []
    remaining = list(column)
    for boundary in sorted(full_width, key=lambda item: (item.bbox[1], item.bbox[0])):
        boundary_center = (boundary.bbox[1] + boundary.bbox[3]) / 2
        before = [item for item in remaining if (item.bbox[1] + item.bbox[3]) / 2 < boundary_center]
        ordered.extend(column_order(before))
        before_ids = {id(item) for item in before}
        remaining = [item for item in remaining if id(item) not in before_ids]
        ordered.append(boundary)
    ordered.extend(column_order(remaining))
    return ordered


def extract_native_pdf_layout(pdf_path: Path, page_index: int) -> list[LayoutBlock]:
    """Extract text, table, image, and vector-figure boxes from a born-digital PDF."""
    try:
        import pymupdf
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised by CLI validation
        raise RuntimeError("native PDF layout detection requires PyMuPDF; install it with: uv add pymupdf") from exc

    with pymupdf.open(pdf_path) as document:
        page = document[page_index]
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        page_area = page_width * page_height
        page_dict = page.get_text("dict", sort=True)

        text_candidates: list[LayoutBlock] = []
        visual_candidates: list[LayoutBlock] = []
        for raw_block in page_dict.get("blocks", []):
            raw_bbox = tuple(float(value) for value in raw_block.get("bbox", (0, 0, 0, 0)))
            if raw_block.get("type") == 1:
                visual_candidates.append(
                    LayoutBlock("figure", pdf_bbox_to_model(raw_bbox, page_width, page_height), "")
                )
                continue
            if raw_block.get("type") != 0:
                continue
            lines: list[str] = []
            sizes: list[float] = []
            fonts: list[str] = []
            for line in raw_block.get("lines", []):
                spans = line.get("spans", [])
                line_text = "".join(str(span.get("text", "")) for span in spans).strip()
                if line_text:
                    lines.append(line_text)
                sizes.extend(float(span.get("size", 0)) for span in spans)
                fonts.extend(str(span.get("font", "")) for span in spans)
            text = " ".join(lines).strip()
            if not text:
                continue
            max_size = max(sizes, default=0)
            bold = any("bold" in font.lower() or "bx" in font.lower() for font in fonts)
            kind = "title" if len(text) <= 180 and (max_size >= 12 or (bold and max_size >= 10)) else "text"
            text_candidates.append(
                LayoutBlock(kind, pdf_bbox_to_model(raw_bbox, page_width, page_height), text)
            )

        try:
            tables = page.find_tables().tables
        except Exception:
            tables = []
        for table in tables:
            raw_bbox = tuple(float(value) for value in table.bbox)
            visual_candidates.append(
                LayoutBlock("table", pdf_bbox_to_model(raw_bbox, page_width, page_height), "")
            )

        try:
            drawing_rects = page.cluster_drawings()
        except Exception:
            drawing_rects = []
        for rect in drawing_rects:
            raw_bbox = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
            width = raw_bbox[2] - raw_bbox[0]
            height = raw_bbox[3] - raw_bbox[1]
            area = width * height
            if (
                area < page_area * 0.008
                or area > page_area * 0.82
                or width < page_width * 0.15
                or height < page_height * 0.04
            ):
                continue
            visual_candidates.append(
                LayoutBlock("figure", pdf_bbox_to_model(raw_bbox, page_width, page_height), "")
            )

    visuals = deduplicate_visual_blocks(visual_candidates)
    visible_text = []
    for block in text_candidates:
        block_area = bbox_area(block.bbox)
        if block_area and any(intersection_area(block.bbox, visual.bbox) / block_area >= 0.55 for visual in visuals):
            continue
        visible_text.append(block)
    return order_layout_blocks(visible_text + visuals)


def render_layout_page(
    blocks: list[LayoutBlock],
    page_image: Path,
    assets_dir: Path,
    html_parent: Path,
    paper_id: str,
    page_num: int,
) -> tuple[str, int]:
    try:
        from PIL import Image
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised by CLI validation
        raise RuntimeError("layout crop rendering requires Pillow; install it with: uv add pillow") from exc

    rendered: list[str] = []
    visual_count = 0
    crop_dir = assets_dir / "layout"
    with Image.open(page_image) as source_image:
        for index, block in enumerate(blocks, start=1):
            block_id = f"p{page_num}-layout-{index}"
            if is_visual_block(block):
                crop_box = scaled_bbox(block.bbox, source_image.width, source_image.height)
                if crop_box[2] - crop_box[0] < 8 or crop_box[3] - crop_box[1] < 8:
                    continue
                crop_dir.mkdir(parents=True, exist_ok=True)
                crop_path = crop_dir / f"page-{page_num:04d}-{index:03d}-{block.kind}.png"
                source_image.crop(crop_box).save(crop_path, format="PNG")
                src = relative_asset_src(crop_path, html_parent)
                alt = html.escape(f"PDF page {page_num} {block.kind}", quote=True)
                rendered.append(
                    f'<figure class="ltx_figure codex_pdf_layout_visual codex_pdf_layout_{block.kind}" '
                    f'id="{block_id}" data-layout-bbox="{html.escape(str(block.bbox), quote=True)}">'
                    f'<img class="ltx_graphics" src="{html.escape(src, quote=True)}" alt="{alt}">'
                    "</figure>"
                )
                visual_count += 1
                continue

            text = block.text.strip()
            if not text:
                continue
            escaped = html.escape(text)
            if is_heading_block(block):
                rendered.append(f'<h2 class="ltx_title ltx_title_section" id="{block_id}">{escaped}</h2>')
            elif is_caption_block(block):
                rendered.append(
                    f'<figcaption class="ltx_caption codex_pdf_layout_caption" id="{block_id}">{escaped}</figcaption>'
                )
            else:
                rendered.append(
                    f'<div class="ltx_para codex_pdf_layout_text" id="{block_id}"><p class="ltx_p">{escaped}</p></div>'
                )
    return "\n".join(rendered), visual_count


class UnlimitedOCRMLX:
    def __init__(self, model_id: str = DEFAULT_LAYOUT_MODEL, max_tokens: int = 8192) -> None:
        try:
            from mlx_vlm import generate, load
            from mlx_vlm.prompt_utils import apply_chat_template
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Unlimited-OCR MLX layout detection requires mlx-vlm; install project dependencies with: uv sync"
            ) from exc
        self._generate = generate
        self._apply_chat_template = apply_chat_template
        self._model_overlay: tempfile.TemporaryDirectory[str] | None = None
        model_path = self._compatible_model_path(model_id)
        self._model, self._processor = load(str(model_path))
        self.model_id = model_id
        self.max_tokens = max_tokens

    def _compatible_model_path(self, model_id: str) -> Path:
        """Route patched quantizations through mlx-vlm's current Unlimited-OCR implementation."""
        try:
            from huggingface_hub import snapshot_download
        except ModuleNotFoundError:
            return Path(model_id)

        source = Path(model_id)
        if not source.exists():
            source = Path(snapshot_download(model_id))
        config_path = source / "config.json"
        if not config_path.exists():
            return source
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if (
            config.get("model_type") != "deepseekocr"
            or "UnlimitedOCRForCausalLM" not in config.get("architectures", [])
        ):
            return source

        self._model_overlay = tempfile.TemporaryDirectory(prefix="unlimited-ocr-mlx-overlay-")
        overlay = Path(self._model_overlay.name)
        for item in source.iterdir():
            if item.is_dir() or item.name in {"config.json", "processor_config.json", "tokenizer_config.json"}:
                continue
            os.symlink(item, overlay / item.name)

        config["model_type"] = "unlimited-ocr"
        (overlay / "config.json").write_text(json.dumps(config), encoding="utf-8")

        processor_path = source / "processor_config.json"
        processor = json.loads(processor_path.read_text(encoding="utf-8")) if processor_path.exists() else {}
        processor["processor_class"] = "UnlimitedOCRHFProcessor"
        processor["sft_format"] = "unlimitedocr"
        (overlay / "processor_config.json").write_text(json.dumps(processor), encoding="utf-8")

        tokenizer_path = source / "tokenizer_config.json"
        tokenizer = json.loads(tokenizer_path.read_text(encoding="utf-8")) if tokenizer_path.exists() else {}
        tokenizer["tokenizer_class"] = "LlamaTokenizerFast"
        tokenizer.pop("processor_class", None)
        (overlay / "tokenizer_config.json").write_text(json.dumps(tokenizer), encoding="utf-8")
        return overlay

    def parse_image(self, image_path: Path) -> tuple[list[LayoutBlock], str]:
        prompt = self._apply_chat_template(
            self._processor,
            self._model.config,
            DEFAULT_LAYOUT_INSTRUCTION,
            num_images=1,
        )
        model_image_path = image_path
        normalized_image_dir: tempfile.TemporaryDirectory[str] | None = None
        try:
            from PIL import Image

            with Image.open(image_path) as source_image:
                if max(source_image.size) > MODEL_INPUT_MAX_EDGE:
                    normalized_image_dir = tempfile.TemporaryDirectory(
                        prefix="unlimited-ocr-input-"
                    )
                    model_image_path = Path(normalized_image_dir.name) / "page.png"
                    normalized = source_image.convert("RGB")
                    normalized.thumbnail(
                        (MODEL_INPUT_MAX_EDGE, MODEL_INPUT_MAX_EDGE),
                        Image.Resampling.LANCZOS,
                    )
                    normalized.save(model_image_path, format="PNG")

            response: Any = self._generate(
                self._model,
                self._processor,
                prompt=prompt,
                image=[str(model_image_path)],
                max_tokens=self.max_tokens,
                verbose=False,
            )
        finally:
            if normalized_image_dir is not None:
                normalized_image_dir.cleanup()
        raw_output = getattr(response, "text", response)
        if not isinstance(raw_output, str):
            raw_output = str(raw_output)
        blocks = parse_grounded_layout(raw_output)
        if not blocks:
            raise RuntimeError("Unlimited-OCR returned no grounded layout blocks")
        return blocks, raw_output
