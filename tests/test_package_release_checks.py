import json, sys, tempfile, unittest, zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'scripts'))
import package_release as pr


class OptionalCredits(unittest.TestCase):
    ROWS = '| A | x |\n| Analogizer interface (only in builds with the Analogizer option) | y |\n| B | z |\n'

    def test_dropped_without_the_option(self):
        out = pr.notices_for(self.ROWS, 'set_global_assignment -name SYSTEMVERILOG_FILE "a/psx_top.sv"')
        self.assertEqual(out, '| A | x |\n| B | z |\n')

    def test_kept_with_the_option(self):
        self.assertEqual(pr.notices_for(self.ROWS, 'SYSTEMVERILOG_FILE "rtl/pocket/psx_analogizer.sv"'), self.ROWS)


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
