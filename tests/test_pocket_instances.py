from pathlib import Path
import json
import os
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from scripts.make_pocket_instances import SECTOR, check, convert, parse_cue, shared_card_name, split_cue

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



class LibCryptSlot(unittest.TestCase):
    def run_card(self, card, *extra):
        subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(card),
                        '--core', 'PSXEarlyLC', *extra], check=True, capture_output=True)
        return json.loads((card/'Assets/playstation/GuardianSoul.PSXEarlyLC/Ape.json').read_text())['instance']['data_slots']

    def test_sbi_goes_to_slot_99_when_the_core_declares_it(self):
        with TemporaryDirectory() as tmp:
            card = Path(tmp); game = card/'Assets/playstation/common/Ape'; game.mkdir(parents=True)
            (game/'Ape.bin').write_bytes(sectors(1, 4))
            (game/'Ape.cue').write_text('FILE "Ape.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
            (game/'Ape.sbi').write_bytes(b'SBI\0' + bytes([3, 8, 5, 1]) + bytes(10))
            core = card/'Cores/GuardianSoul.PSXEarlyLC'; core.mkdir(parents=True)
            declared = [{'id': 0}, {'id': 100}, {'id': 101}, {'id': 99}]
            (core/'data.json').write_text(json.dumps({'data': {'data_slots': declared}}))
            self.assertIn({'id': 99, 'filename': 'Ape.sbi'}, self.run_card(card))
            self.assertNotIn(99, [s['id'] for s in self.run_card(card, '--no-libcrypt')])
            (core/'data.json').write_text(json.dumps({'data': {'data_slots': declared[:3]}}))
            self.assertNotIn(99, [s['id'] for s in self.run_card(card)])



class MultiDiscGroup(unittest.TestCase):
    def test_discs_of_a_game_share_one_instance(self):
        with TemporaryDirectory() as tmp:
            card = Path(tmp); common = card/'Assets/playstation/common'
            for n in (1, 2):
                game = common/f'Game (Disc {n})'; game.mkdir(parents=True)
                (game/f'Game (Disc {n}).bin').write_bytes(sectors(n, 4))
                (game/f'Game (Disc {n}).cue').write_text(f'FILE "Game (Disc {n}).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
            core = card/'Cores/GuardianSoul.PSXEarlyMD'; core.mkdir(parents=True)
            declared = [{'id': 0}, {'id': 100}] + [{'id': 101+k} for k in range(19)] + [{'id': 201}, {'id': 202}]
            (core/'data.json').write_text(json.dumps({'data': {'data_slots': declared}}))
            out = card/'Assets/playstation/GuardianSoul.PSXEarlyMD'; out.mkdir(parents=True)
            (out/'Game (Disc 2).json').write_text('{}')   # from an earlier run
            subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(card), '--core', 'PSXEarlyMD'],
                           check=True, capture_output=True)
            self.assertEqual(sorted(p.name for p in out.iterdir()), ['Game.json'])
            instance = json.loads((out/'Game.json').read_text())['instance']
            self.assertEqual(instance['data_path'], '')
            self.assertEqual(instance['data_slots'], [{'id': 100, 'filename': 'Game (Disc 1)/Game (Disc 1).cue'},
                                                      {'id': 101, 'filename': 'Game (Disc 1)/Game (Disc 1).bin'},
                                                      {'id': 201, 'filename': 'Game (Disc 2)/Game (Disc 2).bin'}])


    def test_release_tags_do_not_split_a_game(self):
        with TemporaryDirectory() as tmp:
            card = Path(tmp); common = card/'Assets/playstation/common'
            for n, stem in ((1, 'Game (Disc 1) (v1.1)'), (2, 'Game (USA) (Disc 2) (Rev 1)')):
                game = common/stem; game.mkdir(parents=True)
                (game/f'{stem}.bin').write_bytes(sectors(n, 4))
                (game/f'{stem}.cue').write_text(f'FILE "{stem}.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
            core = card/'Cores/GuardianSoul.PSXEarlyMD'; core.mkdir(parents=True)
            declared = [{'id': 0}, {'id': 100}] + [{'id': 101+k} for k in range(19)] + [{'id': 201}]
            (core/'data.json').write_text(json.dumps({'data': {'data_slots': declared}}))
            subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(card), '--core', 'PSXEarlyMD'],
                           check=True, capture_output=True)
            out = card/'Assets/playstation/GuardianSoul.PSXEarlyMD'
            self.assertEqual(sorted(p.name for p in out.iterdir()), ['Game (v1.1).json'])
            slots = json.loads((out/'Game (v1.1).json').read_text())['instance']['data_slots']
            self.assertEqual([s['id'] for s in slots if s['id'] != 16], [100, 101, 201])


if __name__ == '__main__':
    unittest.main()


class SharedCard(unittest.TestCase):
    def test_names(self):
        self.assertEqual(shared_card_name('Final Fantasy VII (USA) (Disc 2)'), 'Final Fantasy VII (USA).sav')
        self.assertEqual(shared_card_name('Metal Gear Solid (USA) (Disc 1) (Rev 1)'), 'Metal Gear Solid (USA) (Rev 1).sav')
        self.assertIsNone(shared_card_name('Tekken 3 (USA)'))

    def test_discs_share_one_card(self):
        with TemporaryDirectory() as tmp:
            card = Path(tmp)
            common = card/'Assets/playstation/common'
            for n in (1, 2):
                d = common/f'Game (Disc {n})'
                d.mkdir(parents=True)
                (d/f'Game (Disc {n}).bin').write_bytes(sectors(n, 4))
                (d/f'Game (Disc {n}).cue').write_text(f'FILE "Game (Disc {n}).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
            core = card/'Cores/GuardianSoul.T'
            core.mkdir(parents=True)
            (core/'data.json').write_text(json.dumps({'data': {'data_slots': [{'id': 16}] + [{'id': n} for n in range(100, 110)]}}))
            saves = card/'Saves/playstation/GuardianSoul.T'
            saves.mkdir(parents=True)
            (saves/'Game (Disc 1).sav').write_bytes(b'old')
            (saves/'Game (Disc 2).sav').write_bytes(b'new')
            os.utime(saves/'Game (Disc 1).sav', (1, 1))
            run = lambda: subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(card), '--core', 'T'],
                                         check=True, capture_output=True)
            run()
            out = card/'Assets/playstation/GuardianSoul.T'
            disc = json.loads((out/'Game (Disc 2).json').read_text())['instance']
            self.assertEqual(disc['data_path'], '')
            self.assertEqual(disc['data_slots'], [{'id': 100, 'filename': 'Game (Disc 2)/Game (Disc 2).cue'},
                                                  {'id': 101, 'filename': 'Game (Disc 2)/Game (Disc 2).bin'},
                                                  {'id': 16, 'filename': 'Game.sav'}])
            shared = card/'Saves/playstation/common/Game.sav'
            self.assertEqual(shared.read_bytes(), b'new')
            # A second run never overwrites the shared card.
            shared.write_bytes(b'played')
            run()
            self.assertEqual(shared.read_bytes(), b'played')
