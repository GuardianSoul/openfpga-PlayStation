"""Reject timing-mode misuse and retain the selected upstream render sources."""
from pathlib import Path
import tempfile
import unittest

from scripts.prepare_gpu_line_share import apply_gpu_line_share
from scripts.prepare_gpu_timing import apply_gpu_timing_prune
from scripts.prepare_resource_targets import prepare


class GPUTimingTests(unittest.TestCase):
    def test_enabled_or_dynamic_timing_control_cannot_use_the_specialization(self):
        with tempfile.TemporaryDirectory() as temporary:
            for value in ("'1'", 'runtime_control'):
                manifest=prepare(Path(temporary),['gpu'])
                manifest['targets']['gpu']['fixed_ports']=dict(manifest['targets']['gpu']['fixed_ports'])
                manifest['targets']['gpu']['fixed_ports']['REPRODUCIBLEGPUTIMING']=value
                with self.assertRaises(ValueError):apply_gpu_timing_prune(manifest)
            with self.assertRaises(ValueError):apply_gpu_timing_prune(prepare(Path(temporary),['cpu']))

    def test_pruning_can_follow_line_sharing_without_discarding_that_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest=prepare(Path(temporary),['gpu'])
            apply_gpu_line_share(manifest)
            target=manifest['targets']['gpu']
            previous=target['gpu_line_share']
            sdc=target['project_sha256']['.sdc']
            apply_gpu_timing_prune(manifest)
            adapted=target['gpu_timing_prune']
            self.assertEqual(adapted['selected'],previous['path'])
            self.assertEqual(adapted['selected_sha256'],previous['sha256'])
            self.assertEqual(sdc,target['project_sha256']['.sdc'])
            self.assertFalse(adapted['state_machine_latency_changed'])
            self.assertEqual(adapted['required_controls'],{'REPRODUCIBLEGPUTIMING':0})
            self.assertIn('when CALCLINE =>',Path(adapted['path']).read_text())
            self.assertEqual(len(adapted['auxiliary_sources']),5)
            self.assertEqual({Path(s['path']).stem for s in adapted['auxiliary_sources']},
                {'gpu_rect','gpu_line','gpu_vram2vram','gpu_vram2cpu','gpu_fillVram'})
            with self.assertRaises(ValueError):apply_gpu_timing_prune(manifest)


if __name__=='__main__':unittest.main()
