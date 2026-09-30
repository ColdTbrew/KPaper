from __future__ import annotations
import sys
import unittest
from pathlib import Path
from bs4 import BeautifulSoup
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from translate_html_blocks import format_pdf_titles


class PDFTitleTests(unittest.TestCase):
    def document(self, content):
        return BeautifulSoup('<article class="codex_pdf_document">'+content+'</article>', 'lxml')

    def test_recovers_numbered_heading_levels(self):
        soup=self.document(''.join(f'<div class="codex_pdf_layout_text" id="p{i}"><p class="ltx_p">{text}</p></div>' for i,text in enumerate(['5. Training', '5.2. Reinforcement Training', '5.2.2. GRPO-based Post-Training'])))
        format_pdf_titles(soup)
        self.assertEqual([t.name for t in soup.select('.codex_pdf_layout_text > *')], ['h2','h3','h4'])
        self.assertEqual(soup.h4['id'], 'p2-heading')

    def test_source_label_controls_korean_bold_and_keeps_link(self):
        source=self.document('<div class="codex_pdf_layout_text" id="block"><p class="ltx_p">Behavioral Regulation Deficiency. The model needs regulation.</p></div>')
        translated=self.document('<div class="codex_pdf_layout_text" id="block"><p class="ltx_p">행동 조절 부족. 본문의 <a href="#cite">인용</a>을 유지합니다.</p></div>')
        text=translated.p.get_text()
        format_pdf_titles(translated, source)
        format_pdf_titles(translated, source)
        self.assertEqual(translated.strong.get_text(), '행동 조절 부족.')
        self.assertEqual(len(translated.find_all('strong')), 1)
        self.assertEqual(translated.p.get_text(), text)
        self.assertEqual(translated.a['href'], '#cite')

    def test_ordinary_sentences_and_references_are_not_emphasized(self):
        soup=self.document('<div class="codex_pdf_layout_text"><p class="ltx_p">We train the model. This is ordinary text.</p></div><div class="codex_pdf_layout_text"><p class="ltx_p">References</p></div><div class="codex_pdf_layout_text"><p class="ltx_p">Box IoU Reward. A cited title.</p></div>')
        format_pdf_titles(soup)
        self.assertIsNone(soup.strong)


if __name__ == '__main__':
    unittest.main()
