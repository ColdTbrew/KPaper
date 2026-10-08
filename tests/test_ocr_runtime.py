from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import kpaper
import ocr_runtime
import pdf_layout


RAW_LAYOUT = "<|det|>text [50, 50, 950, 500]<|/det|>Selectable body text."


def make_pdf(path, count=3, figure=False, math=False):
    with pymupdf.open() as document:
        for _ in range(count):
            page = document.new_page()
            page.insert_text((50, 70), "Ordinary body text, long enough to classify as a digital document.")
            if figure:
                page.draw_rect(pymupdf.Rect(50, 100, 300, 300))
            if math:
                page.insert_text((50, 100), "x + y = z", fontname="symb")
        document.save(path)


class PagePipelineTests(unittest.TestCase):
    def test_plain_text_does_not_load_ocr_and_renders_only_selected_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / "paper.pdf"
            make_pdf(pdf)
            with mock.patch.object(pdf_layout.UnlimitedOCRMLX, "_ensure_loaded",
                                   side_effect=AssertionError("plain text loaded OCR")):
                result = kpaper.pdf_to_source_html(pdf, root / "paper.html", root / "assets", "paper", "Paper",
                                                  72, 2, "auto", pdf_layout.DEFAULT_LAYOUT_MODEL, 8192, False)
            self.assertEqual(result["pages"], 2)
            self.assertEqual(result["native_only_pages"], [1, 2])
            self.assertEqual(result["ocr_runtime"]["generated_pages"], 0)
            self.assertIn("Ordinary body text", (root / "paper.html").read_text())
            self.assertEqual(len(list((root / "assets").glob("page-*.png"))), 2)

    def test_vector_figures_and_math_still_require_grounding(self):
        with tempfile.TemporaryDirectory() as folder:
            for kind in ("figure", "math"):
                path = Path(folder) / f"{kind}.pdf"
                make_pdf(path, 1, **{kind: True})
                self.assertTrue(pdf_layout.page_requires_grounding(path, 0))

    def test_renderer_is_lazy_and_clamps_page_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "paper.pdf"
            make_pdf(path)
            assets = root / "assets"
            assets.mkdir()
            images = kpaper.iter_pdf_page_images(path, assets, 72, 99)
            self.assertEqual(list(assets.iterdir()), [])
            index, first = next(images)
            self.assertEqual(index, 0)
            self.assertTrue(first.exists())
            self.assertFalse((assets / "page-0002.png").exists())
            self.assertEqual(len(list(images)), 2)

    def test_ocr_error_falls_back_and_releases_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "paper.pdf"
            make_pdf(path, 1, figure=True)
            engine = mock.Mock()
            engine.parse_image.side_effect = RuntimeError("bad page")
            engine.stats = {}
            with mock.patch.object(pdf_layout, "UnlimitedOCRMLX", return_value=engine):
                result = kpaper.pdf_to_source_html(path, root / "paper.html", root / "assets", "paper", "Paper",
                                                  72, 1, "auto", pdf_layout.DEFAULT_LAYOUT_MODEL, 8192, False)
            engine.close.assert_called_once()
            self.assertEqual(result["layout_fallbacks"][0]["to"], "native")
            self.assertIn("Ordinary body text", (root / "paper.html").read_text())

    def test_render_failure_releases_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "paper.pdf"
            make_pdf(path, 1, figure=True)
            engine = mock.Mock(stats={})
            engine.parse_image.return_value = (pdf_layout.parse_grounded_layout(RAW_LAYOUT), RAW_LAYOUT)
            with mock.patch.object(pdf_layout, "UnlimitedOCRMLX", return_value=engine), \
                 mock.patch.object(pdf_layout, "render_layout_page", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    kpaper.pdf_to_source_html(path, root / "paper.html", root / "assets", "paper", "Paper",
                                             72, 1, "auto", pdf_layout.DEFAULT_LAYOUT_MODEL, 8192, False)
            engine.close.assert_called_once()

    def test_scan_ocr_failure_cannot_silently_create_empty_text(self):
        engine = mock.Mock()
        engine.parse_image.side_effect = RuntimeError("generation truncated")
        with mock.patch.object(pdf_layout, "extract_native_pdf_layout", return_value=[]), \
             mock.patch.object(pdf_layout, "is_blank_pdf_page", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "no native text"):
                kpaper.extract_image_layout_with_fallback(Path("scan.pdf"), 0, Path("page.png"), engine)

    def test_blank_page_can_fall_back_without_failing_document(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "blank.pdf"
            with pymupdf.open() as document:
                document.new_page()
                document.save(path)
            engine = mock.Mock()
            engine.parse_image.side_effect = RuntimeError("no blocks")
            blocks, _, reason = kpaper.extract_image_layout_with_fallback(path, 0, Path("page.png"), engine)
            self.assertEqual(blocks, [])
            self.assertEqual(reason, "no blocks")


class LayoutCacheTests(unittest.TestCase):
    def test_valid_cache_avoids_model_load_and_invalidates_changed_image_or_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = root / "model"
            model.mkdir()
            config = model / "config.json"
            config.write_text('{}')
            image = root / "page.png"
            Image.new("RGB", (100, 100), "white").save(image)
            engine = pdf_layout.UnlimitedOCRMLX(str(model), cache_dir=root / "cache")
            cache = engine._cache_path(image)
            cache.parent.mkdir()
            cache.write_text(json.dumps({"text": RAW_LAYOUT}))
            with mock.patch.object(engine, "_ensure_loaded", side_effect=AssertionError("cache loaded OCR")):
                blocks, _ = engine.parse_image(image)
            self.assertEqual(blocks[0].text, "Selectable body text.")
            self.assertEqual(engine.stats["cache_hits"], 1)
            config.write_text('{"updated": true}')
            self.assertNotEqual(cache, engine._cache_path(image))
            after_model = engine._cache_path(image)
            Image.new("RGB", (100, 100), "black").save(image)
            self.assertNotEqual(after_model, engine._cache_path(image))

    def test_truncated_ocr_is_not_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = root / "model"
            model.mkdir()
            image = root / "page.png"
            Image.new("RGB", (100, 100)).save(image)
            engine = pdf_layout.UnlimitedOCRMLX(str(model), max_tokens=10, cache_dir=root / "cache")
            engine._model = SimpleNamespace(config={})
            engine._processor = object()
            engine._apply_chat_template = lambda *a, **k: "prompt"
            engine._generate = lambda *a, **k: SimpleNamespace(text=RAW_LAYOUT, generation_tokens=10)
            with self.assertRaisesRegex(RuntimeError, "max-tokens"):
                engine.parse_image(image)
            self.assertFalse((root / "cache").exists())


class NativeExecutionTests(unittest.TestCase):
    def test_chunked_attention_matches_upstream_full_attention(self):
        import mlx.core as mx
        import mlx.nn as nn
        from mlx_vlm.models.deepseekocr.sam import Attention
        mx.random.seed(3)
        attention = Attention(32, num_heads=4, use_rel_pos=True, input_size=(8, 8))
        block = SimpleNamespace(attn=attention, window_size=0)
        encoder = SimpleNamespace(blocks=[block])
        values = mx.random.normal((2, 8, 8, 32))
        original = attention(values)
        mx.eval(original)
        ocr_runtime.chunk_sam_attention(encoder, mx, nn, query_chunk=7)
        actual = block.attn(values)
        mx.eval(actual)
        np.testing.assert_allclose(np.array(actual), np.array(original), rtol=1e-5, atol=1e-5)
        mx.clear_cache()

    def test_serial_vision_preserves_values_crop_order_and_embeddings(self):
        calls = []
        class Encoder:
            def __call__(self, images, patch_embeds=None):
                calls.append(images.shape[0])
                return images * 2 + (patch_embeds if patch_embeds is not None else 0)
        mx = SimpleNamespace(array=np.ndarray, concatenate=np.concatenate, eval=lambda *a: None)
        model = SimpleNamespace(sam_model=Encoder(), vision_model=Encoder())
        ocr_runtime.serialize_vision_encoders(model, mx, SimpleNamespace(Module=object))
        values = np.arange(24).reshape(4, 2, 3)
        embeddings = np.ones_like(values) * 7
        np.testing.assert_array_equal(model.sam_model(values), values * 2)
        np.testing.assert_array_equal(model.vision_model(values, embeddings), values * 2 + embeddings)
        np.testing.assert_array_equal(model.vision_model(values, patch_embeds=embeddings), values * 2 + embeddings)
        self.assertEqual(calls, [1] * 12)

    def test_separate_ocr_process_waits_until_lock_is_released(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "waiting"
            lock = ocr_runtime.OCRProcessLock()
            lock.acquire()
            code = ("import sys; from pathlib import Path; "
                    f"sys.path.insert(0, {str(Path(ocr_runtime.__file__).parent)!r}); "
                    "from ocr_runtime import OCRProcessLock; "
                    f"lock=OCRProcessLock(lambda _:Path({str(marker)!r}).write_text('waiting')); "
                    "lock.acquire(); print('acquired', flush=True); lock.release()")
            child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(marker.exists())
                self.assertIsNone(child.poll())
                lock.release()
                stdout, stderr = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, stderr)
                self.assertEqual(stdout.strip(), "acquired")
            finally:
                lock.release()
                if child.poll() is None:
                    child.kill()
                    child.communicate()


if __name__ == "__main__":
    unittest.main()
