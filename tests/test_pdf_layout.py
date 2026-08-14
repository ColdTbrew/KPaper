from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pdf_layout  # noqa: E402
import kpaper  # noqa: E402


class GroundedLayoutParserTests(unittest.TestCase):
    def test_parses_inline_label_variant_in_emitted_reading_order(self) -> None:
        raw = """
<|det|>title [20, 40, 980, 120]<|/det|>4. Training Details
<|det|>table [210, 180, 820, 480]<|/det|><table><tr><td>Model</td></tr></table>
<|det|>text [20, 520, 980, 900]<|/det|>We trained several model sizes.
"""

        blocks = pdf_layout.parse_grounded_layout(raw)

        self.assertEqual([block.kind for block in blocks], ["title", "table", "text"])
        self.assertEqual(blocks[1].bbox, (210.0, 180.0, 820.0, 480.0))
        self.assertEqual(blocks[2].text, "We trained several model sizes.")

    def test_parses_ref_token_and_double_bracket_variant(self) -> None:
        raw = (
            "<|ref|>figure_caption<|/ref|><|det|>[[100, 200, 900, 300]]<|/det|>"
            "Figure 1: System overview"
        )

        blocks = pdf_layout.parse_grounded_layout(raw)

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].kind, "figure-caption")
        self.assertTrue(pdf_layout.is_caption_block(blocks[0]))
        self.assertFalse(pdf_layout.is_visual_block(blocks[0]))

    def test_normalizes_mlx_fast_tokenizer_artifacts(self) -> None:
        raw = (
            "prefixĊ<|det|>titleĠ[86,Ġ58,Ġ480,Ġ72]<|/det|>"
            "RobustĠSpeechĠRecognitionĊ"
        )

        blocks = pdf_layout.parse_grounded_layout(raw)

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].kind, "title")
        self.assertEqual(blocks[0].bbox, (86.0, 58.0, 480.0, 72.0))
        self.assertEqual(blocks[0].text, "Robust Speech Recognition")


class LayoutFallbackTests(unittest.TestCase):
    def test_pdf_inspector_routes_digital_pdf_without_ocr(self) -> None:
        import pymupdf

        with tempfile.TemporaryDirectory() as temporary_directory:
            pdf_path = Path(temporary_directory) / "digital.pdf"
            document = pymupdf.open()
            page = document.new_page()
            page.insert_text((72, 72), "This digital PDF already contains selectable text.")
            document.save(pdf_path)
            document.close()

            classification = kpaper.classify_pdf_for_ocr(pdf_path)

        self.assertEqual(classification["pdf_type"], "text_based")
        self.assertEqual(classification["pages_needing_ocr"], [])

    def test_uses_native_layout_when_mlx_returns_no_blocks(self) -> None:
        class EmptyLayoutEngine:
            def parse_image(self, _image_path: Path):
                raise RuntimeError("Unlimited-OCR returned no grounded layout blocks")

        native_blocks = [pdf_layout.LayoutBlock("text", (1, 2, 3, 4), "fallback")]
        with mock.patch.object(
            kpaper.pdf_layout,
            "extract_native_pdf_layout",
            return_value=native_blocks,
        ) as extract_native:
            blocks, raw_layout, reason = kpaper.extract_image_layout_with_fallback(
                Path("paper.pdf"), 2, Path("page.png"), EmptyLayoutEngine()
            )

        self.assertEqual(blocks, native_blocks)
        self.assertEqual(raw_layout, "")
        self.assertIn("no grounded layout blocks", reason)
        extract_native.assert_called_once_with(Path("paper.pdf"), 2)


class LayoutRendererTests(unittest.TestCase):
    def test_keeps_one_grounded_crop_for_a_composite_vector_figure(self) -> None:
        native_blocks = [
            pdf_layout.LayoutBlock("text", (100, 80, 900, 130), "Before"),
            pdf_layout.LayoutBlock("figure", (180, 180, 240, 240), ""),
            pdf_layout.LayoutBlock("figure", (260, 180, 320, 240), ""),
            pdf_layout.LayoutBlock("text", (200, 220, 300, 250), "inside figure"),
            pdf_layout.LayoutBlock("text", (100, 700, 900, 760), "After"),
        ]
        grounded_blocks = [
            pdf_layout.LayoutBlock("image", (150, 150, 850, 650), ""),
            pdf_layout.LayoutBlock("image-caption", (300, 660, 700, 690), "Figure 1"),
        ]

        combined = pdf_layout.combine_native_text_with_grounded_visuals(
            native_blocks, grounded_blocks
        )

        visuals = [block for block in combined if pdf_layout.is_visual_block(block)]
        self.assertEqual(len(visuals), 1)
        self.assertEqual(visuals[0].bbox, (150, 150, 850, 650))
        self.assertNotIn("inside figure", [block.text for block in combined])
        self.assertIn("Before", [block.text for block in combined])
        self.assertIn("After", [block.text for block in combined])

    def test_crops_visual_block_between_surrounding_text(self) -> None:
        blocks = [
            pdf_layout.LayoutBlock("text", (0, 0, 1024, 150), "Before table"),
            pdf_layout.LayoutBlock("table", (256, 256, 768, 768), "table markdown"),
            pdf_layout.LayoutBlock("text", (0, 800, 1024, 1000), "After table"),
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            page_image = root / "inputs" / "assets" / "paper" / "page-0001.png"
            page_image.parent.mkdir(parents=True)
            Image.new("RGB", (2048, 3072), color="white").save(page_image)
            html_parent = root / "inputs"

            rendered, visual_count = pdf_layout.render_layout_page(
                blocks,
                page_image=page_image,
                assets_dir=page_image.parent,
                html_parent=html_parent,
                paper_id="paper",
                page_num=1,
            )

            self.assertEqual(visual_count, 1)
            self.assertLess(rendered.index("Before table"), rendered.index("codex_pdf_layout_table"))
            self.assertLess(rendered.index("codex_pdf_layout_table"), rendered.index("After table"))
            crops = list((page_image.parent / "layout").glob("*.png"))
            self.assertEqual(len(crops), 1)
            with Image.open(crops[0]) as crop:
                self.assertGreater(crop.width, 1000)
                self.assertGreater(crop.height, 1500)


if __name__ == "__main__":
    unittest.main()
