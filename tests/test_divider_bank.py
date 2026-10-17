"""Preparation guard mechanics; real CPU/GPU behavior has separate fixtures."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_divider_bank as bank
from prepare_resource_targets import digest, UPSTREAM
from tool_paths import ROOT


class DividerBankGuards(unittest.TestCase):
    def test_release_cpu_is_exactly_the_two_ram_entity_renames(self):
        from prepare_no_turbo_cache import no_turbo_cache_source
        from run_cpu_cache_contract import tag_trial
        with tempfile.TemporaryDirectory() as tmp:
            cpu = Path(no_turbo_cache_source(tmp)['path'])
            release = tag_trial(cpu.read_text(), 'falling')
            cpu.write_text(release)
            self.assertEqual(digest(cpu), bank.CPU_DPRAM_SHA)
            self.assertEqual(bank.cpu_profile(cpu), 'release-dpram-cache')
            strict = release.replace('entity work.dpram', 'entity work.pocket_dpram_sdp', 2)
            cpu.write_text(strict)
            self.assertEqual(bank.cpu_profile(cpu), 'strict-sdp-cache')
            for text in (release + '\n-- unreviewed\n',
                         release.replace('divider', 'rogue_divider'),
                         strict.replace('itagram : entity work.pocket_dpram_sdp',
                                        'itagram : entity work.dpram')):
                cpu.write_text(text)
                with self.assertRaisesRegex(ValueError, 'Unreviewed divider dependency'):
                    bank.cpu_profile(cpu)

    def test_external_and_compiled_projects_reject_before_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'ignored build'): bank.prepare_project(tmp)
        parent = ROOT / 'build/sim/divider-bank-guards'; parent.mkdir(parents=True, exist_ok=True)
        for marker in ('db', 'incremental_db', 'output_files', 'divider-bank'):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory(dir=parent) as tmp:
                (Path(tmp) / marker).mkdir()
                with self.assertRaisesRegex(ValueError, 'before map/fit'): bank.prepare_project(tmp)

    def test_state_enabled_project_rejects(self):
        parent = ROOT / 'build/sim/divider-bank-guards'; parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            (Path(tmp) / 'manifest.json').write_text(json.dumps({'reset_only': False}))
            with self.assertRaisesRegex(ValueError, 'reset-only'): bank.prepare_project(tmp)

    def test_untransformed_upstream_cpu_is_not_the_reviewed_consumer(self):
        parent = ROOT / 'build/sim/divider-bank-guards'; parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            d = Path(tmp); (d / 'manifest.json').write_text(json.dumps({'reset_only': True}))
            qsf = d / 'apf_top.qsf'
            qsf.write_text(f'set_global_assignment -name VHDL_FILE "{UPSTREAM / "rtl/cpu.vhd"}"\n')
            sha = digest(qsf)
            with self.assertRaisesRegex(ValueError, 'Unreviewed divider dependency: cpu.vhd'):
                bank.prepare_project(d)
            self.assertEqual(digest(qsf), sha)
            self.assertFalse((d / 'divider-bank').exists())

    def test_extra_consumer_and_source_drift_reject_without_mutation(self):
        # Small artificial consumers isolate project/ownership guard mechanics.
        # Patching only their expected hashes does not replace the real divider
        # or template identities and supplies no architectural equivalence proof.
        parent = ROOT / 'build/sim/divider-bank-guards'; parent.mkdir(parents=True, exist_ok=True)
        for mode in ('extra', 'changed', 'duplicate', 'positive'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=parent) as tmp:
                d = Path(tmp); (d / 'manifest.json').write_text(json.dumps({'reset_only': True}))
                (d / 'apf_top.qpf').write_text('PROJECT_REVISION = "apf_top"\n')
                (d / 'pocket.sdc').write_text('# unrelated constraints remain byte exact\n')
                sources = []; expected = {}
                for name in bank.CONSUMERS:
                    f = d / name
                    text = '-- guard fixture ' + name + '\n'
                    if name in ('cpu.vhd', 'gpu.vhd'): text += 'u: entity work.divider port map(clk=>clk);\n'
                    f.write_text(text); expected[name] = digest(f); sources.append(f)
                divider = d / 'divider.vhd'; shutil.copyfile(UPSTREAM / 'rtl/divider.vhd', divider); sources.append(divider)
                if mode == 'extra':
                    extra = d / 'unreviewed.vhd'; extra.write_text('u: entity work.divider port map(clk=>clk);\n'); sources.append(extra)
                if mode == 'changed': (d / 'gpu_poly.vhd').write_text('-- changed consumer\n')
                if mode == 'duplicate': sources.append(d / 'cpu.vhd')
                qsf = d / 'apf_top.qsf'
                qsf.write_text(''.join(f'set_global_assignment -name VHDL_FILE "{f}" -library work\n' for f in sources))
                original = {f: digest(f) for f in set(sources) | {qsf, d / 'pocket.sdc'}}
                with patch.dict(bank.CONSUMERS, expected, clear=True):
                    if mode != 'positive':
                        with self.assertRaises(ValueError): bank.prepare_project(d)
                        self.assertEqual(original, {f: digest(f) for f in original})
                        self.assertFalse((d / 'divider-bank').exists())
                    else:
                        result = bank.prepare_project(d)
                        self.assertEqual(result['candidate']['sha256'], bank.PACKED_SHA)
                        self.assertFalse(result['intermediate_busy_quotient_unchanged'])
                        self.assertFalse(result['production_selected'])
                        self.assertEqual(result['additional_guest_cycles'], 0)
                        for f in original:
                            if f != qsf: self.assertEqual(digest(f), original[f])
                        with self.assertRaisesRegex(ValueError, 'before map/fit'): bank.prepare_project(d)


if __name__ == '__main__': unittest.main()
