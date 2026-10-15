"""Audit the bounded CD arithmetic variant's source and project identity."""
from pathlib import Path
import tempfile
import unittest

from scripts.prepare_cd_arithmetic import apply_cd_arithmetic, cd_arithmetic_source, arithmetic_functions
from scripts.prepare_resource_targets import digest, prepare


class CDArithmeticTests(unittest.TestCase):
    def test_only_arithmetic_changes_in_original_controller(self):
        with tempfile.TemporaryDirectory() as temporary:
            source=cd_arithmetic_source(Path(temporary))
            original=Path(source['original']).read_text()
            contents=Path(source['path']).read_text().split('\n',1)[1]
            old,new,wrap=arithmetic_functions()
            restored=contents.replace(new+'\n\n'+wrap,old).replace(
                'phy_newOffset <= wrapSectorOffset(phy_oldOffset, phy_spt);',
                'phy_newOffset <= (phy_oldOffset + 1) mod phy_spt;')
            self.assertEqual(restored,original)
            self.assertEqual(source['original_sha256'],digest(Path(source['original'])))
            self.assertEqual(source['required_ranges'],{'phy_oldOffset':[0,31],'phy_spt':[8,22]})
            self.assertTrue((Path(temporary)/'COPYING.GPL2').is_file())

    def test_project_records_variant_without_changing_constraints(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name in ('cd_top','psx_top'):
                with self.subTest(name=name):
                    manifest=prepare(Path(temporary),[name]); target=manifest['targets'][name]
                    sdc=target['project_sha256']['.sdc']; apply_cd_arithmetic(manifest)
                    self.assertEqual(sdc,target['project_sha256']['.sdc'])
                    copy=target['cd_arithmetic']
                    self.assertEqual(sum(s['path']==copy['path'] for s in target['quartus_sources']),1)
                    self.assertFalse(any(s['path']==copy['original'] for s in target['quartus_sources']))
                    self.assertEqual(copy['sha256'],digest(Path(copy['path'])))
                    qsf=Path(target['directory'])/(target['top']+'.qsf')
                    self.assertIn(copy['path'],qsf.read_text())
                    self.assertEqual(digest(qsf),target['project_sha256']['.qsf'])
                    with self.assertRaises(ValueError): apply_cd_arithmetic(manifest)
            with self.assertRaises(ValueError): apply_cd_arithmetic(prepare(Path(temporary),['cpu']))


if __name__=='__main__': unittest.main()
