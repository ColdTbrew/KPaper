from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import kpaper
import translate_html_blocks as translator


class CodexSettingsTests(unittest.TestCase):
    def test_provider_defaults_and_explicit_models(self):
        parser = kpaper.build_parser()
        for provider, model in [('codex', 'gpt-6-luna'), ('api', 'gpt-5.4-mini')]:
            args = parser.parse_args(['translate', '--paper-id', 'paper', '--provider', provider])
            self.assertEqual(translator.translation_model(args.provider, args.model), model)
            args = parser.parse_args(['translate', '--paper-id', 'paper', '--provider', provider, '--model', 'custom-model'])
            self.assertEqual(translator.translation_model(args.provider, args.model), 'custom-model')

    def test_actual_codex_command_has_explicit_low_reasoning(self):
        captured = []
        def run(command, **kwargs):
            captured.append(command)
            output = Path(command[command.index('--output-last-message') + 1])
            output.write_text(json.dumps({'translations': [{'id': 'b0', 'text': '번역'}]}))
            return subprocess.CompletedProcess(command, 0, stdout='', stderr='')
        with patch.dict(os.environ, {'CODEX_EXECUTABLE': '/fake/codex'}), patch.object(translator.subprocess, 'run', side_effect=run):
            result = translator.call_codex('gpt-6-luna', [('b0', 'text')], 30, 0)
        self.assertEqual(result[0], {'b0': '번역'})
        command = captured[0]
        self.assertEqual(command[command.index('--model') + 1], 'gpt-6-luna')
        self.assertEqual(command[command.index('-c') + 1], 'model_reasoning_effort="low"')
        self.assertIn('--ignore-user-config', command)
        self.assertEqual(command[-1], '-')

    def test_cli_passes_resolved_model_without_translation_call(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'paper.html'
            source.write_text('<article class="ltx_document"></article>')
            for provider, expected in [('codex', 'gpt-6-luna'), ('api', 'gpt-5.4-mini')]:
                args = kpaper.build_parser().parse_args(['translate', '--paper-id', 'paper', '--input', str(source), '--provider', provider, '--dry-run'])
                with patch.object(kpaper.translate_html_blocks, 'main') as main, patch.object(kpaper, 'emit'):
                    kpaper.command_translate(args)
                command = main.call_args.args[0]
                self.assertEqual(command[command.index('--model') + 1], expected)
