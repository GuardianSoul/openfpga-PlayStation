"""Audit optional producer-held operand project dependencies and provenance."""
from pathlib import Path
import tempfile
import unittest

from scripts.prepare_gpu_borrowed import apply_gpu_borrowed
from scripts.prepare_gpu_divider_pool import apply_gpu_divider_pool
from scripts.prepare_resource_targets import prepare


class GPUBorrowedTests(unittest.TestCase):
    def test_pool_dependency_and_other_components_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            for target in ('gpu','cpu'):
                with self.assertRaises(ValueError):
                    apply_gpu_borrowed(prepare(Path(temporary),[target]))

    def test_four_sources_preserve_selected_inputs_and_clock_constraints(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest=prepare(Path(temporary),['gpu']);apply_gpu_divider_pool(manifest)
            t=manifest['targets']['gpu'];before=[dict(s) for s in t['quartus_sources']]
            project=dict(t['project_sha256'])
            apply_gpu_borrowed(manifest)
            changed=[(a,b) for a,b in zip(before,t['quartus_sources']) if a!=b]
            self.assertEqual({Path(a['path']).name for a,b in changed},
                {'gpu_poly.vhd','gpu_line.vhd','gpu.vhd','psx_gpu_divider_pool.vhd'})
            for a,b in changed:
                self.assertEqual(b['selected'],a['path'])
                self.assertEqual(b['selected_sha256'],a['sha256'])
            self.assertEqual(project['.sdc'],t['project_sha256']['.sdc'])
            self.assertEqual(project['.qpf'],t['project_sha256']['.qpf'])
            self.assertFalse(t['gpu_borrowed']['game_verified'])
            with self.assertRaises(ValueError):apply_gpu_borrowed(manifest)


if __name__=='__main__':unittest.main()
