# SPDX-License-Identifier: GPL-2.0-only
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_spu_gaussian_mac as preparer
import tool_paths


class GaussianAdmission(unittest.TestCase):
    def test_unreviewed_source_is_rejected(self):
        for combined in [False, True]:
            with self.subTest(combined=combined), \
                 self.assertRaisesRegex(ValueError, 'Exact FIR-width SPU required'):
                preparer.gaussian_text('library ieee; entity spu is end;\n', with_adsr=combined)

    def test_export_project_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'export'
            project.mkdir()
            qsf = project / 'apf_top.qsf'
            qsf.write_text('# published project\n')
            with mock.patch.object(tool_paths, 'ROOT', root), \
                 mock.patch.object(preparer.subprocess, 'run') as git:
                with self.assertRaisesRegex(ValueError, 'Ignored private build project required'):
                    preparer.prepare(project)
                git.assert_not_called()
            self.assertEqual(qsf.read_text(), '# published project\n')
            self.assertEqual([p.name for p in project.iterdir()], ['apf_top.qsf'])

    def test_altered_wrapper_fails_without_project_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'build/unit'
            project.mkdir(parents=True)
            wrapper = root / 'rtl/pocket/psx_spu_gaussian_mac.sv'
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text('module altered; endmodule\n')
            (project / 'manifest.json').write_text(json.dumps({'reset_only': True}))
            (project / 'apf_top.qsf').write_text('# test project\n')
            before = {p.name: p.read_bytes() for p in project.iterdir()}
            with mock.patch.object(tool_paths, 'ROOT', root), \
                 mock.patch.object(preparer.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
                with self.assertRaisesRegex(ValueError, 'Unreviewed Gaussian wrapper'):
                    preparer.prepare(project)
            self.assertEqual(before, {p.name: p.read_bytes() for p in project.iterdir()})


if __name__ == '__main__':
    unittest.main()
