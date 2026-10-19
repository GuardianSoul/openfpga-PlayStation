"""The release core's data.json (package_pocket_early.py, the 0.5.4 recipe's
--one-file-disc --cue-mount --disc-menu): the file list at launch offers game
files, .cue and .m3u, never a .bin. A picked track .bin has no track list, so
the disc plays without CD music and some games stop. Game files never name
slot 0, and every slot they do name keeps taking .bin, so 0.5.2-0.5.4 game
files, Swap Discs, the .sbi slot and the pupdate/Pocket Sync builders are
unchanged."""
from pathlib import Path
import json
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]


def package(tmp, *flags):
    project = Path(tmp)/'project'; out = Path(tmp)/'package'
    (project/'output_files').mkdir(parents=True)
    (project/'output_files/apf_top.fit.summary').write_text('Fitter Status : Successful\nTotal virtual pins : 0\n')
    (project/'output_files/apf_top.sta.summary').write_text('Type  : Setup clk\nSlack : 0.100\n')
    (project/'output_files/apf_top.rbf').write_bytes(bytes(20000))
    subprocess.run([sys.executable, str(ROOT/'scripts/package_pocket_early.py'), str(project), '--output', str(out),
                    '--platform', 'playstation', '--disc', '--multi-track', '4', '--one-file-disc', '--cue-mount',
                    '--memcard-slot', *flags], check=True, capture_output=True)
    core = next((out/'SD/Cores').iterdir())
    data = json.loads((core/'data.json').read_text())['data']['data_slots']
    return {s['id']: s for s in data}, json.loads((core/'instance-packager.json').read_text())


class LaunchSlot(unittest.TestCase):
    def test_launch_list_has_no_bin(self):
        with TemporaryDirectory() as tmp:
            slots, packager = package(tmp, '--disc-menu', '4')
            self.assertEqual(slots[0]['extensions'], ['json', 'cue', 'm3u'])
            # The game-file tools spot a cue-reading core by .cue in slot 0.
            self.assertIn('cue', slots[0]['extensions'])
            # Slots a game file names, old (0.5.2: track .bin files in
            # 101-104) or new (disc n's image in n01), still take .bin.
            for i in (101, 102, 103, 104, 201, 301, 401):
                self.assertIn('bin', slots[i]['extensions'], i)
            self.assertEqual(slots[100]['extensions'], ['cue', 'm3u'])
            self.assertEqual(slots[99]['extensions'], ['sbi'])
            # pupdate/Pocket Sync fill only declared slots, never slot 0.
            ids = {s['id'] for s in packager['data_slots']}
            self.assertNotIn(0, ids)
            self.assertLessEqual(ids, set(slots))
            for s in packager['data_slots']:
                self.assertIn(s['filename'].rsplit('.', 1)[1], slots[s['id']]['extensions'])

    def test_load_disc_without_disc_menu_keeps_bin(self):
        # Without --disc-menu, slot 101 is both "Load disc" and a game file's
        # disc image, so it keeps .bin.
        with TemporaryDirectory() as tmp:
            slots, _ = package(tmp)
            self.assertEqual(slots[0]['extensions'], ['json', 'cue', 'm3u'])
            self.assertIn('bin', slots[101]['extensions'])


if __name__ == '__main__':
    unittest.main()
