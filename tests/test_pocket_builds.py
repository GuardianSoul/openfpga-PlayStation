"""Prevent full-top measurement flags from silently pruning save paths."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class PocketBuildTests(unittest.TestCase):
    def test_probe_rejects_settings_edits_but_records_quartus_version_append(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        from build_pocket import compiler_version_append_only
        original='set_global_assignment -name SDC_FILE pocket.sdc\n'
        version='\nset_global_assignment -name LAST_QUARTUS_VERSION "25.1std.0 Lite Edition"'
        self.assertTrue(compiler_version_append_only(original,original+version))
        self.assertFalse(compiler_version_append_only(original,original.replace('pocket.sdc','other.sdc')+version))
        self.assertFalse(compiler_version_append_only(original,original+'set_global_assignment -name SEED 2\n'+version))

    def test_probe_selects_exact_hardware_MIRLO_phy_and_clocks(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        from build_pocket import prepare_probe
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)/'mirlo'
            manifest=prepare_probe(directory)
            phy=Path(manifest['sdram_phy_source']['path']).read_text()
            original=(ROOT/'references/MIRLO64/rtl/soc/sdr_phy.sv').read_text()
            self.assertEqual(phy.split('\n',1)[1],original.replace('module sdr_phy (','module psx_sdr_phy ('))
            clocks=(directory/'psx_mem_clocks.sv').read_text()
            self.assertIn('frequency0("62.500000 MHz")',clocks)
            self.assertIn('frequency1("125.000000 MHz")',clocks)
            self.assertIn('phase_shift2("4000 ps")',clocks)
            self.assertNotIn('31.250000 MHz',clocks)
            self.assertEqual(manifest['refresh_interval_us'],3.84)
            self.assertFalse(manifest['hardware_verified'])
            self.assertIn('general[2].gpll',(directory/'pocket.sdc').read_text())
            experimental=prepare_probe(Path(temporary)/'falling',sdram_phy='falling')
            self.assertEqual(experimental['client_clock_mhz'],31.25)
            self.assertIn('failed Pocket BIOS CRC',experimental['hardware_selection_note'])

    def prepare(self, directory, *flags):
        result=subprocess.run([sys.executable,str(ROOT/'scripts/prepare_full_pocket.py'),
            '--prepare-only','--output',str(directory),*flags],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads((Path(result.stdout.strip())/'manifest.json').read_text())

    def test_matched_pair_keeps_requested_variants_and_live_save_controls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            enabled=self.prepare(root/'on','--candidate','--save-adapters')
            disabled=self.prepare(root/'off','--candidate','--save-adapters','--reset-only')
            self.assertTrue(all(enabled['variant_flags'].values()))
            self.assertEqual(enabled['variant_flags'],disabled['variant_flags'])
            self.assertEqual(enabled['compile_settings'],disabled['compile_settings'])
            self.assertTrue(enabled['save_state_paths_enabled'])
            self.assertFalse(disabled['save_state_paths_enabled'])
            on=enabled['system_manifest']['targets']['psx_top']
            off=disabled['system_manifest']['targets']['psx_top']
            for port in ('save_state','load_state','rewind_on','rewind_active','savestate_number'):
                self.assertNotIn(port,on['fixed_ports'])
                self.assertIn(port,on['external_ports'])
                self.assertIn(port,off['fixed_ports'])
            self.assertTrue(on['gte_adapter']['savestate_transfers'])
            self.assertTrue(on['spu_context_ram']['save_transfer_supported'])
            self.assertFalse(enabled['release_ready'])
            for manifest in (enabled,disabled):
                self.assertEqual(manifest['video_adapter']['sync_pulse_clocks'],1)
                self.assertTrue(manifest['video_adapter']['RGB_black_outside_DE'])
                core=next(Path(s['path']) for s in manifest['sources'] if Path(s['path']).name=='core_top.sv')
                text=core.read_text()
                self.assertIn('apf_hs<=hsync && !apf_hs_d',text)
                self.assertIn('assign video_skip=apf_skip, video_rgb=apf_rgb;',text)
                self.assertNotIn('video_hs=hsync',text)

    def test_candidate_without_extensions_rejects_save_on(self):
        with tempfile.TemporaryDirectory() as temporary:
            result=subprocess.run([sys.executable,str(ROOT/'scripts/prepare_full_pocket.py'),
                '--prepare-only','--candidate','--output',temporary],cwd=ROOT,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Candidate GTE control contract not satisfied',result.stderr)


if __name__=='__main__':unittest.main()
