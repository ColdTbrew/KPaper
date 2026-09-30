from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import kpaper
import translate_html_blocks as translator
import apply_paper_viewer_style as restyler


class SingleReaderTests(unittest.TestCase):
    def test_cli_paths_use_one_canonical_file(self):
        args = argparse.Namespace(paper_id='paper', input='', output='outputs/paper.ko.paper.html', bilingual_output='')
        paths = kpaper.resolve_paths(args)
        self.assertEqual(paths['output'], Path('outputs/paper.ko-en.paper.html'))
        self.assertEqual(paths['output'], paths['bilingual_output'])
        self.assertEqual(kpaper.bilingual_path_for_output(paths['output']), paths['output'])

    def test_translation_writes_only_unified_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'paper.source.html'
            legacy = root / 'paper.ko.paper.html'
            source.write_text('<html><head><title>Paper</title></head><body><article class="ltx_document"><p class="ltx_p" id="p1">Original</p></article></body></html>')
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'test', 'OPENAI_BASE_URL': 'http://example.invalid'}), patch.object(translator, 'call_api', return_value=({'b0': '<p class="ltx_p" id="p1">번역문</p>'}, 0, 0)):
                translator.main(['--input', str(source), '--output', str(legacy), '--cache', str(root / 'cache.jsonl'), '--env-file', str(root / 'missing')])
            self.assertFalse(legacy.exists())
            canonical = root / 'paper.ko-en.paper.html'
            soup = BeautifulSoup(canonical.read_text(), 'lxml')
            self.assertIn('Original', soup.select_one('#codex-panel-parallel .codex_parallel_column').get_text())
            self.assertIn('번역문', soup.select_one('#codex-panel-ko').get_text())
            # Restyle without a separate source preserves the embedded original.
            restyler.main([str(canonical)])
            soup = BeautifulSoup(canonical.read_text(), 'lxml')
            self.assertEqual(len(soup.select('article.ltx_document')), 3)
            self.assertIn('Original', soup.select_one('#codex-panel-parallel .codex_parallel_column').get_text())
            self.assertFalse(legacy.exists())
