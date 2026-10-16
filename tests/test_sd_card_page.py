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
    # Multi-disc: one folder per disc, release tags that differ between discs.
    for n, stem in ((1, 'Final Fantasy IX (Disc 1) (v1.1)'), (2, 'Final Fantasy IX (USA) (Disc 2) (Rev 1)'),
                    (3, 'Final Fantasy IX (USA) (Disc 3)')):
        one_track(common/'Final Fantasy IX'/stem, stem, n)
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
    core = card/'Cores'/CORE
    core.mkdir(parents=True)
    declared = ([{'id': 0}, {'id': 16}, {'id': 100}] + [{'id': 101+k} for k in range(19)]
                + [{'id': 100*n + k} for n in (2, 3, 4) for k in (1, 2)] + [{'id': 99}])
    (core/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': declared}}))
    (core/'core.json').write_text(json.dumps({'core': {'metadata': {'platform_ids': ['playstation']}}}))
    out = card/'Assets/playstation'/CORE
    out.mkdir(parents=True)
    (out/'Final Fantasy IX (USA) (Disc 2) (Rev 1).json').write_text('{}')   # from an earlier run
    saves = card/'Saves/playstation'/CORE
    saves.mkdir(parents=True)
    (saves/'Final Fantasy IX (Disc 1) (v1.1).sav').write_bytes(b'disc 1')
    (saves/'Final Fantasy IX (USA) (Disc 2) (Rev 1).sav').write_bytes(b'disc 2')
    os.utime(saves/'Final Fantasy IX (Disc 1) (v1.1).sav', (1, 1))
    (card/'Saves/playstation/common').mkdir(parents=True)
    (card/'Saves/playstation/common/Legend (USA).sav').write_bytes(b'kept')
    (saves/'Legend (USA) (Disc 1).sav').write_bytes(b'not copied')


def tree(card):
    return {p.relative_to(card).as_posix(): p.read_bytes() for p in sorted(card.rglob('*')) if p.is_file()}


@unittest.skipUnless(NODE, 'node not installed')
class SdCardPage(unittest.TestCase):
    def test_page_matches_the_script(self):
        with TemporaryDirectory() as tmp:
            script, page = Path(tmp)/'script', Path(tmp)/'page'
            make_card(script)
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
            self.assertIn('Final Fantasy IX (v1.1).json', names)
            self.assertIn('Game.json', names)
            self.assertNotIn('Final Fantasy IX (USA) (Disc 2) (Rev 1).json', names)
            ff9 = json.loads((out/'Final Fantasy IX (v1.1).json').read_text())['instance']['data_slots']
            self.assertEqual([s['id'] for s in ff9], [100, 101, 201, 301, 16])
            self.assertEqual((page/'Saves/playstation/common/Final Fantasy IX (v1.1).sav').read_bytes(), b'disc 2')
            self.assertEqual((page/'Saves/playstation/common/Legend (USA).sav').read_bytes(), b'kept')
            self.assertIn('\\u00e9', (out/'Pokémon Stadium.json').read_text())
            for skipped in ('Long Game', 'Busy', 'Gap', 'Broken/Broken.cue', 'Short/Short.cue'):
                self.assertTrue(any(line.startswith(f'skip {skipped}') for line in js.stdout.splitlines()), skipped)


if __name__ == '__main__':
    unittest.main()
