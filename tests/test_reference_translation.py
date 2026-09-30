from __future__ import annotations

import sys
import unittest
from pathlib import Path
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from translate_html_blocks import collect_blocks, restore_source_references, collapse_references


class ReferenceTranslationTests(unittest.TestCase):
    def test_accordion_groups_pdf_pages_and_preserves_appendix(self):
        soup = BeautifulSoup('''<article>
        <section class="codex_pdf_page"><p class="ltx_p">Main text.</p><div id="refs"><p class="ltx_p">References</p></div><div id="r1"><p class="ltx_p">[1] First title.</p></div></section>
        <section class="codex_pdf_page"><div id="r2"><p class="ltx_p">[2] Second title.</p></div></section>
        <section class="codex_pdf_page"><h2 class="ltx_title_section">Appendix</h2><p class="ltx_p">Appendix text.</p></section></article>''', 'lxml')
        collapse_references(soup)
        collapse_references(soup)
        self.assertEqual(len(soup.select('details.codex_references')), 1)
        self.assertFalse(soup.details.has_attr('open'))
        self.assertEqual([tag['id'] for tag in soup.details.select('[id]')], ['refs', 'r1', 'r2'])
        self.assertNotIn('Appendix text.', soup.details.get_text())
        self.assertEqual(len(soup.select('section.codex_pdf_page')), 2)
        self.assertCountEqual([t.get_text() for t in collect_blocks(soup)], ['Main text.', 'Appendix', 'Appendix text.'])

    def test_accordion_keeps_semantic_bibliography_links(self):
        soup = BeautifulSoup('<article><section class="ltx_bibliography" id="bib"><h2 class="ltx_title_section">References</h2><p class="ltx_p"><a href="https://example.org/paper">Original title</a></p></section></article>', 'lxml')
        collapse_references(soup)
        self.assertEqual(soup.details.a['href'], 'https://example.org/paper')
        self.assertEqual(soup.details.section['id'], 'bib')

    def test_semantic_bibliography_excluded_but_body_citations_translated(self):
        soup = BeautifulSoup('''<article><p class="ltx_p">See [1] for results.</p>
        <section class="ltx_bibliography" id="bib"><h2 class="ltx_title_section">References</h2>
        <p class="ltx_p">[1] Original paper title.</p></section>
        <h2 class="ltx_title_section">Appendix A</h2><p class="ltx_p">More details.</p></article>''', 'lxml')
        texts = [t.get_text() for t in collect_blocks(soup)]
        self.assertIn('See [1] for results.', texts)
        self.assertIn('More details.', texts)
        self.assertNotIn('References', texts)
        self.assertNotIn('[1] Original paper title.', texts)

    def test_pdf_references_span_pages_and_stop_at_appendix(self):
        soup = BeautifulSoup('''<article>
        <section id="page-9"><div id="ref-title"><p class="ltx_p">References</p></div>
        <div id="ref1"><p class="ltx_p">[1] Original title.</p></div></section>
        <section id="page-10"><div id="ref2"><p class="ltx_p">Continuation of reference.</p></div>
        <p class="ltx_p">Appendix A. Training Details</p><p class="ltx_p">Translate the appendix.</p></section>
        </article>''', 'lxml')
        texts = [t.get_text() for t in collect_blocks(soup)]
        self.assertEqual(texts, ['Appendix A. Training Details', 'Translate the appendix.'])
        translated = BeautifulSoup(str(soup), 'lxml')
        translated.find(id='ref1').p.string = '번역된 논문 제목'
        translated.find(id='ref2').p.string = '번역된 뒷부분'
        self.assertEqual(restore_source_references(translated, soup), 3)
        self.assertEqual(translated.find(id='ref1').p.get_text(), '[1] Original title.')
        self.assertEqual(translated.find(id='ref2').p.get_text(), 'Continuation of reference.')

    def test_mentions_of_references_do_not_start_exclusion(self):
        soup = BeautifulSoup('<p class="ltx_p">References to previous studies are discussed here.</p><p class="ltx_p">This remains translatable.</p>', 'lxml')
        self.assertEqual(len(collect_blocks(soup)), 2)


if __name__ == '__main__':
    unittest.main()
