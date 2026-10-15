"""Guard control assumptions and retain real PSX instruction/scratchpad storage."""
from pathlib import Path
import tempfile
import unittest

from scripts.prepare_no_turbo_cache import apply_no_turbo_cache, no_turbo_cache_source
from scripts.prepare_resource_targets import digest, prepare


class NoTurboCacheTests(unittest.TestCase):
    def test_dynamic_or_enabled_cache_controls_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name in ('cpu','psx_top'):
                for control in ('TURBO_CACHE','TURBO_CACHE50'):
                    for value in ("'1'",control):
                        with self.subTest(name=name,control=control,value=value):
                            manifest=prepare(Path(temporary),[name])
                            target=manifest['targets'][name]; target['fixed_ports']=dict(target['fixed_ports'])
                            target['fixed_ports'][control]=value
                            with self.assertRaises(ValueError): apply_no_turbo_cache(manifest)
            with self.assertRaises(ValueError): apply_no_turbo_cache(prepare(Path(temporary),['gte']))

    def test_generated_cpu_retains_psx_storage_and_records_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            source=no_turbo_cache_source(Path(temporary))
            text=Path(source['path']).read_text()
            self.assertNotIn('idatacache : entity work.datacache',text)
            for marker in ('gcache: for i in 0 to 3 generate','itagram : entity mem.RamMLAB',
                           'gscratchpad: for i in 0 to 3 generate'):
                self.assertIn(marker,text)
            self.assertEqual(source['original_sha256'],digest(Path(source['original'])))
            self.assertEqual(source['required_controls'],{'TURBO_CACHE':0,'TURBO_CACHE50':0})
            self.assertTrue((Path(temporary)/'COPYING.GPL2').is_file())
            manifest=prepare(Path(temporary)/'project',['cpu']); target=manifest['targets']['cpu']
            sdc=target['project_sha256']['.sdc']; apply_no_turbo_cache(manifest)
            self.assertEqual(sdc,target['project_sha256']['.sdc'])
            copy=target['no_turbo_cache']
            self.assertEqual(sum(s['path']==copy['path'] for s in target['quartus_sources']),1)
            qsf=Path(target['directory'])/(target['top']+'.qsf')
            self.assertEqual(digest(qsf),target['project_sha256']['.qsf'])
            with self.assertRaises(ValueError): apply_no_turbo_cache(manifest)


if __name__=='__main__': unittest.main()
