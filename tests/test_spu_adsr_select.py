# SPDX-License-Identifier: GPL-2.0-only
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_spu_adsr_select as preparer
import tool_paths


class SpuAdsrAdmission(unittest.TestCase):
    def test_unreviewed_source_is_rejected(self):
        for transform in [preparer.adsr_select_text, preparer.adsr_noise_text]:
            with self.subTest(transform=transform.__name__), \
                 self.assertRaisesRegex(ValueError, 'Exact FIR-width SPU required'):
                transform('library ieee; entity spu is end;\n')

    def test_outside_build_project_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'release'
            project.mkdir()
            qsf = project / 'apf_top.qsf'
            qsf.write_text('# release project\n')
            with mock.patch.object(tool_paths, 'ROOT', root), \
                 mock.patch.object(preparer.subprocess, 'run') as git:
                with self.assertRaisesRegex(ValueError, 'Ignored private build project required'):
                    preparer.prepare(project)
                git.assert_not_called()
            self.assertEqual(qsf.read_text(), '# release project\n')
            self.assertEqual(sorted(p.name for p in project.iterdir()), ['apf_top.qsf'])


if __name__ == '__main__':
    unittest.main()
