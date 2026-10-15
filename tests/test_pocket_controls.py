"""Keep guest buttons/cards while bounding host controller options."""
from pathlib import Path
import tempfile
import unittest

from scripts.prepare_pocket_controls import apply_pocket_controls
from scripts.prepare_resource_targets import apply_compile_profile, digest, prepare


class PocketControlTests(unittest.TestCase):
    def test_digital_configuration_preserves_buttons_and_both_card_interfaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name in ('joypad','psx_top'):
                with self.subTest(name=name):
                    manifest=prepare(Path(temporary),[name]); target=manifest['targets'][name]
                    sdc=target['project_sha256']['.sdc']
                    apply_pocket_controls(manifest)
                    external=target['external_ports']
                    self.assertEqual(len([p for p in external if p.startswith('joypad1_Key')]),16)
                    self.assertFalse(any(p.startswith(('joypad2_','joypad3_','joypad4_')) and not p.endswith('_rumble') for p in external))
                    self.assertEqual(target['pocket_controls']['guest_memory_cards'],2)
                    self.assertIn('memcard1_available' if name=='joypad' else 'memcard1_mounted',external)
                    self.assertIn('memcard2_available' if name=='joypad' else 'memcard2_mounted',external)
                    self.assertEqual(sdc,target['project_sha256']['.sdc'])
                    path=Path(target['directory'])/(target['top']+'.vhd')
                    source=next(s for s in target['quartus_sources'] if s['path']==str(path))
                    self.assertEqual(source['sha256'],digest(path))
                    with self.assertRaises(ValueError): apply_pocket_controls(manifest)

    def test_configuration_rejects_other_targets_and_prior_profiles(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError): apply_pocket_controls(prepare(Path(temporary),['cpu']))
            manifest=prepare(Path(temporary),['joypad']); apply_compile_profile(manifest,'area')
            with self.assertRaises(ValueError): apply_pocket_controls(manifest)


if __name__=='__main__': unittest.main()
