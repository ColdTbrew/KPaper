from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import translate_html_blocks  # noqa: E402
import apply_paper_viewer_style  # noqa: E402


class LocalAssetRebaseTests(unittest.TestCase):
    def test_restyle_restores_equation_with_output_relative_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "inputs" / "paper.source.html"
            output = root / "outputs" / "paper.ko.paper.html"
            source.parent.mkdir()
            output.parent.mkdir()
            crop = source.parent / "assets" / "paper" / "equation.png"
            crop.parent.mkdir(parents=True)
            crop.write_bytes(b"png")
            source.write_text('<html><body><article class="ltx_document"><figure class="codex_pdf_layout_equation" id="eq1"><img src="assets/paper/equation.png"></figure></article></body></html>')
            output.write_text('<html><body><article class="ltx_document"><p id="eq1">broken formula</p><p>한국어 본문</p></article></body></html>')
            bilingual = output.with_name("paper.ko-en.paper.html")
            for _ in range(2):
                apply_paper_viewer_style.main([str(output), str(output), str(source), "--bilingual-output", str(bilingual)])
                soup = BeautifulSoup(output.read_text(), "lxml")
                self.assertEqual(soup.img["src"], "../inputs/assets/paper/equation.png")
                self.assertNotIn("broken formula", soup.get_text())
                self.assertIn("한국어 본문", soup.get_text())
                comparison = BeautifulSoup(bilingual.read_text(), "lxml")
                self.assertEqual([img["src"] for img in comparison.find_all("img")], ["../inputs/assets/paper/equation.png"] * 3)

    def test_rebases_input_asset_for_output_reader(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_dir = root / "inputs"
            output_dir = root / "outputs"
            crop = input_dir / "assets" / "paper" / "layout" / "figure.png"
            crop.parent.mkdir(parents=True)
            output_dir.mkdir()
            crop.write_bytes(b"png")
            soup = BeautifulSoup(
                '<figure><img src="assets/paper/layout/figure.png"></figure>',
                "lxml",
            )

            translate_html_blocks.rebase_local_asset_links(soup, input_dir, output_dir)

            self.assertEqual(soup.img["src"], "../inputs/assets/paper/layout/figure.png")


if __name__ == "__main__":
    unittest.main()
