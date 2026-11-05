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

from tests.test_pocket_instances import MERGED, SECTOR, sectors

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
CORE = 'GuardianSoul.PlayStation'


def one_track(folder, stem, tag=1, extra=''):
    folder.mkdir(parents=True, exist_ok=True)
    (folder/f'{stem}.bin').write_bytes(sectors(tag, 4))
    (folder/f'{stem}.cue').write_text(f'FILE "{stem}.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n' + extra)


def index01_only_twin(common):
    """A redump disc (data + 2 audio tracks, INDEX 00/01) and the same disc as
    one merged .bin whose cue gives the audio tracks INDEX 01 only (as
    ISOBuster writes them): their one-file track tables must match."""
    with_audio(common/'Twin', 'Twin (USA)', 2)
    (common/'Twin Merged').mkdir()
    (common/'Twin Merged/Twin Merged.bin').write_bytes(
        b''.join((common/f'Twin/Twin (USA) (Track {k}).bin').read_bytes() for k in (1, 2, 3)))
    (common/'Twin Merged/Twin Merged.cue').write_text(
        'FILE "Twin Merged.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
        '  TRACK 02 AUDIO\n    INDEX 01 00:02:04\n  TRACK 03 AUDIO\n    INDEX 01 00:04:14\n')
    # And split at each INDEX 01 with INDEX 01 only (EAC's "gaps appended
    # to previous track"): each audio track's gap ends the file before it.
    (common/'Twin Split').mkdir()
    image = (common/'Twin Merged/Twin Merged.bin').read_bytes()
    lines = []
    for k, (a, b) in enumerate(((0, 154), (154, 314), (314, 324)), 1):
        (common/f'Twin Split/Twin Split (Track {k}).bin').write_bytes(image[a*SECTOR:b*SECTOR])
        lines += [f'FILE "Twin Split (Track {k}).bin" BINARY', f'  TRACK 0{k} {"AUDIO" if k > 1 else "MODE2/2352"}', '    INDEX 01 00:00:00']
    (common/'Twin Split/Twin Split.cue').write_text('\n'.join(lines) + '\n')


def twin_tables(common):
    return [next((common/f).glob('*.trk')).read_bytes() for f in ('Twin', 'Twin Merged', 'Twin Split')]


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
    # Cues whose FILE names differ from the files: another case, a .bin
    # renamed along with its one-file cue, track files renamed keeping their
    # "(Track n)". A file that is really gone is still skipped.
    one_track(common/'Case', 'Case Game (USA)')
    (common/'Case/Case Game (USA).cue').unlink()
    (common/'Case/Case.cue').write_text('FILE "CASE GAME (usa).BIN" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    one_track(common/'Renamed', 'Renamed (USA)')
    (common/'Renamed/Renamed (USA).bin').rename(common/'Renamed/Renamed.bin')
    (common/'Renamed/Renamed (USA).cue').rename(common/'Renamed/Renamed.cue')
    with_audio(common/'Tracks Renamed', 'Tracks Renamed (USA)', 2)
    for k in (1, 2, 3):
        (common/f'Tracks Renamed/Tracks Renamed (USA) (Track {k}).bin').rename(common/f'Tracks Renamed/TR (Track 0{k}).bin')
    # Renamed tracks next to a .bin of another game: not matched by "(Track n)".
    with_audio(common/'Stray', 'Stray (USA)', 1)
    for k in (1, 2):
        (common/f'Stray/Stray (USA) (Track {k}).bin').rename(common/f'Stray/ST (Track {k}).bin')
    (common/'Stray/Other (Track 3).bin').write_bytes(sectors(3, 160))
    with_audio(common/'Gone', 'Gone (USA)', 1)
    (common/'Gone/Gone (USA) (Track 2).bin').unlink()
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
    os.utime(saves/'Final Fantasy IX (Disc 1) (v1.1).sav', (400000000, 400000000))  # 1982: exFAT starts at 1980
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


def cue_variants_card(card):
    """A cue-reader card with the cue and image variants other tools write
    (2026-11-04 audit), and VARIANT_LINES: what every tool must say."""
    cue_reader_card(card)
    common = card/'Assets/playstation/common'
    def put(rel, data):
        (common/rel).parent.mkdir(parents=True, exist_ok=True)
        (common/rel).write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))
    # Lower case keywords and CR-only line ends (the core reads them).
    put('Lower/Lower.bin', sectors(1, 300))
    put('Lower/Lower.cue', 'file "Lower.bin" binary\r  track 01 mode2/2352\r    index 01 00:00:00\r'
                           '  track 02 audio\r    index 00 00:02:00\r    index 01 00:03:00\r')
    # CDRWin/EAC metadata lines, FLAGS and ISRC.
    put('Meta/Meta.bin', sectors(2, 300))
    put('Meta/Meta.cue', 'REM GENRE Game\nCATALOG 0000000000000\nPERFORMER "x"\nTITLE "Meta"\nFILE "Meta.bin" BINARY\n'
                         '  TRACK 01 MODE2/2352\n    FLAGS DCP\n    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n'
                         '    ISRC ABCDE1234567\n    INDEX 00 00:02:00\n    INDEX 01 00:03:00\n')
    # A FILE line without its type (hand-written).
    put('No Type/No Type.bin', sectors(3, 4))
    put('No Type/No Type.cue', 'FILE "No Type.bin"\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    # Track types the core can't read: told, not given a game file (this
    # .bin's size is whole 2352-byte sectors too).
    put('M2336/M2336.bin', bytes(2336*SECTOR))
    put('M2336/M2336.cue', 'FILE "M2336.bin" BINARY\n  TRACK 01 MODE2/2336\n    INDEX 01 00:00:00\n')
    put('Cooked/Cooked.iso', bytes(2048*300))
    put('Cooked/Cooked.cue', 'FILE "Cooked.iso" BINARY\n  TRACK 01 MODE1/2048\n    INDEX 01 00:00:00\n')
    # An ECM-compressed .bin next to its cue, formats the core can't read
    # and archives: each gets a line saying what to do.
    put('Ecm/Ecm.bin.ecm', b'ECM\0' + bytes(100))
    put('Ecm/Ecm.cue', 'FILE "Ecm.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    put('Loose.bin.ecm', b'ECM\0' + bytes(100))
    put('Iso Only/Iso Only.iso', bytes(2048*300))
    put('Clone/Clone.img', sectors(4, 4))
    put('Clone/Clone.sub', bytes(96*4))
    put('Clone/Clone.ccd', '[CloneCD]\nVersion=3\n')
    put('Psp/EBOOT.PBP', b'\0PBP' + bytes(100))
    put('Zipped (USA).7z', b'7z\xbc\xaf' + bytes(100))
    put('Zipped (Europe).ZIP', b'PK\3\4' + bytes(100))
    # A cue in Latin-1 (old Windows tools) naming two files with accents.
    put('Cafe/Café (Track 1).bin', sectors(5, 4))
    put('Cafe/Café (Track 2).bin', sectors(6, 160))
    put('Cafe/Café.cue', ('FILE "Café (Track 1).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
                          'FILE "Café (Track 2).bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 00 00:00:00\n'
                          '    INDEX 01 00:02:00\n').encode('latin-1'))
    # A cue over the 16 KB the core reads.
    put('Big/Big.bin', sectors(7, 4))
    put('Big/Big.cue', 'REM ' + 'x' * 16400 + '\nFILE "Big.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')


VARIANT_LINES = [
    'skip Clone/Clone.ccd: it is a CloneCD image (.ccd/.img/.sub), which the core can\'t read: use the disc\'s .cue/.bin or .chd',
    'skip Iso Only/Iso Only.iso: it is an .iso (2048-byte sectors, no CD music), which the core can\'t read: use the disc\'s .cue/.bin or .chd',
    'skip Loose.bin.ecm: it is ECM-compressed: decompress it to its .bin first, then Generate game files again',
    'skip Psp/EBOOT.PBP: it is a PSP EBOOT, which the core can\'t read: use the disc\'s .cue/.bin or .chd',
    'skip Zipped (Europe).ZIP: it is a compressed archive: extract it on a computer first, then Generate game files again',
    'skip Zipped (USA).7z: it is a compressed archive: extract it on a computer first, then Generate game files again',
    'skip Cooked/Cooked.cue: track 1 is MODE1/2048, which the core can\'t read: use the disc\'s .cue/.bin or .chd with 2352-byte sectors',
    'skip Ecm/Ecm.cue: Ecm.bin is ECM-compressed (Ecm.bin.ecm): decompress it first, then Generate game files again',
    'wrote Lower.json (2 tracks, cue)',
    'skip M2336/M2336.cue: track 1 is MODE2/2336, which the core can\'t read: use the disc\'s .cue/.bin or .chd with 2352-byte sectors',
    'wrote Meta.json (2 tracks, cue)',
    'wrote No Type.json (1 track, cue)',
    'wrote Café.json (2 tracks, cue)',
    'skip Big/Big.cue: the .cue is larger than the core reads (16 KB): remove its REM, TITLE and other comment lines',
]


def cue_reader_card(card):
    make_card(card)
    (card/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': CUE_READER}}))


def folder_discs(common):
    """Discs whose names have no disc tag, a folder each: one game's discs
    ("Riven/disc1", "disc2", with music on disc 2; "Lost/1", "2", "3"), and
    folders that are not one game: sequels, different titles, five discs, a
    tagged disc. Plus a Disc 2 alone and a game's discs under different
    folders."""
    for folder, stem, audio in (('Riven', 'disc1', 0), ('Riven', 'disc2', 2), ('Lost', '1', 0), ('Lost', '2', 0), ('Lost', '3', 0),
                                ('Sequels', 'Tekken 2', 0), ('Sequels', 'Tekken 3', 0), ('Mixed', 'Crash', 0), ('Mixed', 'Spyro 2', 0), ('Pair', 'Alpha 1', 0), ('Pair', 'Beta 2', 0),
                                ('Five', 'cd1', 0), ('Five', 'cd2', 0), ('Five', 'cd3', 0), ('Five', 'cd4', 0), ('Five', 'cd5', 0),
                                ('Tagged', 'Alone (Disc 1)', 0), ('Tagged', 'other 2', 0), ('Lonely (Disc 2)', 'Lonely (Disc 2)', 0),
                                ('A/Spread (Disc 1)', 'Spread (Disc 1)', 0), ('B/Spread (Disc 2)', 'Spread (Disc 2)', 1),
                                ('Gran Turismo 2 (USA)', 'Gran Turismo 2 (USA) (Disc 1) (Arcade Mode)', 0),
                                ('Gran Turismo 2 (USA)', 'Gran Turismo 2 (USA) (Disc 2) (Simulation Mode)', 0),
                                # Two whole releases of one game: a game file each.
                                ('Parasite Eve II (USA) (Disc 1)', 'Parasite Eve II (USA) (Disc 1)', 0),
                                ('Parasite Eve II (USA) (Disc 2)', 'Parasite Eve II (USA) (Disc 2)', 0),
                                ('Parasite Eve II (Europe) (Disc 1)', 'Parasite Eve II (Europe) (Disc 1)', 0),
                                ('Parasite Eve II (Europe) (Disc 2)', 'Parasite Eve II (Europe) (Disc 2)', 0)):
        with_audio(common/folder, stem, audio)
    # An earlier run named Gran Turismo 2 after disc 1's tags: its game file
    # and playlist go, the shared card keeps that name.
    out = common.parent/CORE
    out.mkdir(parents=True, exist_ok=True)
    (out/'Gran Turismo 2 (USA) (Arcade Mode).json').write_text('{}')
    (common/'Gran Turismo 2 (USA)/Gran Turismo 2 (USA) (Arcade Mode).m3u').write_text(
        'Gran Turismo 2 (USA) (Disc 1) (Arcade Mode).cue\nGran Turismo 2 (USA) (Disc 2) (Simulation Mode).cue\n')


def tree(card):
    return {p.relative_to(card).as_posix(): p.read_bytes() for p in sorted(card.rglob('*')) if p.is_file()}


@unittest.skipUnless(NODE, 'node not installed')
class SdCardPage(unittest.TestCase):
    def test_cores_games_elsewhere_and_bios(self):
        with TemporaryDirectory() as tmp:
            card = Path(tmp)
            make_card(card)
            other = card/'Cores/akoaysigod.PSX'   # another developer's core: no slots 100 and 101+
            other.mkdir()
            (other/'core.json').write_text(json.dumps({'core': {'metadata': {'platform_ids': ['psx']}}}))
            (other/'data.json').write_text(json.dumps({'data': {'data_slots': [{'id': 0, 'extensions': ['cue']}]}}))
            shutil.move(card/'Assets/playstation/common', card/'Assets/psx/common')
            (card/'Assets/playstation/common').mkdir()
            bios = bytearray(524288)
            bios[0x7ff32:0x7ff32 + 33] = b'System ROM Version 4.1 12/16/97 E'
            (card/'Assets/psx/common/PSX BIOS.bin').write_bytes(bios)
            checks = lambda: subprocess.run([NODE, str(ROOT/'tests/sd_card_page.cjs'), str(card), '--checks'],
                                            check=True, capture_output=True, text=True).stdout.splitlines()
            self.assertEqual(checks(), [
                'core GuardianSoul.PlayStation playstation supported', 'elsewhere Assets/psx/common',
                "Assets/psx/common/PSX BIOS.bin is a Europe PlayStation BIOS, but the core doesn't look for it there: "
                'copy it to Assets/playstation/common/ as scph5502.bin',
                'core akoaysigod.PSX psx other'])
            (card/'Assets/psx/common/PSX BIOS.bin').unlink()
            self.assertIn("No BIOS found: the core can't start games without one. Copy your PlayStation BIOS to "
                          'Assets/playstation/common/ as scph5501.bin (USA), scph5500.bin (Japan) or scph5502.bin (Europe)', checks())
            (card/'Assets/playstation/common/scph1001.bin').write_bytes(bytes(1000))
            (card/'Assets/playstation/common/SCPH5501.BIN').write_bytes(bios[:0x7ff32] + b'System ROM Version 4.1 12/16/97 A' + bios[0x7ff32+33:])
            self.assertEqual(checks()[2:4], ['BIOS SCPH5501.BIN: USA (System ROM Version 4.1 12/16/97 A), OK',
                                             'BIOS scph1001.bin is 1 KB, but a PlayStation BIOS is exactly 512 KB: '
                                             "replace it with a full dump of your console's BIOS"])

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
            self.assertIn('Final Fantasy IX.json', names)
            self.assertIn('Game.json', names)
            self.assertNotIn('Final Fantasy IX (USA) (Disc 4) (Rev 1).json', names)
            ff9 = json.loads((out/'Final Fantasy IX.json').read_text())['instance']['data_slots']
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
            self.assertIn('wrote Final Fantasy IX.json (4 discs, playlist Final Fantasy IX (USA)/Final Fantasy IX.m3u)', js.stdout)
            out = page/'Assets/playstation'/CORE
            ff9 = json.loads((out/'Final Fantasy IX.json').read_text())['instance']
            self.assertEqual(ff9, {'magic': 'APF_VER_1', 'data_path': '',
                                   'data_slots': [{'id': 100, 'filename': 'Final Fantasy IX (USA)/Final Fantasy IX.m3u'},
                                                  {'id': 101, 'filename': 'Final Fantasy IX (USA)/Final Fantasy IX (Disc 1) (v1.1).bin'},
                                                  {'id': 16, 'filename': 'Final Fantasy IX (v1.1).sav'}]})
            self.assertFalse((out/'Final Fantasy IX (USA) (Disc 4) (Rev 1).json').exists())   # per-disc file from an earlier run

    def test_user_playlist_gets_a_game_file(self):
        # Issue #25: a cheat disc shared by several games through playlists
        # the user wrote (the cheat disc first, then the game). Each playlist
        # gets a game file naming it; one with a missing disc or more than
        # four is skipped; a disc may sit beside the playlist's folder (..).
        with TemporaryDirectory() as tmp:
            script, page = Path(tmp)/'script', Path(tmp)/'page'
            cue_reader_card(script)
            declared = [dict(s, extensions=['cue', 'm3u']) if s['id'] == 100 else s for s in CUE_READER]
            (script/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': declared}}))
            common = script/'Assets/playstation/common'
            one_track(common/'CodePatch', 'CodePatch')
            one_track(common/'Ace Combat 2 (USA)', 'Ace Combat 2 (USA)')
            (common/'Ace Combat 2 (USA) - Cheats.m3u').write_text('#EXTM3U\r\nCodePatch/CodePatch.cue\r\nAce Combat 2 (USA)\\Ace Combat 2 (USA).cue  \r\n')
            (common/'Ace Combat 2 (USA)/Ace - Cheats.m3u').write_text('../CodePatch/CodePatch.cue\nAce Combat 2 (USA).cue\n')
            (common/'Broken.m3u').write_text('CodePatch/CodePatch.cue\nNope/Nope.cue\n')
            (common/'Many.m3u').write_text('CodePatch/CodePatch.cue\n' * 5)
            shutil.copytree(script, page)
            py = subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(script),
                                 '--core', 'PlayStation'], check=True, capture_output=True, text=True)
            js = subprocess.run([NODE, str(ROOT/'tests/sd_card_page.cjs'), str(page), '--core', CORE],
                                check=True, capture_output=True, text=True)
            self.assertEqual(py.stdout.splitlines()[:-1], js.stdout.splitlines()[:-1])
            self.assertEqual(tree(script), tree(page))
            out = page/'Assets/playstation'/CORE
            cheat = json.loads((out/'Ace Combat 2 (USA) - Cheats.json').read_text())['instance']
            self.assertEqual(cheat, {'magic': 'APF_VER_1', 'data_path': '',
                                     'data_slots': [{'id': 100, 'filename': 'Ace Combat 2 (USA) - Cheats.m3u'}]})
            inner = json.loads((out/'Ace - Cheats.json').read_text())['instance']
            self.assertEqual(inner, {'magic': 'APF_VER_1', 'data_path': 'Ace Combat 2 (USA)/',
                                     'data_slots': [{'id': 100, 'filename': 'Ace - Cheats.m3u'}]})
            self.assertIn('skip Broken.m3u: Nope/Nope.cue not found', js.stdout)
            self.assertIn('skip Many.m3u: 5 discs (the core takes 4)', js.stdout)
            self.assertTrue((out/'CodePatch.json').is_file() and (out/'Ace Combat 2 (USA).json').is_file())
        # A core without .m3u in slot 100 makes no playlist game files.
        with TemporaryDirectory() as tmp:
            card = Path(tmp)
            cue_reader_card(card)
            common = card/'Assets/playstation/common'
            one_track(common/'CodePatch', 'CodePatch')
            (common/'Cheats.m3u').write_text('CodePatch/CodePatch.cue\n')
            subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(card), '--core', 'PlayStation'],
                           check=True, capture_output=True)
            self.assertFalse((card/'Assets/playstation'/CORE/'Cheats.json').exists())

    def test_one_file_core_matches_the_script(self):
        # --one-file-disc core: merged images used as they are, per-track
        # files merged into "(one file).bin", a .trk table per disc.
        with TemporaryDirectory() as tmp:
            script, page = Path(tmp)/'script', Path(tmp)/'page'
            make_card(script)
            common = script/'Assets/playstation/common'
            with_audio(common/'Rayman', 'Rayman (USA)', 50)
            index01_only_twin(common)
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
            redump, merged, split = twin_tables(page/'Assets/playstation/common')
            self.assertEqual(redump, merged)
            self.assertEqual(redump, split)

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

    def test_other_disc_namings_match_the_script(self):
        # "X.cue" with "X Disc2.cue" (a user's Star Wars Rebel Assault II),
        # "CD1"/"CD2", " - Disc 1"/" - Disc 2" group as the script groups them;
        # sequel numbers stay apart.
        for reader in (False, True):
            with self.subTest(reader=reader), TemporaryDirectory() as tmp:
                script, page = Path(tmp)/'script', Path(tmp)/'page'
                if reader:
                    cue_reader_card(script)
                    declared = [dict(s, extensions=['cue', 'm3u']) if s['id'] == 100 else s for s in CUE_READER]
                    (script/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': declared}}))
                else:
                    make_card(script)
                common = script/'Assets/playstation/common'
                for folder, stem, audio in (('Star Wars Rebel Assault II', 'Star Wars Rebel Assault II', 0),
                                            ('Star Wars Rebel Assault II Disc2', 'Star Wars Rebel Assault II Disc2', 0),
                                            ('Riven', 'Riven CD1', 0), ('Riven', 'Riven CD2', 1),
                                            ('Myst', 'Myst - Disc 1 (USA)', 0), ('Myst', 'Myst - Disc 2 (USA)', 0),
                                            ('Tekken 2', 'Tekken 2', 0), ('Tekken 2 Demo', 'Tekken 2 Demo', 0)):
                    with_audio(common/folder, stem, audio)
                shutil.copytree(script, page)
                py = subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(script),
                                     '--core', 'PlayStation'], check=True, capture_output=True, text=True)
                js = subprocess.run([NODE, str(ROOT/'tests/sd_card_page.cjs'), str(page), '--core', CORE],
                                    check=True, capture_output=True, text=True)
                self.assertEqual(py.stdout.splitlines()[:-1], js.stdout.splitlines()[:-1])
                self.assertEqual(tree(script).keys(), tree(page).keys())
                for name, data in tree(script).items():
                    self.assertEqual(tree(page)[name], data, name)
                out = page/'Assets/playstation'/CORE
                self.assertTrue((out/'Star Wars Rebel Assault II.json').exists())
                self.assertFalse((out/'Star Wars Rebel Assault II Disc2.json').exists())
                self.assertTrue((out/'Tekken 2.json').exists() and (out/'Tekken 2 Demo.json').exists())

    def test_folder_discs_match_the_script(self):
        # Discs grouped by their folder, the folders that stay apart and the
        # disc warnings, as the script does them, for both core kinds.
        for reader in (False, True):
            with self.subTest(reader=reader), TemporaryDirectory() as tmp:
                script, page = Path(tmp)/'script', Path(tmp)/'page'
                if reader:
                    cue_reader_card(script)
                    declared = [dict(s, extensions=['cue', 'm3u']) if s['id'] == 100 else s for s in CUE_READER]
                    (script/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': declared}}))
                else:
                    make_card(script)
                folder_discs(script/'Assets/playstation/common')
                shutil.copytree(script, page)
                py = subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(script),
                                     '--core', 'PlayStation'], check=True, capture_output=True, text=True)
                js = subprocess.run([NODE, str(ROOT/'tests/sd_card_page.cjs'), str(page), '--core', CORE],
                                    check=True, capture_output=True, text=True)
                self.assertEqual(py.stdout.splitlines()[:-1], js.stdout.splitlines()[:-1])
                self.assertEqual(tree(script).keys(), tree(page).keys())
                for name, data in tree(script).items():
                    self.assertEqual(tree(page)[name], data, name)
                self.assertIn('Riven: 2 discs in one folder, grouped as one game', js.stdout)
                self.assertIn('warning: Spread: its discs are in different folders', js.stdout)
                out = page/'Assets/playstation'/CORE
                self.assertTrue((out/'Lost.json').exists())
                self.assertEqual((out/'Riven.json').exists(), reader)   # the older core takes 2 files of disc 2
                self.assertFalse((out/'disc1.json').exists())
                self.assertTrue((out/'Gran Turismo 2 (USA).json').exists())
                for region in ('USA', 'Europe'):
                    self.assertIn(f'Parasite Eve II ({region}).sav', (out/f'Parasite Eve II ({region}).json').read_text())
                    self.assertIn(f'Parasite Eve II ({region}) (Disc 1)', (out/f'Parasite Eve II ({region}).json').read_text())
                self.assertFalse((out/'Gran Turismo 2 (USA) (Arcade Mode).json').exists())
                self.assertIn('Gran Turismo 2 (USA) (Arcade Mode).sav', (out/'Gran Turismo 2 (USA).json').read_text())
                self.assertEqual((page/'Assets/playstation/common/Gran Turismo 2 (USA)/Gran Turismo 2 (USA) (Arcade Mode).m3u').exists(), not reader)

    def test_track_sizes_match_the_script(self):
        # A cue-reader core that declares slot 97: cues opened by path (more
        # files than 101-104) get <cue>.tsz and their game files name it,
        # disc 1's for a grouped or playlist game file.
        for playlist in (False, True):
            with self.subTest(playlist=playlist), TemporaryDirectory() as tmp:
                script, page = Path(tmp)/'script', Path(tmp)/'page'
                cue_reader_card(script)
                declared = [dict(s, extensions=['cue', 'm3u']) if s['id'] == 100 and playlist else s for s in CUE_READER] + [{'id': 97}]
                # Disc names that differ by more than their number: a playlist
                # game file when slot 100 takes .m3u, disc 1 with five files.
                with_audio(script/'Assets/playstation/common/Odd (USA)', 'Odd (Disc 1) (v1.1)', 4)
                with_audio(script/'Assets/playstation/common/Odd (USA)', 'Odd (USA) (Disc 2) (Rev 1)', 1)
                (script/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': declared}}))
                shutil.copytree(script, page)
                py = subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(script),
                                     '--core', 'PlayStation'], check=True, capture_output=True, text=True)
                js = subprocess.run([NODE, str(ROOT/'tests/sd_card_page.cjs'), str(page), '--core', CORE],
                                    check=True, capture_output=True, text=True)
                self.assertEqual(py.stdout.splitlines()[:-1], js.stdout.splitlines()[:-1])
                self.assertEqual(tree(script).keys(), tree(page).keys())
                for name, data in tree(script).items():
                    self.assertEqual(tree(page)[name], data, name)
                legend = json.loads((page/'Assets/playstation'/CORE/'Legend (USA).json').read_text())['instance']['data_slots']
                self.assertIn({'id': 97, 'filename': 'Legend (Disc 1)/Legend (USA) (Disc 1).tsz'}, legend)
                self.assertTrue(any(n.endswith('.tsz') for n in tree(page)))
                odd = json.loads((page/'Assets/playstation'/CORE/('Odd.json' if playlist else 'Odd (Disc 1) (v1.1).json')).read_text())['instance']['data_slots']
                self.assertIn({'id': 97, 'filename': 'Odd (USA)/Odd (Disc 1) (v1.1).tsz'}, odd)

    def test_cue_variants_match_the_script(self):
        # Other tools' cues and images: what the core reads gets a game file,
        # the rest a line saying what to do.
        with TemporaryDirectory() as tmp:
            script, page = Path(tmp)/'script', Path(tmp)/'page'
            cue_variants_card(script)
            shutil.copytree(script, page)
            py = subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(script),
                                 '--core', 'PlayStation'], check=True, capture_output=True, text=True)
            js = subprocess.run([NODE, str(ROOT/'tests/sd_card_page.cjs'), str(page), '--core', CORE],
                                check=True, capture_output=True, text=True)
            self.assertEqual(py.stdout.splitlines()[:-1], js.stdout.splitlines()[:-1])
            self.assertEqual(tree(script).keys(), tree(page).keys())
            for name, data in tree(script).items():
                self.assertEqual(tree(page)[name], data, name)
            lines = js.stdout.splitlines()
            for line in VARIANT_LINES: self.assertIn(line, lines)
            self.assertEqual([l for l in lines if l.startswith('skip ') and 'Zipped' in l], VARIANT_LINES[4:6])
            out = page/'Assets/playstation'/CORE
            self.assertEqual(json.loads((out/'Lower.json').read_text())['instance']['data_slots'][:2],
                             [{'id': 100, 'filename': 'Lower.cue'}, {'id': 101, 'filename': 'Lower.bin'}])
            for gone in ('M2336', 'Cooked', 'Ecm', 'Clone', 'Iso Only', 'EBOOT'): self.assertFalse((out/f'{gone}.json').exists())

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
            self.assertIn("skip Lost Cue/Lost (USA) (Track 1).bin: it is one track of a disc whose .cue is missing: "
                          "put the disc's .cue next to it", js.stdout)
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
                          "Final Fantasy IX (USA)/Final Fantasy IX.m3u", js.stdout)
            self.assertEqual((page/'Assets/playstation/common/Final Fantasy IX (USA)/Final Fantasy IX.m3u').read_text(),
                             'Final Fantasy IX (Disc 1) (v1.1).cue\nFinal Fantasy IX (Disc 2) (v1.1).cue\n'
                             'Final Fantasy IX (Disc 3) (v1.1).cue\nFinal Fantasy IX (USA) (Disc 4) (Rev 1).cue\n')
            for disc in ('Final Fantasy IX (Disc 1) (v1.1)', 'Final Fantasy IX (USA) (Disc 4) (Rev 1)'):
                slots = json.loads((out/f'{disc}.json').read_text())['instance']['data_slots']
                self.assertEqual(slots, [{'id': 100, 'filename': f'Final Fantasy IX (USA)/{disc}.cue'},
                                         {'id': 101, 'filename': f'Final Fantasy IX (USA)/{disc}.bin'},
                                         {'id': 16, 'filename': 'Final Fantasy IX (v1.1).sav'}])


if __name__ == '__main__':
    unittest.main()
