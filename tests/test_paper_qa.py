import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from paper_qa import extract_blocks, select_context, validate_answer


class PaperQuestionTests(unittest.TestCase):
    def test_extracts_english_once_and_preserves_math_and_citation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'outputs' / 'paper.html'
            path.parent.mkdir()
            path.write_text('''<div id="codex-panel-ko"><article><div class="ltx_para" id="p1-layout-1">번역문</div></article></div>
            <div id="codex-panel-parallel"><div class="codex_parallel_column"><article>
            <div class="ltx_para" id="p1-layout-1">Original [7]<math alttext="x^2">x²</math></div>
            <figure id="p1-layout-2"><figcaption>Plot</figcaption><img src="../../secret.png"></figure>
            </article></div><div class="codex_parallel_column"><article><div class="ltx_para" id="p1-layout-1">번역문</div></article></div></div>''')
            blocks = extract_blocks(path)
            self.assertEqual(len(blocks), 2)
            self.assertIn('Original [7]', blocks[0]['text'])
            self.assertIn('x^2', blocks[0]['text'])
            self.assertEqual(blocks[0]['korean'], '번역문')
            self.assertEqual(blocks[1]['images'], [])

    def test_fails_for_missing_parsed_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'empty.html'
            path.write_text('<article></article>')
            with self.assertRaisesRegex(ValueError, '파싱된 본문'):
                extract_blocks(path)

    def test_unknown_citations_are_rejected_and_valid_ones_deduplicated(self):
        blocks = [{'id': 'p5-layout-10', 'label': 'p. 5 · GRPO'}]
        with self.assertRaises(ValueError):
            validate_answer(json.dumps({'answer': 'answer', 'citations': [{'id': 'invented'}]}), blocks)
        result = validate_answer(json.dumps({'answer': 'answer', 'citations': [{'id': 'p5-layout-10'}] * 2}), blocks)
        self.assertEqual(result['citations'], [{'id': 'p5-layout-10', 'label': 'p. 5 · GRPO'}])

    def test_long_document_retrieval_reaches_relevant_late_sections(self):
        blocks = [{'id': str(i), 'text': 'Unrelated ' * 2000, 'korean': '', 'images': []} for i in range(20)]
        blocks[-1]['text'] = 'GRPO reward details'
        selected = select_context(blocks, 'GRPO reward', [])
        self.assertIn(blocks[-1], selected)
        self.assertLessEqual(sum(len(b['text']) + 100 for b in selected), 180000)
        self.assertEqual([int(b['id']) for b in selected], sorted(int(b['id']) for b in selected))


if __name__ == '__main__':
    unittest.main()
