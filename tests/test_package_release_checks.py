import json, sys, tempfile, unittest, zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'scripts'))
import package_release as pr


class ArchiveChecks(unittest.TestCase):
    def make(self, tmp, sleep=True, updaters=None):
        path = Path(tmp)/'r.zip'
        base = f'Cores/{pr.AUTHOR}.PlayStation/'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr(base + 'core.json', json.dumps({'core': {'framework': {'sleep_supported': sleep}}}))
            z.writestr(base + 'updaters.json', json.dumps(
                updaters if updaters is not None else {'plugins': [pr.PLUGIN], 'declare_ai': ['Claude', 'Codex']}))
        return path

    def test_good(self):
        with tempfile.TemporaryDirectory() as t:
            path = self.make(t); pr.check_archive(path); self.assertTrue(path.exists())

    def test_refused(self):
        for kw in ({'sleep': False}, {'sleep': None}, {'updaters': {'plugins': [pr.PLUGIN]}},
                   {'updaters': {'declare_ai': ['Claude', 'Codex']}}, {'updaters': {'plugins': [pr.PLUGIN], 'declare_ai': ['Claude']}}):
            with self.subTest(**{k: str(v) for k, v in kw.items()}), tempfile.TemporaryDirectory() as t:
                path = self.make(t, **kw)
                with self.assertRaises(SystemExit): pr.check_archive(path)
                self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
