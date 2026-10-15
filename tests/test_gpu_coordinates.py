"""Check coordinate-pipeline provenance and unchanged project constraints."""
from pathlib import Path
import tempfile
import unittest

from scripts.prepare_gpu_coordinates import apply_gpu_coordinates
from scripts.prepare_gpu_line_share import apply_gpu_line_share
from scripts.prepare_gpu_timing import apply_gpu_timing_prune
from scripts.prepare_resource_targets import prepare


class GPUCoordinateTests(unittest.TestCase):
    def test_coordinate_pipeline_preserves_selected_source_and_constraints(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = prepare(Path(temporary), ['gpu'])
            apply_gpu_line_share(manifest)
            apply_gpu_timing_prune(manifest)
            target = manifest['targets']['gpu']
            sources = [dict(s) for s in target['quartus_sources']]
            project = dict(target['project_sha256'])
            apply_gpu_coordinates(manifest)
            changed = [(a,b) for a,b in zip(sources,target['quartus_sources']) if a != b]
            self.assertEqual(len(changed), 1)
            old,new = changed[0]
            self.assertEqual(new['selected'], old['path'])
            self.assertEqual(new['selected_sha256'], old['sha256'])
            self.assertEqual(new['coordinate_range'], [-2048,2047])
            self.assertEqual(new['negated_coordinate_range'], [-2047,2048])
            self.assertFalse(new['state_machine_latency_changed'])
            self.assertEqual(project['.sdc'], target['project_sha256']['.sdc'])
            self.assertEqual(project['.qpf'], target['project_sha256']['.qpf'])
            self.assertNotEqual(project['.qsf'], target['project_sha256']['.qsf'])
            with self.assertRaises(ValueError): apply_gpu_coordinates(manifest)

    def test_coordinate_pipeline_rejects_other_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                apply_gpu_coordinates(prepare(Path(temporary), ['cpu']))


if __name__ == '__main__': unittest.main()
