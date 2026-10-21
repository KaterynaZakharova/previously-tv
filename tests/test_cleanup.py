import tempfile
import unittest
from pathlib import Path
from tools.cleanup_retired_paths import cleanup


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for relative in ['movies/old.mp4', 'assets/cartoon/film/movie.mp4',
                         'assets/db.sqlite', 'runtime/viewers.sqlite3', '.env',
                         'tests/fixtures/old_schema_failure.json',
                         'tests/fixtures/schema_failure.json']:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'keep exact bytes')

    def tearDown(self):
        self.tmp.cleanup()

    def test_preview_does_not_delete(self):
        cleanup(self.root)
        self.assertTrue((self.root / 'movies/old.mp4').exists())

    def test_apply_preserves_active_assets_state_and_configuration(self):
        cleanup(self.root, apply=True)
        self.assertFalse((self.root / 'movies').exists())
        self.assertFalse((self.root / 'tests/fixtures/old_schema_failure.json').exists())
        for relative in ['assets/cartoon/film/movie.mp4', 'assets/db.sqlite',
                         'runtime/viewers.sqlite3', '.env', 'tests/fixtures/schema_failure.json']:
            self.assertEqual((self.root / relative).read_bytes(), b'keep exact bytes')
        self.assertEqual(cleanup(self.root, apply=True), [])
