from __future__ import annotations

import html
import gc
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


DEFAULT_LAYOUT_MODEL = "sahilchachra/unlimited-ocr-mxfp8-mlx"
DEFAULT_LAYOUT_INSTRUCTION = "<|grounding|>Convert the document to markdown."
# Grounding boxes use 0–1000 coordinates, independent of the vision input size.
LAYOUT_COORDINATE_SIZE = 1000
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
    "equation",
    "formula",
    "math",
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
    coordinate_width: float = LAYOUT_COORDINATE_SIZE,
    coordinate_height: float = LAYOUT_COORDINATE_SIZE,
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
        bbox[0] * LAYOUT_COORDINATE_SIZE / page_width,
        bbox[1] * LAYOUT_COORDINATE_SIZE / page_height,
        bbox[2] * LAYOUT_COORDINATE_SIZE / page_width,
        bbox[3] * LAYOUT_COORDINATE_SIZE / page_height,
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
    midpoint = LAYOUT_COORDINATE_SIZE / 2
    full_width: list[LayoutBlock] = []
    column: list[LayoutBlock] = []
    for block in blocks:
        width = block.bbox[2] - block.bbox[0]
        crosses_midpoint = block.bbox[0] < midpoint < block.bbox[2]
        if crosses_midpoint and width >= LAYOUT_COORDINATE_SIZE * 0.42:
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


def page_requires_grounding(pdf_path: Path, page_index: int) -> bool:
    """Skip VLM work only on clearly ordinary, selectable text pages.

    Be conservative about vector figures and math. Native geometry alone can
    split those into fragments, so they still need the complete grounded crop.
    """
    import pymupdf
    with pymupdf.open(pdf_path) as document:
        page = document[page_index]
        if page.get_images() or page.get_drawings():
            return True
        text = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
        spans = [span for block in text["blocks"] for line in block.get("lines", [])
                 for span in line.get("spans", [])]
        if sum(len(span.get("text", "").strip()) for span in spans) < 40:
            return True
        for span in spans:
            if (span.get("flags", 0) & 1
                    or re.search(r"math|symbol|cmsy|cmmi|cmex|msbm|stix|mtmi", span.get("font", ""), re.I)
                    or re.search(r"[\u0370-\u03ff\u2070-\u209f\u2200-\u22ff]", span.get("text", ""))):
                return True
        return False


def is_blank_pdf_page(pdf_path: Path, page_index: int) -> bool:
    import pymupdf
    with pymupdf.open(pdf_path) as document:
        page = document[page_index]
        return not (page.get_text().strip() or page.get_images() or page.get_drawings())


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
                # Equations are tightly spaced between paragraphs. Keep the full
                # formula and number without pulling adjacent prose into the crop.
                padding = 5 if kind_contains(block.kind, {"equation", "formula", "math"}) else 18
                crop_box = scaled_bbox(block.bbox, source_image.width, source_image.height, padding=padding)
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
    def __init__(
        self, model_id: str = DEFAULT_LAYOUT_MODEL, max_tokens: int = 8192,
        cache_dir: Path | None = None, status: Callable[[str], None] | None = None,
    ) -> None:
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.cache_dir = cache_dir
        self.status = status
        self._model = self._processor = self._mx = None
        self._lock = None
        self._old_cache_limit = None
        self._model_overlay: tempfile.TemporaryDirectory[str] | None = None
        self.stats = {"generated_pages": 0, "cache_hits": 0, "peak_mlx_bytes": 0}

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from ocr_runtime import MLX_CACHE_BYTES, OCRProcessLock, serialize_vision_encoders
        self._lock = OCRProcessLock(self.status)
        self._lock.acquire()
        try:
            if self.status:
                self.status("OCR 모델 준비 중")
            import mlx.core as mx
            import mlx.nn as nn
            from mlx_vlm import generate, load
            from mlx_vlm.prompt_utils import apply_chat_template
            self._mx = mx
            self._old_cache_limit = mx.set_cache_limit(MLX_CACHE_BYTES)
            mx.reset_peak_memory()
            self._generate = generate
            self._apply_chat_template = apply_chat_template
            model_path = self._compatible_model_path(self.model_id)
            self._model, self._processor = load(str(model_path))
            serialize_vision_encoders(self._model, mx, nn)
            mx.eval(self._model.parameters())
            mx.clear_cache()
            self.stats["peak_mlx_bytes"] = mx.get_peak_memory()
        except ModuleNotFoundError as exc:
            self.close()
            raise RuntimeError(
                "Unlimited-OCR MLX layout detection requires mlx-vlm; install project dependencies with: uv sync"
            ) from exc
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Release model weights and Metal allocations before translation starts."""
        self._model = self._processor = None
        try:
            if self._mx is not None:
                gc.collect()
                self._mx.synchronize()
                self._mx.clear_cache()
                if self._old_cache_limit is not None:
                    self._mx.set_cache_limit(self._old_cache_limit)
                    self._old_cache_limit = None
            if self._model_overlay is not None:
                self._model_overlay.cleanup()
                self._model_overlay = None
        finally:
            if self._lock is not None:
                self._lock.release()
                self._lock = None

    def _cache_path(self, image_path: Path) -> Path | None:
        if self.cache_dir is None:
            return None
        # Include the locally cached HF revision (or local model file metadata)
        # so a model update cannot silently reuse old grounding results.
        source = Path(self.model_id)
        if not source.exists():
            from huggingface_hub import try_to_load_from_cache
            config = try_to_load_from_cache(self.model_id, "config.json")
            source = Path(config).parent if isinstance(config, str) else source
        signature = [(item.name, item.stat().st_size, item.stat().st_mtime_ns)
                     for item in sorted(source.glob("*")) if item.is_file()]
        identity = json.dumps(["serial-vision-v2", self.model_id, str(source), signature,
                               self.max_tokens, DEFAULT_LAYOUT_INSTRUCTION]).encode()
        digest = hashlib.sha256(identity + image_path.read_bytes()).hexdigest()
        return self.cache_dir / f"{digest}.json"

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
        model_image_path = image_path
        normalized_image_dir: tempfile.TemporaryDirectory[str] | None = None
        response = None
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
                    normalized.close()

            cache_path = self._cache_path(model_image_path)
            if cache_path is not None and cache_path.is_file():
                try:
                    cached = json.loads(cache_path.read_text(encoding="utf-8"))
                    raw_output = cached["text"]
                    if not isinstance(raw_output, str):
                        raise ValueError("invalid cached OCR text")
                    blocks = parse_grounded_layout(raw_output)
                    if blocks:
                        self.stats["cache_hits"] += 1
                        return blocks, raw_output
                except (OSError, ValueError, KeyError, TypeError):
                    pass

            self._ensure_loaded()
            prompt = self._apply_chat_template(
                self._processor, self._model.config, DEFAULT_LAYOUT_INSTRUCTION, num_images=1,
            )

            response: Any = self._generate(
                self._model,
                self._processor,
                prompt=prompt,
                image=[str(model_image_path)],
                max_tokens=self.max_tokens,
                prefill_step_size=512,
                verbose=False,
            )
            raw_output = getattr(response, "text", response)
            if not isinstance(raw_output, str):
                raw_output = str(raw_output)
            blocks = parse_grounded_layout(raw_output)
            if not blocks:
                raise RuntimeError("Unlimited-OCR returned no grounded layout blocks")
            if getattr(response, "generation_tokens", 0) >= self.max_tokens:
                raise RuntimeError("Unlimited-OCR reached --layout-max-tokens before completing the page")
            self.stats["generated_pages"] += 1
            # Resolve the cache key again after a first model download.
            cache_path = self._cache_path(model_image_path)
            if cache_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache_path.parent,
                                                 prefix=".layout-", suffix=".tmp", delete=False) as handle:
                    temporary_path = Path(handle.name)
                    json.dump({"text": raw_output}, handle, ensure_ascii=False)
                try:
                    os.replace(temporary_path, cache_path)
                finally:
                    temporary_path.unlink(missing_ok=True)
            return blocks, raw_output
        finally:
            response = None
            if self._mx is not None:
                self.stats["peak_mlx_bytes"] = max(
                    self.stats["peak_mlx_bytes"], self._mx.get_peak_memory(),
                )
                gc.collect()
                self._mx.clear_cache()
            if normalized_image_dir is not None:
                normalized_image_dir.cleanup()
