"""Entry probes cannot silently become successful saves or unbound ROM updates."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from build_save_abort_probe import probe_source
from run_frozen_pocket_fit import abort_probe_identity, digest


class AbortProbeTest(unittest.TestCase):
    def fixture(self, root):
        for name, text in {'apf_top.qsf': 'selected graph', 'apf_top.qpf': 'project', 'pocket.sdc': '# clock',
                           'db/routing': 'unchanged placement',
                           'physical-result.json': '{}', 'release-timing.json': '{"passed": false}',
                           'save-states/psx_save_rom40.hex': 'fitted ROM'}.items():
            path = root / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(text)
        return dict(save_abort_probe=dict(before_sha256=digest(root / 'save-states/psx_save_rom40.hex'),
                                         after_sha256='a' * 64),
                    test_only_assemble_despite_setup_failure='Owner authorizes this diagnostic only',
                    base_sha256={str(p.relative_to(root)): digest(p) for p in root.rglob('*') if p.is_file()})

    def test_probe_refuses_saves_instead_of_creating_a_false_state(self):
        source = (ROOT / 'rtl/pocket/save_fw/save_handler.S').read_text()
        probe = probe_source(source)
        self.assertTrue(probe.startswith('        .equ    NOOP, 1\n'))
        self.assertIn('li      $t0, 2                  # is refused;', probe)
        with self.assertRaisesRegex(ValueError, 'NOOP entry block'):
            probe_source(source.replace('# NOOP builds', '# other build'))

    def test_failed_timing_stays_failed_with_explicit_test_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = self.fixture(root)
            self.assertEqual(abort_probe_identity(root, request), request['base_sha256'])
            self.assertFalse(json.loads((root / 'release-timing.json').read_text())['passed'])

    def test_early_finish_precedes_staging_and_removes_late_toggle(self):
        source = (ROOT / 'rtl/pocket/save_fw/save_handler.S').read_text()
        probe = probe_source(source, early_finish=True)
        self.assertLess(probe.index('sb      $k1, FINISH'), probe.index('        GPRS    sw'))
        self.assertNotIn('sb      $t0, FINISH', probe[probe.index('.ifdef  NOOP'):probe.index('        lui     $t2, 0xFFFF')])
        self.assertEqual(probe.count('sb      $k1, FINISH'), 1)
        with self.assertRaisesRegex(ValueError, 'entry and one NOOP'):
            probe_source(source.replace('entry:\n', 'other_entry:\n'), early_finish=True)

    def test_early_mode_is_strictly_bound_to_probe_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = self.fixture(root)
            request['save_abort_finish_early'] = True
            self.assertEqual(abort_probe_identity(root, request), request['base_sha256'])
            request['save_abort_finish_early'] = 'true'
            with self.assertRaisesRegex(ValueError, 'boolean true'):
                abort_probe_identity(root, request)
            request['save_abort_finish_early'] = True
            del request['save_abort_probe']
            with self.assertRaisesRegex(ValueError, 'explicit entry-probe'):
                abort_probe_identity(root, request)

    def test_missing_authorization_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = self.fixture(root)
            request.pop('test_only_assemble_despite_setup_failure')
            with self.assertRaisesRegex(ValueError, 'authorization'):
                abort_probe_identity(root, request)

    def test_kernel_firmware_rejects_unpaired_cpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = self.fixture(root)
            (root / 'cpu.vhd').write_text('-- original or otherwise unqualified CPU\n')
            (root / 'apf_top.qsf').write_text('set_global_assignment -name VHDL_FILE "cpu.vhd"\n')
            request['base_sha256'] = {str(p.relative_to(root)): digest(p)
                                     for p in root.rglob('*') if p.is_file()}
            request['save_abort_kernel_gprs'] = True
            with self.assertRaisesRegex(ValueError, 'exact qualified paired CPU'):
                abort_probe_identity(root, request)

    def test_kernel_mode_requires_explicit_probe_and_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = self.fixture(root)
            for value in ('true', 1, False):
                request['save_abort_kernel_gprs'] = value
                with self.assertRaisesRegex(ValueError, 'boolean true'):
                    abort_probe_identity(root, request)
            request['save_abort_kernel_gprs'] = True
            del request['save_abort_probe']
            with self.assertRaisesRegex(ValueError, 'explicit entry-probe'):
                abort_probe_identity(root, request)

    def test_kernel_capture_uses_closed_relocated_cpu_and_fitted_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = self.fixture(root)
            old = '/gone/jenkins-fit'
            (root / 'cpu.vhd').write_text('-- unit fixture CPU\n')
            cpu_hash = digest(root / 'cpu.vhd')
            (root / 'apf_top.qsf').write_text(
                f'set_global_assignment -name VHDL_FILE "{old}/cpu.vhd"\n')
            fitted = dict(source_manifest=[dict(path=old + '/cpu.vhd', assignment='VHDL_FILE', sha256=cpu_hash)])
            (root / 'physical-result.json').write_text(json.dumps(fitted))
            request.update(repoint_from=old, save_abort_kernel_gprs=True)
            request['base_sha256'] = {str(p.relative_to(root)): digest(p)
                                     for p in root.rglob('*') if p.is_file()}
            # Fixture fingerprint only: production retains its real CPU hash.
            with patch('prepare_save_kernel_gprs.QUALIFIED_CPU', cpu_hash):
                self.assertEqual(abort_probe_identity(root, request), request['base_sha256'])
                fitted['source_manifest'][0]['sha256'] = '0' * 64
                (root / 'physical-result.json').write_text(json.dumps(fitted))
                request['base_sha256']['physical-result.json'] = digest(root / 'physical-result.json')
                with self.assertRaisesRegex(ValueError, 'fitted CPU identity'):
                    abort_probe_identity(root, request)
                request['repoint_from'] = '/different/original'
                with self.assertRaisesRegex(ValueError, 'escapes the protected capture'):
                    abort_probe_identity(root, request)

    def test_trace_profile_requires_paired_fitted_cpu_and_logger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); request=self.fixture(root)
            request['save_trace_diagnostic']=True
            with self.assertRaisesRegex(ValueError, 'explicit paired kernel'):
                abort_probe_identity(root, request)
            request['save_abort_kernel_gprs']=True
            old='/gone/trace-fit'
            (root/'cpu.vhd').write_text('-- trace CPU unit fixture\n')
            (root/'psx_save_trace.sv').write_text('// trace logger unit fixture\n')
            cpu_hash=digest(root/'cpu.vhd');logger_hash=digest(root/'psx_save_trace.sv')
            (root/'apf_top.qsf').write_text(f'set_global_assignment -name VHDL_FILE "{old}/cpu.vhd"\n'
                                          f'set_global_assignment -name SYSTEMVERILOG_FILE "{old}/psx_save_trace.sv"\n')
            fitted=dict(source_manifest=[dict(path=old+'/cpu.vhd',assignment='VHDL_FILE',sha256=cpu_hash),
                                        dict(path=old+'/psx_save_trace.sv',assignment='SYSTEMVERILOG_FILE',sha256=logger_hash)])
            (root/'physical-result.json').write_text(json.dumps(fitted))
            request.update(repoint_from=old)
            request['base_sha256']={str(p.relative_to(root)):digest(p) for p in root.rglob('*') if p.is_file()}
            with patch('prepare_save_trace.TRACE_CPU_SHA256',cpu_hash), patch('prepare_save_trace.TRACE_LOGGER_SHA256',logger_hash):
                self.assertEqual(abort_probe_identity(root,request),request['base_sha256'])
                del request['save_trace_diagnostic']
                with self.assertRaisesRegex(ValueError,'exact qualified paired CPU'):
                    abort_probe_identity(root,request)
                request['save_trace_diagnostic']=True
                fitted['source_manifest'][1]['sha256']='0'*64
                (root/'physical-result.json').write_text(json.dumps(fitted))
                request['base_sha256']['physical-result.json']=digest(root/'physical-result.json')
                with self.assertRaisesRegex(ValueError,'qualified fitted logger'):
                    abort_probe_identity(root,request)

    def test_unbound_database_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = self.fixture(root)
            request['base_sha256'].pop('db/routing')
            with self.assertRaisesRegex(ValueError, 'closure is incomplete'):
                abort_probe_identity(root, request)

    def test_changed_baseline_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = self.fixture(root)
            (root / 'db/routing').write_text('other placement')
            with self.assertRaisesRegex(ValueError, 'baseline drift'):
                abort_probe_identity(root, request)

    def test_foreign_before_rom_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = self.fixture(root)
            request['save_abort_probe']['before_sha256'] = 'b' * 64
            with self.assertRaisesRegex(ValueError, 'ROM hash mismatch'):
                abort_probe_identity(root, request)


if __name__ == '__main__':
    unittest.main()
