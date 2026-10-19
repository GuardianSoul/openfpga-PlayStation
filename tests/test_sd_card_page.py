"""The SD card helper page (docs/instances.js) writes the same game files,
shared cards and split tracks as scripts/make_pocket_instances.py."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from tests.test_pocket_instances import MERGED, sectors

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
CORE = 'GuardianSoul.PlayStation'


def one_track(folder, stem, tag=1, extra=''):
    folder.mkdir(parents=True, exist_ok=True)
    (folder/f'{stem}.bin').write_bytes(sectors(tag, 4))
    (folder/f'{stem}.cue').write_text(f'FILE "{stem}.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n' + extra)


def with_audio(folder, stem, audio):
    folder.mkdir(parents=True, exist_ok=True)
    lines = [f'FILE "{stem} (Track 1).bin" BINARY', '  TRACK 01 MODE2/2352', '    INDEX 01 00:00:00']
    (folder/f'{stem} (Track 1).bin').write_bytes(sectors(1, 4))
    for k in range(2, audio + 2):
        (folder/f'{stem} (Track {k}).bin').write_bytes(sectors(k, 160))
        lines += [f'FILE "{stem} (Track {k}).bin" BINARY', f'  TRACK {k:02d} AUDIO', '    INDEX 00 00:00:00', '    INDEX 01 00:02:00']
    (folder/f'{stem}.cue').write_text('\r\n'.join(lines) + '\r\n')


def make_card(card):
    common = card/'Assets/playstation/common'
    one_track(common/'Tekken 3 (USA)', 'Tekken 3 (USA)')
    one_track(common, 'Loose (Europe)')
    one_track(common/'Pokémon Stadium', 'Pokémon Stadium')
    with_audio(common/'Wipeout (Europe)', 'Wipeout (Europe)', 3)
    one_track(common/'Ape Escape (Europe)', 'Ape Escape (Europe)')
    (common/'Ape Escape (Europe)/Ape Escape (Europe).sbi').write_bytes(b'SBI\0' + bytes(14))
    (common/'Ape Escape (Europe)/._Ape Escape (Europe).cue').write_bytes(b'\0\5\26\7 Mac metadata')
    # Multi-disc with release tags that differ between discs, named as on a real card.
    for n, stem in ((1, 'Final Fantasy IX (Disc 1) (v1.1)'), (2, 'Final Fantasy IX (Disc 2) (v1.1)'),
                    (3, 'Final Fantasy IX (Disc 3) (v1.1)'), (4, 'Final Fantasy IX (USA) (Disc 4) (Rev 1)')):
        one_track(common/'Final Fantasy IX (USA)', stem, n)
    for n in (1, 2):
        with_audio(common/f'Legend (Disc {n})', f'Legend (USA) (Disc {n})', 1 if n == 2 else 4)
    for n in range(1, 6):
        one_track(common/'Long Game', f'Long Game (Disc {n})', n)
    one_track(common/'Busy (Disc 1)', 'Busy (Disc 1)')
    with_audio(common/'Busy (Disc 2)', 'Busy (Disc 2)', 3)
    one_track(common/'Gap', 'Gap (Disc 1)')
    one_track(common/'Gap', 'Gap (Disc 3)')
    # Merged image (split with --convert) and a broken cue.
    (common/'Merged').mkdir()
    (common/'Merged/Game.bin').write_bytes(sectors(1, 10) + sectors(2, 200) + sectors(3, 30))
    (common/'Merged/Game.cue').write_text(MERGED)
    (common/'Broken').mkdir()
    (common/'Broken/Broken.cue').write_text('INDEX 01 00:00:00\n')
    (common/'Short').mkdir()
    (common/'Short/Short.bin').write_bytes(b'x' * 100)
    (common/'Short/Short.cue').write_text('﻿FILE "Short.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    # A .bin that came without a .cue gets a one-track cue; a track file without its cue doesn't.
    (common/'Bin Only').mkdir()
    (common/'Bin Only/Bin Only (USA).bin').write_bytes(sectors(1, 4))
    (common/'Lost Cue').mkdir()
    (common/'Lost Cue/Lost (USA) (Track 1).bin').write_bytes(sectors(1, 4))
    core = card/'Cores'/CORE
    core.mkdir(parents=True)
    declared = ([{'id': 0}, {'id': 16}, {'id': 100}] + [{'id': 101+k} for k in range(19)]
                + [{'id': 100*n + k} for n in (2, 3, 4) for k in (1, 2)] + [{'id': 99}])
    (core/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': declared}}))
    (core/'core.json').write_text(json.dumps({'core': {'metadata': {'platform_ids': ['playstation']}}}))
    out = card/'Assets/playstation'/CORE
    out.mkdir(parents=True)
    (out/'Final Fantasy IX (USA) (Disc 4) (Rev 1).json').write_text('{}')   # from an earlier run
    saves = card/'Saves/playstation'/CORE
    saves.mkdir(parents=True)
    (saves/'Final Fantasy IX (Disc 1) (v1.1).sav').write_bytes(b'disc 1')
    (saves/'Final Fantasy IX (USA) (Disc 4) (Rev 1).sav').write_bytes(b'disc 4')
    os.utime(saves/'Final Fantasy IX (Disc 1) (v1.1).sav', (1, 1))
    (card/'Saves/playstation/common').mkdir(parents=True)
    (card/'Saves/playstation/common/Legend (USA).sav').write_bytes(b'kept')
    (saves/'Legend (USA) (Disc 1).sav').write_bytes(b'not copied')


# A --cue-mount core's data.json: the launch slot takes .cue (0.5.3 and 0.5.4
# listed .bin there too), the core opens track files (103) and other discs'
# cues (104) itself.
CUE_READER = ([{'id': 0, 'extensions': ['json', 'cue', 'm3u']}, {'id': 16}, {'id': 100, 'extensions': ['cue']},
               {'id': 101, 'extensions': ['cue', 'bin', 'm3u']}, {'id': 102, 'extensions': ['trk']},
               {'id': 103, 'extensions': ['bin']}, {'id': 104, 'extensions': ['cue']}]
              + [{'id': 100*n + k, 'extensions': ['bin' if k == 1 else 'trk']} for n in (2, 3, 4) for k in (1, 2)])


def cue_reader_card(card):
    make_card(card)
    (card/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': CUE_READER}}))


def tree(card):
    return {p.relative_to(card).as_posix(): p.read_bytes() for p in sorted(card.rglob('*')) if p.is_file()}


@unittest.skipUnless(NODE, 'node not installed')
class SdCardPage(unittest.TestCase):
    def test_page_matches_the_script(self):
        with TemporaryDirectory() as tmp:
            script, page = Path(tmp)/'script', Path(tmp)/'page'
            make_card(script)
            help_entry = script/'Assets/playstation'/CORE/'! How to add games (see README).json'
            help_entry.parent.mkdir(parents=True, exist_ok=True); help_entry.write_text('{}')
            shutil.copytree(script, page)
            py = subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(script),
                                 '--core', 'PlayStation', '--convert'], check=True, capture_output=True, text=True)
            js = subprocess.run([NODE, str(ROOT/'tests/sd_card_page.cjs'), str(page), '--core', CORE, '--convert'],
                                check=True, capture_output=True, text=True)
            self.assertEqual(py.stdout.splitlines()[:-1], js.stdout.splitlines()[:-1])
            self.assertEqual(tree(script).keys(), tree(page).keys())
            for name, data in tree(script).items():
                self.assertEqual(tree(page)[name], data, name)
            out = page/'Assets/playstation'/CORE
            names = sorted(p.name for p in out.iterdir())
            self.assertNotIn(help_entry.name, names)   # the release placeholder goes
            self.assertIn('Final Fantasy IX (v1.1).json', names)
            self.assertIn('Game.json', names)
            self.assertNotIn('Final Fantasy IX (USA) (Disc 4) (Rev 1).json', names)
            ff9 = json.loads((out/'Final Fantasy IX (v1.1).json').read_text())['instance']['data_slots']
            self.assertEqual([s['id'] for s in ff9], [100, 101, 201, 301, 401, 16])
            self.assertEqual((page/'Saves/playstation/common/Final Fantasy IX (v1.1).sav').read_bytes(), b'disc 4')
            self.assertEqual((page/'Saves/playstation/common/Legend (USA).sav').read_bytes(), b'kept')
            self.assertIn('\\u00e9', (out/'Pokémon Stadium.json').read_text())
            for skipped in ('Long Game', 'Busy', 'Gap', 'Broken/Broken.cue', 'Short/Short.cue'):
                self.assertTrue(any(line.startswith(f'skip {skipped}') for line in js.stdout.splitlines()), skipped)

    def test_page_can_replace_merged_images(self):
        with TemporaryDirectory() as tmp:
            card = Path(tmp)
            make_card(card)
            merged = card/'Assets/playstation/common/Merged'
            subprocess.run([NODE, str(ROOT/'tests/sd_card_page.cjs'), str(card), '--core', CORE, '--convert', '--remove-originals'],
                           check=True, capture_output=True, text=True)
            self.assertEqual(sorted(p.name for p in merged.iterdir()), ['Game'])
            self.assertEqual(sorted(p.name for p in (merged/'Game').iterdir()),
                             ['Game (Track 01).bin', 'Game (Track 02).bin', 'Game (Track 03).bin', 'Game.cue'])
            game = json.loads((card/'Assets/playstation'/CORE/'Game.json').read_text())['instance']
            self.assertEqual(game['data_path'], 'Merged/Game/')
            # A second run finds nothing left to split and keeps the game file.
            again = subprocess.run([NODE, str(ROOT/'tests/sd_card_page.cjs'), str(card), '--core', CORE, '--convert', '--remove-originals'],
                                   check=True, capture_output=True, text=True)
            self.assertIn('0 images converted', again.stdout)
            self.assertIn('wrote Game.json (3 tracks)', again.stdout)

    def test_page_keeps_the_original_on_a_full_card(self):
        # The split copy claims its size plus 256 MB first; on a full card it
        # fails before writing, the half-made folder goes and the original stays.
        with TemporaryDirectory() as tmp:
            card = Path(tmp)
            make_card(card)
            merged = card/'Assets/playstation/common/Merged'
            before = tree(card)
            used = sum(len(d) for d in before.values())
            js = subprocess.run([NODE, str(ROOT/'tests/sd_card_page.cjs'), str(card), '--core', CORE, '--convert',
                                 '--remove-originals', '--card-bytes', str(used + (100 << 20))],
                                check=True, capture_output=True, text=True)
            self.assertIn('skip Merged/Game.cue: not enough room on the SD card for Game (Track 01).bin', js.stdout)
            self.assertEqual(sorted(p.name for p in merged.iterdir()), ['Game.bin', 'Game.cue'])
            after = tree(card)
            self.assertEqual([n for n in before if after.get(n) != before[n] and not n.endswith('.json')], [])

    def test_playlist_core_names_the_playlist(self):
        # A cue-reader core whose slot 100 takes .m3u: FFIX gets one game file
        # naming its playlist, with the shared card; the core swaps from it.
        with TemporaryDirectory() as tmp:
            script, page = Path(tmp)/'script', Path(tmp)/'page'
            cue_reader_card(script)
            declared = [dict(s, extensions=['cue', 'm3u']) if s['id'] == 100 else s for s in CUE_READER]
            (script/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': declared}}))
            shutil.copytree(script, page)
            py = subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(script),
                                 '--core', 'PlayStation'], check=True, capture_output=True, text=True)
            js = subprocess.run([NODE, str(ROOT/'tests/sd_card_page.cjs'), str(page), '--core', CORE],
                                check=True, capture_output=True, text=True)
            self.assertEqual(py.stdout.splitlines()[:-1], js.stdout.splitlines()[:-1])
            self.assertEqual(tree(script), tree(page))
            self.assertIn('wrote Final Fantasy IX (v1.1).json (4 discs, playlist Final Fantasy IX (USA)/Final Fantasy IX (v1.1).m3u)', js.stdout)
            out = page/'Assets/playstation'/CORE
            ff9 = json.loads((out/'Final Fantasy IX (v1.1).json').read_text())['instance']
            self.assertEqual(ff9, {'magic': 'APF_VER_1', 'data_path': '',
                                   'data_slots': [{'id': 100, 'filename': 'Final Fantasy IX (USA)/Final Fantasy IX (v1.1).m3u'},
                                                  {'id': 101, 'filename': 'Final Fantasy IX (USA)/Final Fantasy IX (Disc 1) (v1.1).bin'},
                                                  {'id': 16, 'filename': 'Final Fantasy IX (v1.1).sav'}]})
            self.assertFalse((out/'Final Fantasy IX (USA) (Disc 4) (Rev 1).json').exists())   # per-disc file from an earlier run

    def test_one_file_core_matches_the_script(self):
        # --one-file-disc core: merged images used as they are, per-track
        # files merged into "(one file).bin", a .trk table per disc.
        with TemporaryDirectory() as tmp:
            script, page = Path(tmp)/'script', Path(tmp)/'page'
            make_card(script)
            common = script/'Assets/playstation/common'
            with_audio(common/'Rayman', 'Rayman (USA)', 50)
            (common/'Plain').mkdir()
            (common/'Plain/Game.bin').write_bytes(sectors(1, 10) + sectors(2, 200) + sectors(3, 100))
            (common/'Plain/Plain.cue').write_text(MERGED.replace('    PREGAP 00:02:00\n    INDEX 01 00:02:60\n',
                                                                 '    INDEX 00 00:02:60\n    INDEX 01 00:03:60\n'))
            (common/'Ape Escape (Europe)/Ape Escape (Europe).sbi').write_bytes(b'SBI\0' + bytes([3, 8, 5, 1]) + bytes(10))
            declared = ([{'id': 0}, {'id': 16}, {'id': 100}]
                        + [{'id': 100*n + k, 'extensions': ['bin' if k == 1 else 'trk']} for n in (1, 2, 3, 4) for k in (1, 2)])
            (script/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': declared}}))
            shutil.copytree(script, page)
            py = subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(script),
                                 '--core', 'PlayStation', '--convert'], check=True, capture_output=True, text=True)
            js = subprocess.run([NODE, str(ROOT/'tests/sd_card_page.cjs'), str(page), '--core', CORE, '--convert'],
                                check=True, capture_output=True, text=True)
            self.assertEqual(py.stdout.splitlines()[:-1], js.stdout.splitlines()[:-1])
            self.assertEqual(tree(script).keys(), tree(page).keys())
            for name, data in tree(script).items():
                self.assertEqual(tree(page)[name], data, name)
            self.assertIn('wrote Rayman (USA).json (51 tracks, one file)', js.stdout)
            self.assertIn('wrote Plain.json (3 tracks, one file)', js.stdout)
            self.assertIn('LibCrypt Ape Escape (Europe).sbi', js.stdout)
            self.assertTrue((page/'Assets/playstation/common/Rayman/Rayman (USA) (one file).trk').is_file())
            self.assertFalse((page/'Assets/playstation/common/Merged/Game').exists())   # nothing split

    def test_cheat_slot_names_the_cht(self):
        # A core declaring slot 98 (--cheats): <cue name>.cht, or the game's name without its disc tag.
        with TemporaryDirectory() as tmp:
            script, page = Path(tmp)/'script', Path(tmp)/'page'
            cue_reader_card(script)
            (script/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': CUE_READER + [{'id': 98}, {'id': 99}]}}))
            common = script/'Assets/playstation/common'
            (common/'Ape Escape (Europe)/Ape Escape (Europe).cht').write_text('80012345 0063\n')
            (common/'Legend (Disc 1)/Legend (USA).cht').write_text('80012345 0063\n')
            shutil.copytree(script, page)
            py = subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(script),
                                 '--core', 'PlayStation'], check=True, capture_output=True, text=True)
            js = subprocess.run([NODE, str(ROOT/'tests/sd_card_page.cjs'), str(page), '--core', CORE],
                                check=True, capture_output=True, text=True)
            self.assertEqual(py.stdout.splitlines()[:-1], js.stdout.splitlines()[:-1])
            self.assertEqual(tree(script), tree(page))
            out = page/'Assets/playstation'/CORE
            self.assertIn('wrote Ape Escape (Europe).json (1 track, cue, LibCrypt Ape Escape (Europe).sbi, cheats Ape Escape (Europe).cht)', js.stdout)
            ape = json.loads((out/'Ape Escape (Europe).json').read_text())['instance']['data_slots']
            self.assertIn({'id': 98, 'filename': 'Ape Escape (Europe).cht'}, ape)
            legend = json.loads((out/'Legend (USA).json').read_text())['instance']['data_slots']
            self.assertIn({'id': 98, 'filename': 'Legend (Disc 1)/Legend (USA).cht'}, legend)

    def test_cue_reader_core_matches_the_script(self):
        # --cue-mount core: game files name the cue and disc 1's files; nothing is joined.
        with TemporaryDirectory() as tmp:
            script, page = Path(tmp)/'script', Path(tmp)/'page'
            cue_reader_card(script)
            shutil.copytree(script, page)
            before = set(tree(script))
            py = subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(script),
                                 '--core', 'PlayStation', '--convert'], check=True, capture_output=True, text=True)
            js = subprocess.run([NODE, str(ROOT/'tests/sd_card_page.cjs'), str(page), '--core', CORE, '--convert'],
                                check=True, capture_output=True, text=True)
            self.assertEqual(py.stdout.splitlines()[:-1], js.stdout.splitlines()[:-1])
            self.assertEqual(tree(script).keys(), tree(page).keys())
            for name, data in tree(script).items():
                self.assertEqual(tree(page)[name], data, name)
            added = set(tree(page)) - before
            self.assertEqual([n for n in added if not n.endswith(('.json', '.sav', '.m3u'))],
                             ['Assets/playstation/common/Bin Only/Bin Only (USA).cue'])   # no .bin, no .trk
            self.assertEqual((page/'Assets/playstation/common/Bin Only/Bin Only (USA).cue').read_text(),
                             'FILE "Bin Only (USA).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
            self.assertIn('skip Lost Cue/Lost (USA) (Track 1).bin: a track file whose .cue is missing', js.stdout)
            self.assertIn('wrote Bin Only (USA).json (1 track, cue)', js.stdout)
            out = page/'Assets/playstation'/CORE
            legend = json.loads((out/'Legend (USA).json').read_text())['instance']
            # Five track files, but the core declares only 101-104: the game
            # file names only the cue (a slot it doesn't declare is an
            # instance error on the Pocket).
            self.assertEqual(legend['data_slots'], [{'id': 100, 'filename': 'Legend (Disc 1)/Legend (USA) (Disc 1).cue'},
                                                    {'id': 16, 'filename': 'Legend (USA).sav'}])
            self.assertIn('wrote Game.json (3 tracks, cue)', js.stdout)
            self.assertIn('wrote Ape Escape (Europe).json (1 track, cue, LibCrypt Ape Escape (Europe).sbi)', js.stdout)
            # FFIX's discs differ by more than their number: a playlist the core
            # file list can open, and (slot 100 takes no .m3u) one file per disc, one card.
            self.assertIn("Final Fantasy IX: the discs' names differ by more than their number: wrote the playlist "
                          "Final Fantasy IX (USA)/Final Fantasy IX (v1.1).m3u", js.stdout)
            self.assertEqual((page/'Assets/playstation/common/Final Fantasy IX (USA)/Final Fantasy IX (v1.1).m3u').read_text(),
                             'Final Fantasy IX (Disc 1) (v1.1).cue\nFinal Fantasy IX (Disc 2) (v1.1).cue\n'
                             'Final Fantasy IX (Disc 3) (v1.1).cue\nFinal Fantasy IX (USA) (Disc 4) (Rev 1).cue\n')
            for disc in ('Final Fantasy IX (Disc 1) (v1.1)', 'Final Fantasy IX (USA) (Disc 4) (Rev 1)'):
                slots = json.loads((out/f'{disc}.json').read_text())['instance']['data_slots']
                self.assertEqual(slots, [{'id': 100, 'filename': f'Final Fantasy IX (USA)/{disc}.cue'},
                                         {'id': 101, 'filename': f'Final Fantasy IX (USA)/{disc}.bin'},
                                         {'id': 16, 'filename': 'Final Fantasy IX (v1.1).sav'}])


if __name__ == '__main__':
    unittest.main()
