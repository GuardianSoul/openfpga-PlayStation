"""Audit the optional scanline sharing project and declared timing tradeoff."""
from pathlib import Path
import tempfile
import unittest

from scripts.prepare_gpu_line_share import shared_line_source,apply_gpu_line_share
from scripts.prepare_resource_targets import prepare,digest
from scripts.reference_system_memory_program import write_program


class GPUSharingTests(unittest.TestCase):
    def test_shared_unit_retains_original_reference_and_declares_added_latency(self):
        with tempfile.TemporaryDirectory() as temporary:
            source=shared_line_source(Path(temporary))
            self.assertEqual(source['original_sha256'],digest(Path(source['original'])))
            self.assertEqual(source['sha256'],digest(Path(source['path'])))
            text=Path(source['path']).read_text()
            self.assertEqual(text.count('imul32u : entity work.mul32u'),1)
            self.assertIn('mulIn1 <= lineGradient when state=CALCLINE',text)
            self.assertNotIn('resize(dxR * to_integer(xStart',text)
            self.assertTrue(source['state_machine_latency_changed'])
            self.assertFalse(source['pixel_equivalence_verified'])
            self.assertFalse(source['hardware_verified'])

    def test_project_variant_replaces_only_polygon_source_without_claiming_fit(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest=prepare(Path(temporary),['gpu'])
            before=[s['path'] for s in manifest['targets']['gpu']['quartus_sources']]
            result=apply_gpu_line_share(manifest);target=result['targets']['gpu']
            after=[s['path'] for s in target['quartus_sources']]
            self.assertEqual(sum(a!=b for a,b in zip(before,after)),1)
            self.assertFalse(target['gpu_line_share']['fit_verified'])
            with self.assertRaises(ValueError):apply_gpu_line_share(result)

    def test_ram_program_reports_real_checks_separately_from_monitor_sentinels(self):
        with tempfile.TemporaryDirectory() as temporary:
            program=write_program(Path(temporary))
            self.assertEqual(len(program['expected'].read_text().splitlines()),2648)
            self.assertEqual(program['main_ram_checks']+program['sentinel_checks'],2648)
            self.assertLess(program['instructions']*4,524288)
            self.assertFalse(program['cpu_cache_hits_verified'])


if __name__=='__main__':unittest.main()
