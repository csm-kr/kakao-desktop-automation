import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('unread_capture', Path(__file__).resolve().parents[1] / 'scripts/kakao_unread_capture.py')
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


class UnreadCaptureTests(unittest.TestCase):
    def run_capture(self, *, marker=True, conflict=False, bottom=True):
        stage = 0
        limits = []
        counts = [30, 80, 100]

        def invoke(args, **kwargs):
            nonlocal stage
            if args[0] == 'collector':
                limit = int(args[2]); limits.append(limit)
                count = counts[stage]
                rows = []
                for index in range(max(0, count-limit), count):
                    text = f'message {index}'
                    if index == 10 and marker: text = '여기까지 읽었습니다.'
                    if index == count-1: text = ''
                    if conflict and stage == 1 and index == 28: text = 'changed index'
                    rows.append({'row_index': index, 'text_parts': [text] if text else [], 'body_sha256': hashlib.sha256(text.encode()).hexdigest()})
                data = {'ready': True, 'target_title': 'Exact room', 'rows_available': count, 'rows': rows}
            else:
                if args[1] == 'step': stage += 1
                data = {'rows_after': counts[stage], 'after_scroll_value': 1 if stage == 2 and bottom else .5}
            return subprocess.CompletedProcess(args, 0, json.dumps(data), '')

        with tempfile.TemporaryDirectory() as tmp, patch.object(capture.subprocess, 'run', side_effect=invoke):
            result = capture.capture('Exact room', Path('collector'), Path('navigator'), Path(tmp)/'run', max_pages=2)
            data = json.loads(Path(result['capture']).read_text())
            return result, data, limits

    def test_new_rows_only_and_previous_placeholder_updated(self):
        result, data, limits = self.run_capture()
        self.assertEqual(limits, [200, 52, 22])
        self.assertEqual([r['row_index'] for r in data['rows']], list(range(11, 100)))
        self.assertEqual(data['rows'][18]['text_parts'], ['message 29'])
        self.assertEqual(result['messages_sent'], 0)

    def test_missing_boundary_does_not_claim_unread(self):
        with self.assertRaisesRegex(ValueError, 'Unread boundary'):
            self.run_capture(marker=False)

    def test_shifted_indices_do_not_merge(self):
        with self.assertRaisesRegex(ValueError, 'indices changed'):
            self.run_capture(conflict=True)

    def test_incomplete_bottom_does_not_claim_complete(self):
        with self.assertRaisesRegex(ValueError, 'Bottom not confirmed'):
            self.run_capture(bottom=False)


if __name__ == '__main__':
    unittest.main()
