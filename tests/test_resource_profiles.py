"""Verify experiment identity and reject unsafe specialization configurations."""
from pathlib import Path
import tempfile
import unittest

from scripts.prepare_resource_targets import apply_compile_profile, apply_reset_only, prepare
from scripts.prepare_spu_context_ram import apply_spu_context_ram


class ResourceProfileTests(unittest.TestCase):
    def test_spu_context_ram_rejects_savestate_readback_and_unspecialized_system(self):
        with tempfile.TemporaryDirectory() as temporary:
            for value in ("'1'", 'SS_rden'):
                manifest = prepare(Path(temporary), ['spu'])
                manifest['targets']['spu']['fixed_ports']['SS_rden'] = value
                with self.assertRaises(ValueError): apply_spu_context_ram(manifest)
            manifest = prepare(Path(temporary), ['psx_top'])
            with self.assertRaises(ValueError): apply_spu_context_ram(manifest)
            apply_reset_only(manifest)
            for control in ('save_state', 'load_state', 'rewind_on', 'rewind_active', 'loadExe'):
                manifest['targets']['psx_top']['fixed_ports'][control] = control
                with self.assertRaises(ValueError): apply_spu_context_ram(manifest)
                manifest['targets']['psx_top']['fixed_ports'][control] = "'0'"

    def test_spu_context_ram_keeps_clock_constraints_and_other_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name in ('spu', 'psx_top'):
                manifest = prepare(Path(temporary), [name])
                if name == 'psx_top': apply_reset_only(manifest)
                target = manifest['targets'][name]
                prior_sources = [dict(s) for s in target['quartus_sources']]
                prior_project = dict(target['project_sha256'])
                apply_spu_context_ram(manifest)
                changed = [(a, b) for a, b in zip(prior_sources, target['quartus_sources']) if a != b]
                self.assertEqual(len(changed), 1)
                self.assertEqual(Path(changed[0][0]['path']).name, 'spu.vhd')
                self.assertEqual(changed[0][1]['original_sha256'], changed[0][0]['sha256'])
                self.assertEqual(prior_project['.sdc'], target['project_sha256']['.sdc'])
                self.assertEqual(prior_project['.qpf'], target['project_sha256']['.qpf'])
                self.assertNotEqual(prior_project['.qsf'], target['project_sha256']['.qsf'])
                with self.assertRaises(ValueError): apply_spu_context_ram(manifest)

    def test_minimal_state_profile_quotes_the_multiword_quartus_value(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest=prepare(Path(temporary),['cpu'])
            target=manifest['targets']['cpu']
            sources=[dict(source) for source in target['quartus_sources']]
            clocks=target['project_sha256']['.sdc']
            apply_compile_profile(manifest,'area-min-state')
            qsf=Path(target['directory'])/(target['top']+'.qsf')
            self.assertIn('set_global_assignment -name STATE_MACHINE_PROCESSING "MINIMAL BITS"\n',qsf.read_text())
            self.assertEqual(target['compile_settings']['STATE_MACHINE_PROCESSING'],'MINIMAL BITS')
            self.assertEqual(sources,target['quartus_sources'])
            self.assertEqual(clocks,target['project_sha256']['.sdc'])

    def test_area_profile_changes_only_compiler_assignments(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest=prepare(Path(temporary),['psx_top'])
            target=manifest['targets']['psx_top']
            original=dict(target['project_sha256'])
            sources=[dict(source) for source in target['quartus_sources']]
            apply_compile_profile(manifest,'area')
            self.assertNotEqual(original['.qsf'],target['project_sha256']['.qsf'])
            self.assertEqual(original['.sdc'],target['project_sha256']['.sdc'])
            self.assertEqual(original['.qpf'],target['project_sha256']['.qpf'])
            self.assertEqual(sources,target['quartus_sources'])
            self.assertEqual(target['compile_settings']['PHYSICAL_SYNTHESIS_REGISTER_RETIMING'],'OFF')
            with self.assertRaises(ValueError): apply_compile_profile(manifest,'baseline')

    def test_reset_specialization_rejects_enabled_or_dynamic_requests(self):
        with tempfile.TemporaryDirectory() as temporary:
            for control in ('save_state','load_state','rewind_on','rewind_active','loadExe'):
                with self.subTest(control=control):
                    manifest=prepare(Path(temporary),['psx_top'])
                    target=manifest['targets']['psx_top']
                    target['fixed_ports']=dict(target['fixed_ports'])
                    target['fixed_ports'][control]=control
                    with self.assertRaises(ValueError): apply_reset_only(manifest)
            with self.assertRaises(ValueError):
                apply_reset_only(prepare(Path(temporary),['cpu']))

    def test_reset_experiment_records_the_replaced_source_and_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest=prepare(Path(temporary),['psx_top'])
            target=manifest['targets']['psx_top']
            original=next(source for source in target['quartus_sources'] if source['path'].endswith('/savestates.vhd'))
            apply_reset_only(manifest)
            replacement=target['reset_only_specialization']
            self.assertEqual(replacement['original'],original['path'])
            self.assertEqual(replacement['original_sha256'],original['sha256'])
            self.assertNotEqual(replacement['sha256'],original['sha256'])
            self.assertEqual(replacement['required_controls'],{'save':0,'load':0,'loadExe':0})
            self.assertEqual(replacement['reset_write_fields'],[12,16])
            self.assertEqual(replacement['required_generics'],{'SAVETYPESCOUNT':17})
            self.assertEqual(sum(s['path']==replacement['path'] for s in target['quartus_sources']),1)


if __name__=='__main__': unittest.main()
