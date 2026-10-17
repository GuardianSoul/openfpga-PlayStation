"""instance-packager.json against the rules of the two builders that read it:
pupdate's BuildInstanceJson (CoresService.Download.cs) and Pocket Sync's
openfpga-instance-packager (lib.rs build_jsons_for_core). 0.5.3 broke both:
no slot_limit (pupdate built nothing) and a "single" .bin slot (Pocket Sync
skipped every multi-track disc)."""
from fnmatch import fnmatch
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.instance_packager import instance_packager

# Slots the release core (--one-file-disc --cue-mount) declares for disc 1.
DECLARED = {99, 100, 101, 102, 103, 104}
FOLDERS = {
    'Crash Bandicoot (USA)': ['Crash Bandicoot (USA).cue', 'Crash Bandicoot (USA).bin'],
    'Tekken 3 (USA)': ['Tekken 3 (USA).cue'] + [f'Tekken 3 (USA) (Track {k}).bin' for k in (1, 2)],
    'Rayman (USA)': ['Rayman (USA).cue'] + [f'Rayman (USA) (Track {k}).bin' for k in range(1, 5)],
    'Ape Escape (Europe)': ['Ape Escape (Europe).cue', 'Ape Escape (Europe).bin', 'Ape Escape (Europe).sbi'],
    'Wipeout (USA)': ['Wipeout (USA).cue'] + [f'Wipeout (USA) (Track {k:02}).bin' for k in range(1, 10)],
}


def files(folder, pattern):
    return sorted(f.name for f in folder.iterdir() if fnmatch(f.name, pattern) and not f.name.startswith('.'))


def pupdate(packager, folder):
    """None for "Unable to build" or a skipped folder, else the slots."""
    slots = []
    for slot in packager['data_slots']:
        found = files(folder, slot['filename'])
        if slot['required'] and not found:
            return None
        slots += [(slot['id'] + k, f) for k, f in enumerate(found)]
    limit = packager.get('slot_limit')
    if limit is None:  # (long)slot_limit["count"] throws; caught per folder
        return None
    return slots if slots and len(slots) <= limit['count'] else None


def pocket_sync(packager, folder):
    slots = []
    for slot in packager['data_slots']:
        found = files(folder, slot['filename'])
        if slot['required'] and (not found or (slot['sort'] == 'single' and len(found) > 1)):
            return None
        slots += [(slot['id'] + k, f) for k, f in enumerate(found)]
    limit = packager.get('slot_limit')
    return None if limit and len(slots) > limit['count'] else slots


class InstancePackagerTest(unittest.TestCase):
    def test_release_core_game_files(self):
        packager = instance_packager('playstation', 'PlayStation', one_file_disc=True, cue_mount=True,
                                     libcrypt=False, multi_track=19)
        with TemporaryDirectory() as tmp:
            for name, names in FOLDERS.items():
                (Path(tmp)/name).mkdir()
                for f in names:
                    (Path(tmp)/name/f).touch()
                (Path(tmp)/name/('._' + names[0])).touch()  # macOS metadata
            for builder in (pupdate, pocket_sync):
                for name, names in FOLDERS.items():
                    with self.subTest(builder=builder.__name__, folder=name):
                        slots = builder(packager, Path(tmp)/name)
                        if name.startswith('Wipeout'):
                            self.assertIsNone(slots)  # 9 files: not declared
                            continue
                        self.assertIsNotNone(slots)
                        self.assertEqual(sorted(f for _, f in slots), sorted(names))
                        self.assertLessEqual({i for i, _ in slots}, DECLARED)
                        self.assertEqual(dict(slots)[100], names[0])

    def test_every_variant_has_a_slot_limit(self):
        for one_file_disc in (False, True):
            for cue_mount in (False, True) if one_file_disc else (False,):
                p = instance_packager('psx', 'X', one_file_disc=one_file_disc, cue_mount=cue_mount,
                                      libcrypt=False, multi_track=19)
                self.assertIn('count', p['slot_limit'])
                bins = next(s for s in p['data_slots'] if s['id'] == 101)
                self.assertEqual(bins['sort'], 'ascending')


if __name__ == '__main__':
    unittest.main()
