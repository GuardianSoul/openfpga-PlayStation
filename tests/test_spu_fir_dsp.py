# SPDX-License-Identifier: GPL-2.0-only
from pathlib import Path
import json
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_spu_fir_dsp as preparer
import run_spu_fir_dsp as runner


class SpuFirDspAdmission(unittest.TestCase):
    def test_unreviewed_spu_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unreviewed SPU source'):
            preparer.spu_fir_dsp_text('library ieee; entity spu is end entity;\n')

    def test_altered_wrapper_fails_before_compiler_or_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / 'rtl/pocket/psx_spu_fir_dsp.sv'
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text('module psx_spu_fir_dsp; endmodule\n')
            vendor = root / 'vendor'
            vendor.mkdir()
            for name in ['altera_mf.v', '220model.v']:
                (vendor / name).write_text('// fixture; never compiled\n')
            output = root / 'output'
            with mock.patch.object(runner, 'ROOT', root), \
                 mock.patch.object(runner, 'find_tool', return_value='/unused/compiler'), \
                 mock.patch.object(runner.subprocess, 'run') as compiler:
                with self.assertRaisesRegex(ValueError, 'Unreviewed DSP wrapper'):
                    runner.run(output, vendor)
                compiler.assert_not_called()
                self.assertFalse(output.exists())


    def test_vendor_stop_followed_by_pass_is_rejected(self):
        # Actual observed vendor failure can continue on EOF and print PASS.
        log = ('Error: Chain adder is not supported in altera_mf for Cyclone V device family\n'
               '** VVP Stop(0) **\nPASS registeredDSP checks=208800\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / 'rtl/pocket/psx_spu_fir_dsp.sv'
            wrapper.parent.mkdir(parents=True)
            wrapper.write_bytes((runner.ROOT / 'rtl/pocket/psx_spu_fir_dsp.sv').read_bytes())
            vendor = root / 'vendor'
            vendor.mkdir()
            for name in ['altera_mf.v', '220model.v']:
                (vendor / name).write_text('// never compiled: captured-error admission test\n')
            output = root / 'output'
            results = [SimpleNamespace(returncode=0),
                       SimpleNamespace(returncode=0, stdout=log, stderr='')]
            with mock.patch.object(runner, 'ROOT', root), \
                 mock.patch.object(runner, 'find_tool', return_value='/unused/tool'), \
                 mock.patch.object(runner.subprocess, 'run', side_effect=results) as compiler:
                with self.assertRaisesRegex(ValueError, 'Unexpected DSP verdict: positive'):
                    runner.run(output, vendor)
                self.assertEqual(compiler.call_count, 2)
                self.assertIn('-N', compiler.call_args.args[0])
            report = json.loads((output / 'report.json').read_text())
            self.assertFalse(report['passed'])
            self.assertFalse(report['cases']['positive']['passed'])
            self.assertTrue(report['cases']['positive']['model_errors'])


if __name__ == '__main__':
    unittest.main()
