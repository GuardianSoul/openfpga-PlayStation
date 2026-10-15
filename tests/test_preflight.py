from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import unittest

from scripts.preflight import scan_dependencies, verify_support_manifest


class DependencyAuditTests(unittest.TestCase):
    def test_pinned_support_sources_are_present_and_unchanged(self):
        self.assertTrue(verify_support_manifest()['verified'])

    def test_manifest_detects_missing_and_modified_sources(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'support.v'
            source.write_text('original')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'pins': {}, 'limits': 'test fixture', 'files': [
                {'path': 'support.v', 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'role': 'test'}]}))
            self.assertTrue(verify_support_manifest(manifest, root)['verified'])
            source.write_text('modified')
            self.assertFalse(verify_support_manifest(manifest, root)['verified'])
            source.unlink()
            self.assertFalse(verify_support_manifest(manifest, root)['verified'])

    def test_generated_headers_and_commented_includes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'core.sflp').write_text(
                '%i "child.h"\n// %i "ignored.h"\n/* %i "ignored2.h" */\n')
            (root / 'child.sflp').write_text('%i "absent.h"\n%i "core.h"\n')
            report = scan_dependencies(root, Path('core.sflp'))
            self.assertEqual(report['sources'], ['child.sflp', 'core.sflp'])
            self.assertEqual([m['include'] for m in report['missing']], ['absent.h'])
            self.assertEqual(report['missing'][0]['line'], 1)

    def test_external_dependency_cannot_be_satisfied_by_ambient_files(self):
        with TemporaryDirectory() as directory:
            outer = Path(directory)
            root = outer / 'reference'
            root.mkdir()
            (outer / 'external.h').write_text('ambient dependency')
            (root / 'core.sflp').write_text('%i "../external.h"\n')
            report = scan_dependencies(root, Path('core.sflp'))
            self.assertEqual(report['missing'][0]['reason'], 'outside pinned repository')
