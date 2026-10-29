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

    def test_first_game_file_removes_the_release_placeholder(self):
        with TemporaryDirectory() as tmp:
            card = Path(tmp); common = card/'Assets/playstation/common'; common.mkdir(parents=True)
            out = card/'Assets/playstation/GuardianSoul.PSXEarlyLC'; out.mkdir(parents=True)
            help_entry = out/'! How to add games (see README).json'; help_entry.write_text('{}')
            self.run_script(card)
            self.assertTrue(help_entry.exists())   # no games yet: keep it
            (common/'Ape.bin').write_bytes(sectors(1, 4))   # loose in common
            (common/'Ape.cue').write_text('FILE "Ape.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
            self.assertEqual(self.run_card(card), [{'id': 100, 'filename': 'Ape.cue'}, {'id': 101, 'filename': 'Ape.bin'}])
            self.assertFalse(help_entry.exists())

    def run_script(self, card, *extra):
        subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(card),
                        '--core', 'PSXEarlyLC', *extra], check=True, capture_output=True)

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
            # A cue-reader core (slot 0 takes .cue) reads a game JSON's .sbi
            # from slot 99 too: the JSON's cue has no path on the Pocket.
            # 0.5.4's launch slot listed .bin too; later ones don't.
            for launch in (['json', 'cue', 'bin', 'm3u'], ['json', 'cue', 'm3u']):
                reader = [{'id': 0, 'extensions': launch}, {'id': 100, 'extensions': ['cue', 'm3u']},
                          {'id': 101}, {'id': 99}]
                (core/'data.json').write_text(json.dumps({'data': {'data_slots': reader}}))
                self.assertIn({'id': 99, 'filename': 'Ape.sbi'}, self.run_card(card))
            (core/'data.json').write_text(json.dumps({'data': {'data_slots': reader[:3]}}))
            self.assertNotIn(99, [s['id'] for s in self.run_card(card)])



class TrackSizes(unittest.TestCase):
    def test_cue_only_game_file_gets_its_track_sizes(self):
        # A cue-reader core opens the files of a cue with more files than it
        # declares slots by path; with slot 97 the game file carries their
        # sizes (cue_mount.c tsz_word).
        with TemporaryDirectory() as tmp:
            card = Path(tmp); game = card/'Assets/playstation/common/Many'; game.mkdir(parents=True)
            cue = 'FILE "Many (Track 1).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
            (game/'Many (Track 1).bin').write_bytes(sectors(1, 40))
            for k in range(2, 7):
                (game/f'Many (Track {k}).bin').write_bytes(sectors(k, 150 + k))
                cue += f'FILE "Many (Track {k}).bin" BINARY\n  TRACK {k:02d} AUDIO\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n'
            (game/'Many.cue').write_text(cue)
            core = card/'Cores/GuardianSoul.PSXEarlyTS'; core.mkdir(parents=True)
            reader = [{'id': 0, 'extensions': ['json', 'cue', 'm3u']}, {'id': 100, 'extensions': ['cue', 'm3u']}] + [{'id': n} for n in range(101, 105)]
            out = card/'Assets/playstation/GuardianSoul.PSXEarlyTS/Many.json'
            for slots, want in ((reader + [{'id': 97}], True), (reader, False)):
                (game/'Many.tsz').unlink(missing_ok=True)
                (core/'data.json').write_text(json.dumps({'data': {'data_slots': slots}}))
                subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(card), '--core', 'PSXEarlyTS'],
                               check=True, capture_output=True)
                instance = json.loads(out.read_text())['instance']
                self.assertEqual(instance['data_path'], 'Many/')
                if not want:
                    self.assertEqual(instance['data_slots'], [{'id': 100, 'filename': 'Many.cue'}])
                    self.assertFalse((game/'Many.tsz').exists())
                    continue
                self.assertEqual(instance['data_slots'], [{'id': 100, 'filename': 'Many.cue'}, {'id': 97, 'filename': 'Many.tsz'}])
                words = [int.from_bytes((game/'Many.tsz').read_bytes()[i:i+4], 'little') for i in range(0, 36, 4)]
                self.assertEqual(words, [0x315A5354, len(cue.encode()), 6, 40, 152, 153, 154, 155, 156])


    def test_multi_disc_game_file_gets_disc_1_track_sizes(self):
        # A Disc-menu core groups "(Disc N)" cues into one game file (Street
        # Fighter Collection (USA) (Disc 1) (Rev 1), with a shared card): its
        # boot mount is disc 1's, so slot 97 holds disc 1's sizes.
        with TemporaryDirectory() as tmp:
            card = Path(tmp); stem = 'Fight (USA) (Disc 1) (Rev 1)'
            game = card/'Assets/playstation/common'/stem; game.mkdir(parents=True)
            cue = f'FILE "{stem} (Track 1).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
            (game/f'{stem} (Track 1).bin').write_bytes(sectors(1, 40))
            for k in range(2, 7):
                (game/f'{stem} (Track {k}).bin').write_bytes(sectors(k, 150 + k))
                cue += f'FILE "{stem} (Track {k}).bin" BINARY\n  TRACK {k:02d} AUDIO\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n'
            (game/f'{stem}.cue').write_text(cue)
            core = card/'Cores/GuardianSoul.PSXEarlyTM'; core.mkdir(parents=True)
            slots = ([{'id': 0, 'extensions': ['json', 'cue', 'm3u']}, {'id': 16}, {'id': 100, 'extensions': ['cue', 'm3u']}, {'id': 97}]
                     + [{'id': n} for n in range(101, 105)] + [{'id': 201}, {'id': 202}])
            (core/'data.json').write_text(json.dumps({'data': {'data_slots': slots}}))
            subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(card), '--core', 'PSXEarlyTM'],
                           check=True, capture_output=True)
            instance = json.loads((card/'Assets/playstation/GuardianSoul.PSXEarlyTM/Fight (USA) (Rev 1).json').read_text())['instance']
            self.assertEqual(instance['data_path'], '')
            self.assertEqual([s for s in instance['data_slots'] if s['id'] in (97, 100)],
                             [{'id': 100, 'filename': f'{stem}/{stem}.cue'}, {'id': 97, 'filename': f'{stem}/{stem}.tsz'}])
            self.assertEqual((game/f'{stem}.tsz').read_bytes()[8:12], (6).to_bytes(4, 'little'))


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


    def test_other_disc_namings_group(self):
        # Disc tags as other rippers write them: an untagged disc 1 with
        # "Disc2" (Star Wars Rebel Assault II, from a user's card), "CD1"/"CD2",
        # " - Disc 1"/" - Disc 2". Sequel numbers and lone titles stay apart.
        with TemporaryDirectory() as tmp:
            card = Path(tmp); common = card/'Assets/playstation/common'
            stems = {'Star Wars Rebel Assault II': 1, 'Star Wars Rebel Assault II Disc2': 2,
                     'Riven CD1': 1, 'Riven CD2': 2, 'Myst - Disc 1 (USA)': 1, 'Myst - Disc 2 (USA)': 2,
                     'Tekken 2': 1, 'Tekken 3': 1, 'Discworld II': 1}
            for stem, n in stems.items():
                game = common/stem; game.mkdir(parents=True)
                (game/f'{stem}.bin').write_bytes(sectors(n, 4))
                (game/f'{stem}.cue').write_text(f'FILE "{stem}.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
            core = card/'Cores/GuardianSoul.PSXEarlyMD'; core.mkdir(parents=True)
            declared = [{'id': 0}, {'id': 16}, {'id': 100}] + [{'id': 101+k} for k in range(19)] + [{'id': 201}, {'id': 202}]
            (core/'data.json').write_text(json.dumps({'data': {'data_slots': declared}}))
            subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(card), '--core', 'PSXEarlyMD'],
                           check=True, capture_output=True)
            out = card/'Assets/playstation/GuardianSoul.PSXEarlyMD'
            self.assertEqual(sorted(p.name for p in out.iterdir()),
                             ['Discworld II.json', 'Myst (USA).json', 'Riven.json', 'Star Wars Rebel Assault II.json', 'Tekken 2.json', 'Tekken 3.json'])
            ra2 = json.loads((out/'Star Wars Rebel Assault II.json').read_text())['instance']
            self.assertEqual(ra2['data_slots'], [{'id': 100, 'filename': 'Star Wars Rebel Assault II/Star Wars Rebel Assault II.cue'},
                                                 {'id': 101, 'filename': 'Star Wars Rebel Assault II/Star Wars Rebel Assault II.bin'},
                                                 {'id': 201, 'filename': 'Star Wars Rebel Assault II Disc2/Star Wars Rebel Assault II Disc2.bin'},
                                                 {'id': 16, 'filename': 'Star Wars Rebel Assault II.sav'}])
            riven = json.loads((out/'Riven.json').read_text())['instance']['data_slots']
            self.assertEqual([s['id'] for s in riven], [100, 101, 201, 16])
            self.assertIn({'id': 16, 'filename': 'Riven.sav'}, riven)
            myst = json.loads((out/'Myst (USA).json').read_text())['instance']['data_slots']
            self.assertIn({'id': 16, 'filename': 'Myst (USA).sav'}, myst)
            self.assertEqual([s['id'] for s in json.loads((out/'Tekken 2.json').read_text())['instance']['data_slots']], [100, 101])

    def test_folder_discs(self):
        # A subfolder of 2-4 cues without disc tags, named as one game's discs,
        # is one game named after the folder; other folders stay apart.
        from tests.test_sd_card_page import folder_discs
        with TemporaryDirectory() as tmp:
            card = Path(tmp); folder_discs(card/'Assets/playstation/common')
            core = card/'Cores/GuardianSoul.PSXEarlyMD'; core.mkdir(parents=True)
            declared = ([{'id': 0}, {'id': 16}, {'id': 100}] + [{'id': 101+k} for k in range(19)]
                        + [{'id': 100*n + k} for n in (2, 3, 4) for k in (1, 2, 3)])
            (core/'data.json').write_text(json.dumps({'data': {'data_slots': declared}}))
            run = subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(card), '--core', 'PSXEarlyMD'],
                                 check=True, capture_output=True, text=True).stdout
            out = card/'Assets/playstation/GuardianSoul.PSXEarlyMD'
            self.assertEqual(sorted(p.name for p in out.iterdir()),
                             ['Alone.json', 'Alpha 1.json', 'Beta 2.json', 'Crash.json', 'Gran Turismo 2 (USA).json', 'Lost.json', 'Riven.json', 'Spread.json', 'Spyro 2.json', 'Tekken 2.json',
                              'Tekken 3.json', 'cd1.json', 'cd2.json', 'cd3.json', 'cd4.json', 'cd5.json', 'other 2.json'])
            riven = json.loads((out/'Riven.json').read_text())['instance']
            self.assertEqual(riven['data_slots'], [{'id': 100, 'filename': 'Riven/disc1.cue'}, {'id': 101, 'filename': 'Riven/disc1 (Track 1).bin'},
                                                   {'id': 201, 'filename': 'Riven/disc2 (Track 1).bin'}, {'id': 202, 'filename': 'Riven/disc2 (Track 2).bin'},
                                                   {'id': 203, 'filename': 'Riven/disc2 (Track 3).bin'}, {'id': 16, 'filename': 'Riven.sav'}])
            self.assertEqual([s['id'] for s in json.loads((out/'Lost.json').read_text())['instance']['data_slots']], [100, 101, 201, 301, 16])
            self.assertIn('Riven: 2 discs in one folder, grouped as one game', run)
            self.assertIn('skip Lonely: disc 1 is missing (found discs 2): put it in Assets/playstation/common too', run)
            self.assertIn('warning: Spread: its discs are in different folders (A/Spread (Disc 1), B/Spread (Disc 2)); put them in one folder', run)

    def test_tags_one_disc_has_leave_the_name(self):
        # Gran Turismo 2's discs: "(Arcade Mode)" and "(Simulation Mode)" are
        # not the game's name. The shared card keeps its earlier name, and
        # the game file of the earlier name (with disc 1's tag) goes.
        with TemporaryDirectory() as tmp:
            card = Path(tmp); common = card/'Assets/playstation/common'
            stems = ('Gran Turismo 2 (USA) (Disc 1) (Arcade Mode)', 'Gran Turismo 2 (USA) (Disc 2) (Simulation Mode)')
            for n, stem in enumerate(stems, 1):
                game = common/stem; game.mkdir(parents=True)
                (game/f'{stem}.bin').write_bytes(sectors(n, 4))
                (game/f'{stem}.cue').write_text(f'FILE "{stem}.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
            core = card/'Cores/GuardianSoul.PSXEarlyMD'; core.mkdir(parents=True)
            declared = [{'id': 0}, {'id': 16}, {'id': 100}] + [{'id': 101+k} for k in range(19)] + [{'id': 201}]
            (core/'data.json').write_text(json.dumps({'data': {'data_slots': declared}}))
            out = card/'Assets/playstation/GuardianSoul.PSXEarlyMD'; out.mkdir(parents=True)
            (out/'Gran Turismo 2 (USA) (Arcade Mode).json').write_text('{}')   # from an earlier run
            subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(card), '--core', 'PSXEarlyMD'],
                           check=True, capture_output=True)
            self.assertEqual(sorted(p.name for p in out.iterdir()), ['Gran Turismo 2 (USA).json'])
            slots = json.loads((out/'Gran Turismo 2 (USA).json').read_text())['instance']['data_slots']
            self.assertIn({'id': 16, 'filename': 'Gran Turismo 2 (USA) (Arcade Mode).sav'}, slots)

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
            self.assertEqual(sorted(p.name for p in out.iterdir()), ['Game.json'])
            slots = json.loads((out/'Game.json').read_text())['instance']['data_slots']
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


class OneFile(unittest.TestCase):
    """--one-file-disc cores: one .bin plus a .trk track table per disc."""
    declared = [{'id': 0}, {'id': 100}, {'id': 101, 'extensions': ['bin']}, {'id': 102, 'extensions': ['trk']},
                {'id': 201, 'extensions': ['bin']}, {'id': 202, 'extensions': ['trk']}]

    def card(self, tmp):
        card = Path(tmp); core = card/'Cores/GuardianSoul.PSXEarlyOF'; core.mkdir(parents=True)
        (core/'data.json').write_text(json.dumps({'data': {'data_slots': self.declared}}))
        return card, card/'Assets/playstation/common', card/'Assets/playstation/GuardianSoul.PSXEarlyOF'

    def run_card(self, card):
        return subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(card), '--core', 'PSXEarlyOF'],
                              check=True, capture_output=True, text=True).stdout

    def words(self, path):
        data = path.read_bytes(); self.assertEqual(len(data), 1600)
        return [int.from_bytes(data[i:i+4], 'little') for i in range(0, 1600, 4)]

    def test_track_files_merge_and_match_the_multi_track_table(self):
        from scripts.psx_disc_host import multitrack_metadata_words
        with TemporaryDirectory() as tmp:
            card, common, out = self.card(tmp)
            game = common/'Rayman'; game.mkdir(parents=True)
            counts = [12] + [151 + k for k in range(50)]   # 51 tracks, as Rayman
            lines = []
            for k, n in enumerate(counts):
                name = f'Rayman (Track {k+1:02d}).bin'
                (game/name).write_bytes(sectors(k+1, n))
                lines += [f'FILE "{name}" BINARY', f'  TRACK {k+1:02d} {"MODE2/2352" if k == 0 else "AUDIO"}']
                lines += ['    INDEX 01 00:00:00'] if k == 0 else ['    INDEX 00 00:00:00', '    INDEX 01 00:02:00']
            (game/'Rayman.cue').write_text('\n'.join(lines) + '\n')
            self.assertIn('51 tracks, one file', self.run_card(card))
            image = game/'Rayman (one file).bin'
            self.assertEqual(image.read_bytes(), b''.join(sectors(k+1, n) for k, n in enumerate(counts)))
            self.assertEqual(self.words(image.with_suffix('.trk')), multitrack_metadata_words(counts))
            slots = json.loads((out/'Rayman.json').read_text())['instance']['data_slots']
            self.assertEqual(slots, [{'id': 100, 'filename': 'Rayman.cue'}, {'id': 101, 'filename': 'Rayman (one file).bin'},
                                     {'id': 102, 'filename': 'Rayman (one file).trk'}])
            mtime = image.stat().st_mtime_ns
            self.run_card(card)   # idempotent: the merged image is not rewritten
            self.assertEqual(image.stat().st_mtime_ns, mtime)

    def test_merged_image_used_in_place_with_its_own_pregaps(self):
        with TemporaryDirectory() as tmp:
            card, common, out = self.card(tmp)
            game = common/'Game'; game.mkdir(parents=True)
            (game/'Game.bin').write_bytes(sectors(1, 10) + sectors(2, 200) + sectors(3, 100))
            # Track 2: 150-sector pregap; track 3: 75 (1 s) stored, INDEX 01 at sector 285.
            (game/'Game.cue').write_text(MERGED.replace('    PREGAP 00:02:00\n    INDEX 01 00:02:60\n',
                                                        '    INDEX 00 00:02:60\n    INDEX 01 00:03:60\n'))
            self.run_card(card)
            self.assertFalse((game/'Game (one file).bin').exists())
            words = self.words(game/'Game.trk')
            self.assertEqual(words[:4], [0x0303, 460, 0x0006, 2 << 16])   # 310 sectors + 150; 460/75 = 6 s
            self.assertEqual(words[4:8], [0, 159, 0x0002, 0])
            self.assertEqual(words[8:12], [160, 359, (1 << 16) | 0x0004, 0])     # INDEX 01 LBA 310 = 4 s
            self.assertEqual(words[12:16], [360, 459, (1 << 16) | 0x0005, 0])    # INDEX 01 LBA 435 = 5 s
            self.assertEqual(words[16:], [0]*384)
            slots = json.loads((out/'Game.json').read_text())['instance']['data_slots']
            self.assertEqual([(s['id'], s['filename']) for s in slots], [(100, 'Game.cue'), (101, 'Game.bin'), (102, 'Game.trk')])

    def test_unsupported_layouts_are_skipped(self):
        with TemporaryDirectory() as tmp:
            card, common, out = self.card(tmp)
            game = common/'Game'; game.mkdir(parents=True)
            merged_image(game)   # track 3's PREGAP is not in the image
            (common/'Mixed').mkdir()
            (common/'Mixed/Mixed.bin').write_bytes(sectors(1, 400))
            (common/'Mixed/Mixed.cue').write_text('FILE "Mixed.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
                                                  '  TRACK 02 MODE2/2352\n    INDEX 00 00:01:00\n    INDEX 01 00:03:00\n')
            log = self.run_card(card)
            self.assertIn('PREGAP outside the image', log)
            self.assertIn('data after track 1', log)
            self.assertFalse(out.exists() and any(out.iterdir()))

    def test_discs_of_a_game_get_image_and_table_slots(self):
        with TemporaryDirectory() as tmp:
            card, common, out = self.card(tmp)
            for n in (1, 2):
                game = common/f'Game (Disc {n})'; game.mkdir(parents=True)
                (game/f'Game (Disc {n}).bin').write_bytes(sectors(n, 4))
                (game/f'Game (Disc {n}).cue').write_text(f'FILE "Game (Disc {n}).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
            self.run_card(card)
            slots = json.loads((out/'Game.json').read_text())['instance']['data_slots']
            self.assertEqual([(s['id'], s['filename']) for s in slots if s['id'] != 16],
                             [(100, 'Game (Disc 1)/Game (Disc 1).cue'), (101, 'Game (Disc 1)/Game (Disc 1).bin'),
                              (102, 'Game (Disc 1)/Game (Disc 1).trk'), (201, 'Game (Disc 2)/Game (Disc 2).bin'),
                              (202, 'Game (Disc 2)/Game (Disc 2).trk')])

    def test_sbi_mask_goes_into_the_table(self):
        with TemporaryDirectory() as tmp:
            card, common, out = self.card(tmp)
            game = common/'Ape'; game.mkdir(parents=True)
            (game/'Ape.bin').write_bytes(sectors(1, 4))
            (game/'Ape.cue').write_text('FILE "Ape.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
            entries = [bytes([3, 8, 5, 1]) + bytes(10), bytes([3, 8, 6, 1]) + bytes(10), bytes([3, 0x35, 0x42, 1]) + bytes(10)]
            (game/'Ape.sbi').write_bytes(b'SBI\0' + b''.join(entries))
            self.assertIn('LibCrypt Ape.sbi', self.run_card(card))
            self.assertEqual(self.words(game/'Ape.trk')[3], (2 << 16) | 0x8001)
            slots = json.loads((out/'Ape.json').read_text())['instance']['data_slots']
            self.assertEqual([s['id'] for s in slots], [100, 101, 102])
