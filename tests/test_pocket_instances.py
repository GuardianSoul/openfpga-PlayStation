from pathlib import Path
import json
import os
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from scripts.make_pocket_instances import SECTOR, check, convert, parse_cue, split_cue

ROOT = Path(__file__).resolve().parents[1]


def sectors(tag, count):
    return b''.join(bytes([tag, n & 0xff]) * (SECTOR//2) for n in range(count))


MERGED = '''FILE "Game.bin" BINARY
  TRACK 01 MODE2/2352
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    INDEX 00 00:00:10
    INDEX 01 00:02:10
  TRACK 03 AUDIO
    PREGAP 00:02:00
    INDEX 01 00:02:60
'''


def merged_image(folder):
    # Track 1: 10 data sectors; track 2: 150 stored pregap + 50; track 3: 30, pregap not stored.
    (folder/'Game.bin').write_bytes(sectors(1, 10) + sectors(2, 200) + sectors(3, 30))
    (folder/'Game.cue').write_text(MERGED)


class SplitCue(unittest.TestCase):
    def test_merged_image_becomes_redump_layout(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            merged_image(folder)
            cue = split_cue(folder/'Game.cue', folder/'Game')
            files = parse_cue(cue)
            self.assertIsNone(check(files, cue.parent))
            self.assertEqual([name for name, _ in files],
                             ['Game (Track 01).bin', 'Game (Track 02).bin', 'Game (Track 03).bin'])
            self.assertEqual((cue.parent/files[0][0]).read_bytes(), sectors(1, 10))
            self.assertEqual((cue.parent/files[1][0]).read_bytes(), sectors(2, 200))
            # Missing pregap is synthesized as silence ahead of the track data.
            self.assertEqual((cue.parent/files[2][0]).read_bytes(), bytes(150*SECTOR) + sectors(3, 30))
            self.assertTrue((folder/'Game.bin').is_file())

    def test_single_track_moves_the_image(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'One.bin').write_bytes(sectors(1, 5))
            (folder/'One.cue').write_text('FILE "One.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
            cue = split_cue(folder/'One.cue', folder/'One', move=True)
            self.assertEqual((cue.parent/'One.bin').read_bytes(), sectors(1, 5))
            self.assertFalse((folder/'One.bin').exists())
            self.assertIsNone(check(parse_cue(cue), cue.parent))

    def test_rejects_short_pregap(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            merged_image(folder)
            (folder/'Game.cue').write_text(MERGED.replace('INDEX 00 00:00:10', 'INDEX 00 00:01:10'))
            with self.assertRaisesRegex(ValueError, 'pregap'):
                split_cue(folder/'Game.cue', folder/'Game')

    def test_rejects_cooked_sectors(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            merged_image(folder)
            (folder/'Game.cue').write_text(MERGED.replace('MODE2/2352', 'MODE1/2048'))
            with self.assertRaisesRegex(ValueError, '2352'):
                split_cue(folder/'Game.cue', folder/'Game')


FAKE_CHDMAN = '''import sys, shutil
args = dict(zip(sys.argv[2::2], sys.argv[3::2]))
source = sys.argv[sys.argv.index('-i') + 1]
if source.endswith('Broken.chd'):
    print('Error: bad hunk', file=sys.stderr); sys.exit(1)
shutil.copy(source[:-4] + '.src.bin', args['-ob'])
open(args['-o'], 'w').write(open(source[:-4] + '.src.txt').read().replace('Game.bin', __import__('os').path.basename(args['-ob'])))
'''


class Convert(unittest.TestCase):
    def test_chd_and_merged_cue_end_up_as_instances(self):
        with TemporaryDirectory() as tmp:
            card = Path(tmp)
            common = card/'Assets/playstation/common'
            (common/'chd').mkdir(parents=True)
            (common/'cue').mkdir()
            merged_image(common/'cue')
            (common/'chd/Disc.src.bin').write_bytes((common/'cue/Game.bin').read_bytes())
            (common/'chd/Disc.src.txt').write_text(MERGED)
            (common/'chd/Disc.chd').write_bytes(b'MComprHD')
            (common/'chd/Broken.chd').write_bytes(b'MComprHD')
            chdman = card/'chdman'
            chdman.write_text(f'#!{sys.executable}\n' + FAKE_CHDMAN)
            chdman.chmod(0o755)
            self.assertEqual(convert(common, str(chdman)), (2, 1))
            self.assertFalse((common/'chd/Broken').exists())
            self.assertEqual(sorted(p.name for p in (common/'chd').iterdir() if p.name.startswith('.')), [])
            # Idempotent: a second pass skips what is already converted.
            self.assertEqual(convert(common, str(chdman)), (0, 1))
            core = card/'Cores/GuardianSoul.PSXEarlyNativeR'
            core.mkdir(parents=True)
            (core/'data.json').write_text(json.dumps({'data': {'data_slots': [{'id': n} for n in range(100, 120)]}}))
            subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(card),
                            '--core', 'PSXEarlyNativeR'], check=True, capture_output=True)
            out = card/'Assets/playstation/GuardianSoul.PSXEarlyNativeR'
            disc = json.loads((out/'Disc.json').read_text())['instance']
            self.assertEqual(disc['data_path'], 'chd/Disc/')
            self.assertEqual([s['filename'] for s in disc['data_slots']],
                             ['Disc.cue', 'Disc (Track 01).bin', 'Disc (Track 02).bin', 'Disc (Track 03).bin'])
            self.assertEqual(json.loads((out/'Game.json').read_text())['instance']['data_path'], 'cue/Game/')


if __name__ == '__main__':
    unittest.main()
