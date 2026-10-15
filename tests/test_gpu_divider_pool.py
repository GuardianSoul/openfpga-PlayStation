"""Audit optional divider-pool selection and dependency/reset wiring."""
from pathlib import Path
import tempfile
import unittest

from scripts.prepare_gpu_divider_pool import apply_gpu_divider_pool
from scripts.prepare_resource_targets import prepare, digest
from scripts.prepare_system_verilator import prepare as prepare_sim


class DividerPoolSourcesTests(unittest.TestCase):
    def test_pool_is_optional_and_scheduler_precedes_the_gpu(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            normal=prepare_sim(root/'normal')
            self.assertIsNone(normal['gpu_divider_pool'])
            self.assertFalse(any(Path(source['path']).name=='psx_gpu_divider_pool.vhd' for source in normal['sources']))
            candidate=prepare_sim(root/'pool',gpu_divider_pool=True)
            names=[Path(source['path']).name for source in candidate['sources']]
            self.assertLess(names.index('psx_gpu_divider_pool.vhd'),names.index('gpu.vhd'))
            gpu=next(source for source in candidate['sources'] if Path(source['path']).name=='gpu.vhd')
            text=Path(gpu['path']).read_text()
            self.assertIn('reset=>softreset or SS_reset',text)
            self.assertEqual(text.count('entity work.psx_gpu_divider_pool'),1)
            self.assertEqual(gpu['original_sha256'],digest(Path(gpu['original'])))
            self.assertFalse(candidate['production_reference_modified'])

    def test_quartus_target_keeps_original_arithmetic_and_records_both_added_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest=prepare(Path(temporary),['gpu'])
            target=manifest['targets']['gpu']
            divider=next(source.copy() for source in target['quartus_sources'] if Path(source['path']).name=='divider.vhd')
            apply_gpu_divider_pool(manifest)
            self.assertIn(divider,target['quartus_sources'])
            pool=target['gpu_divider_pool']
            self.assertFalse(pool['game_verified'])
            self.assertFalse(pool['fit_verified'])
            for source in (pool,pool['scheduler']):
                self.assertIn(str(source['path']),(Path(target['directory'])/(target['top']+'.qsf')).read_text())
                self.assertEqual(source['original_sha256'],digest(Path(source['original'])))
            with self.assertRaises(ValueError):apply_gpu_divider_pool(manifest)


if __name__=='__main__':unittest.main()
