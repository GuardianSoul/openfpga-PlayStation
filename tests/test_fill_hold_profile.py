from pathlib import Path
import hashlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_fill_hold import PROFILE, profile_sources


class FillHoldProfile(unittest.TestCase):
    def target(self):
        return dict(quartus_sources=[dict(path='/private/' + name, sha256=sha)
                                    for name, sha in PROFILE.items()])

    def test_refreshed_manifest_cannot_admit_changed_hardware(self):
        for name in PROFILE:
            with self.subTest(name=name):
                target = self.target()
                next(r for r in target['quartus_sources']
                     if Path(r['path']).name == name)['sha256'] = '0' * 64
                with self.assertRaisesRegex(ValueError, 'Exact Fill/B6'):
                    profile_sources(target)

    def test_duplicate_assignment_cannot_bypass_source_guard(self):
        target = self.target()
        target['quartus_sources'].append(dict(target['quartus_sources'][0]))
        with self.assertRaisesRegex(ValueError, 'Exact Fill/B6'):
            profile_sources(target)

    def test_existing_hold_cells_are_not_silently_stacked(self):
        target = self.target()
        target['quartus_sources'].append(dict(path='/private/psx_hold_cell.sv', sha256='0' * 64))
        with self.assertRaisesRegex(ValueError, 'Unbuffered parent'):
            profile_sources(target)

    def test_path_normalization_does_not_hide_hardware_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core = root / 'core_top.sv'
            core.write_text('coefficient=' + str(root) + '/rom; SLOT368=1;')
            expected = hashlib.sha256(core.read_bytes().replace(
                str(root).encode(), b'<PROJECT>')).hexdigest()
            target = self.target()
            row = next(r for r in target['quartus_sources']
                       if Path(r['path']).name == 'core_top.sv')
            row.update(path=str(core), sha256=hashlib.sha256(core.read_bytes()).hexdigest())
            with mock.patch.dict(PROFILE, {'core_top.sv': expected}):
                profile_sources(target, root)
                core.write_text(core.read_text().replace('SLOT368=1', 'SLOT368=0'))
                row['sha256'] = hashlib.sha256(core.read_bytes()).hexdigest()
                with self.assertRaisesRegex(ValueError, 'Exact Fill/B6'):
                    profile_sources(target, root)


if __name__ == '__main__':
    unittest.main()
