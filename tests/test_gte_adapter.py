"""Guard the experimental geometry replacement's source and control contracts."""
from pathlib import Path
import tempfile
import unittest

from scripts.prepare_gte_adapter import apply_gte_adapter
from scripts.prepare_resource_targets import digest, prepare


class GTEAdapterTests(unittest.TestCase):
    def test_adapter_rejects_dynamic_optional_controls(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name,ports in (('gte',('WIDESCREEN','TURBO','loading_savestate','SS_wren','SS_rden')),
                               ('psx_top',('WIDESCREEN','TURBO_COMP','save_state','load_state','rewind_on','rewind_active'))):
                for port in ports:
                    with self.subTest(name=name,port=port):
                        manifest=prepare(Path(temporary),[name])
                        manifest['targets'][name]['fixed_ports']=dict(manifest['targets'][name]['fixed_ports'])
                        manifest['targets'][name]['fixed_ports'][port]=port
                        with self.assertRaises(ValueError): apply_gte_adapter(manifest)
            with self.assertRaises(ValueError): apply_gte_adapter(prepare(Path(temporary),['cpu']))

    def test_replacement_sources_are_separate_audited_and_in_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest=prepare(Path(temporary),['psx_top'])
            target=manifest['targets']['psx_top']; sdc=target['project_sha256']['.sdc']
            apply_gte_adapter(manifest)
            with self.assertRaises(ValueError): apply_gte_adapter(manifest)
            self.assertNotEqual(sdc,target['project_sha256']['.sdc'])
            constraints=(Path(target['directory'])/(target['top']+'.sdc')).read_text()
            self.assertIn('set_multicycle_path -from $gte_state -to $gte_state -setup 2',constraints)
            self.assertNotIn('false_path',constraints)
            qsf=Path(target['directory'])/(target['top']+'.qsf')
            self.assertEqual(digest(qsf),target['project_sha256']['.qsf'])
            for source in target['quartus_sources']:
                self.assertEqual(digest(Path(source['path'])),source['sha256'])
                self.assertIn(source['path'],qsf.read_text())
                if 'original' in source:
                    self.assertNotEqual(source['path'],source['original'])
                    self.assertEqual(digest(Path(source['original'])),source['original_sha256'])
            self.assertFalse(target['gte_adapter']['cpu_execution_verified'])
            self.assertFalse(target['gte_adapter']['hardware_verified'])


if __name__=='__main__': unittest.main()
