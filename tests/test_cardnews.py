import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/cardnews_gallery.py'
SPEC = importlib.util.spec_from_file_location('cardnews', SCRIPT)
gallery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gallery)
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=')


class CardnewsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.digest = {'title': 'Summary <test>', 'room': 'Example room', 'collected_date': '2026-01-01', 'scope': 'text only', 'overview': ['Short summary'], 'sections': [{'title': 'Chat', 'items': [{'title': 'Topic', 'summary': 'Discussion', 'useful': 'Use case', 'status': 'Opinion', 'sources': []}]}]}
        self.write('digest.json', self.digest)

    def write(self, name, data):
        (self.directory/name).write_text(json.dumps(data))

    def run_cli(self, *args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPT), '--directory', str(self.directory), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return json.loads(result.stdout) if success else result

    def add_image(self):
        card = {'id': 'example', 'topic': 'Example', 'headline': 'Headline', 'summary': 'Brief', 'card_summary': 'Subtitle', 'generation_prompt': 'Original prompt', 'labels': ['A','B','C'], 'badge': 'Opinion', 'short_limit': 'Not tested', 'bullets': ['Detail'], 'why': 'Useful', 'caution': 'Concept', 'sources': []}
        (self.directory/'source.png').write_bytes(PNG)
        self.write('copy.json', {'cards': [card]})
        self.write('generated-sources.json', {'backend': 'fixture', 'cards': [{'id': card['id'], 'path': str(self.directory/'source.png'), 'image_key': gallery.image_key(card)}]})
        return card

    def test_summary_does_not_require_images_or_card_metadata(self):
        result = self.run_cli('--summary-only')
        self.assertEqual(result['image_reads'], 0)
        self.assertIn('Short summary', (self.directory/'summary.md').read_text())
        self.assertFalse((self.directory/'index.html').exists())

    def test_unchanged_card_reuses_image(self):
        self.add_image()
        self.assertEqual(self.run_cli('--plan-images')['generate_count'], 0)

    def test_changed_headline_queues_card_and_rejects_stale_image(self):
        card = self.add_image(); card['headline'] = 'Changed'
        self.write('copy.json', {'cards': [card]})
        self.assertEqual(self.run_cli('--plan-images')['generate_count'], 1)
        self.run_cli(success=False)

    def test_missing_image_is_queued(self):
        self.add_image(); (self.directory/'source.png').unlink()
        self.assertEqual(self.run_cli('--plan-images')['generate_count'], 1)

    def test_default_gallery_loads_png_lazily_after_summary(self):
        self.add_image(); self.run_cli()
        page = (self.directory/'index.html').read_text()
        self.assertIn('src="01-example.png" loading="lazy"', page)
        self.assertNotIn('data:image/png', page)
        self.assertLess(page.index('Short summary'), page.index('<img'))
        self.assertIn('Summary &lt;test&gt;', page)
        self.assertEqual((self.directory/'01-example.png').read_bytes(), PNG)

    def test_unchanged_image_is_not_copied_again(self):
        self.add_image(); self.run_cli()
        destination = self.directory/'01-example.png'; modified = destination.stat().st_mtime_ns
        self.run_cli()
        self.assertEqual(destination.stat().st_mtime_ns, modified)

    def test_portable_export_embeds_image(self):
        self.add_image(); result = self.run_cli('--embed-images')
        self.assertTrue(result['embedded'])
        self.assertIn('data:image/png;base64,', (self.directory/'index.html').read_text())


if __name__ == '__main__':
    unittest.main()
