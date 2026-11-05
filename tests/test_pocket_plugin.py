"""The Pocket Sync / pupdate plugin (tools/pocket-plugin) writes the same game
files, shared cards, split tracks and track tables as
scripts/make_pocket_instances.py, never fills the SD card past its reserve,
and can replace originals (through its folder on the computer when the card
is short of space). Needs cargo with the wasm32-wasip1 target and the extism
Python host (python3 -m pip install extism)."""
from pathlib import Path
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from tests.test_pocket_instances import MERGED, sectors
from tests.test_sd_card_page import (CORE, CUE_READER, VARIANT_LINES, cue_reader_card, cue_variants_card, folder_discs,
                                     index01_only_twin, make_card, one_track, tree, twin_tables, with_audio)

ONE_FILE = ([{'id': 0}, {'id': 16}, {'id': 100}]
            + [{'id': 100*n + k, 'extensions': ['bin' if k == 1 else 'trk']} for n in (1, 2, 3, 4) for k in (1, 2)])

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT/'tools/pocket-plugin'
WASM = CRATE/'target/wasm32-wasip1/release/plugin.wasm'
HOST = CRATE/'host/run_plugin.py'
MAKE = 'Generate game files'
KEEP = 'Convert them, keep the originals'
REPLACE = 'Convert them, replace the originals'
SKIP = 'Skip them'
RESERVE = 256 << 20   # convert.rs keeps this much free


def has_wasm_target():
    """cargo plus a wasm32-wasip1 standard library (rustup, or a distro's rust-wasm)."""
    if not shutil.which('cargo') or not shutil.which('rustc'): return False
    out = subprocess.run(['rustc', '--print', 'target-libdir', '--target', 'wasm32-wasip1'], capture_output=True, text=True)
    return out.returncode == 0 and Path(out.stdout.strip()).is_dir() and any(Path(out.stdout.strip()).glob('libstd-*'))


CHDMAN = shutil.which('chdman')


def add_chds(common, scratch):
    """CHD images made with chdman: per-track files with stored pregaps and
    track lengths that need padding, a merged image with a PREGAP outside it,
    a single track, and one compressed with zstd."""
    scratch.mkdir()
    def make(name, cue, codecs=None):
        (scratch/f'{name}.cue').write_text(cue)
        folder = common/'CHD'   # not a folder named after the image: that is where it extracts to
        folder.mkdir(exist_ok=True)
        cmd = [CHDMAN, 'createcd', '-i', str(scratch/f'{name}.cue'), '-o', str(folder/f'{name}.chd')]
        subprocess.run(cmd + (['-c', codecs] if codecs else []), check=True, capture_output=True)
    lines = ['FILE "t1.bin" BINARY', '  TRACK 01 MODE2/2352', '    INDEX 01 00:00:00']
    (scratch/'t1.bin').write_bytes(sectors(1, 5))
    for k, n in ((2, 163), (3, 171)):
        (scratch/f't{k}.bin').write_bytes(sectors(k, n))
        lines += [f'FILE "t{k}.bin" BINARY', f'  TRACK {k:02d} AUDIO', '    INDEX 00 00:00:00', '    INDEX 01 00:02:00']
    make('Tracks (USA)', '\n'.join(lines) + '\n')
    make('Zstd (Europe) (Disc 1)', '\n'.join(lines) + '\n', 'cdzs')
    (scratch/'m.bin').write_bytes(sectors(1, 7) + sectors(2, 100))
    make('Merged Gap', 'FILE "m.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
                       '  TRACK 02 AUDIO\n    PREGAP 00:02:00\n    INDEX 01 00:00:07\n')
    (scratch/'s.bin').write_bytes(sectors(9, 13))
    make('Single', 'FILE "s.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    make('Zstd (Europe) (Disc 2)', 'FILE "s.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n', 'cdzs')


def report(stdout):
    """The plugin's per-game lines, as the script prints them (without its
    menus, progress and closing lines, and the summary with its path)."""
    lines = [l for l in stdout.splitlines() if not l.startswith(('  ', '[', 'needs converting:', 'Converting ', 'Looking for ', 'Removed '))]
    start = next(i for i, l in enumerate(lines) if l.startswith('PlayStation game files'))
    end = next(i for i, l in enumerate(lines) if ' game files in ' in l)
    return lines[start+1:end]


def one_file_card(card, chds=None):
    make_card(card)
    common = card/'Assets/playstation/common'
    with_audio(common/'Rayman', 'Rayman (USA)', 50)
    index01_only_twin(common)
    (common/'Plain').mkdir()
    (common/'Plain/Game.bin').write_bytes(sectors(1, 10) + sectors(2, 200) + sectors(3, 100))
    (common/'Plain/Plain.cue').write_text(MERGED.replace('    PREGAP 00:02:00\n    INDEX 01 00:02:60\n',
                                                         '    INDEX 00 00:02:60\n    INDEX 01 00:03:60\n'))
    (common/'Ape Escape (Europe)/Ape Escape (Europe).sbi').write_bytes(b'SBI\0' + bytes([3, 8, 5, 1]) + bytes(10))
    (card/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': ONE_FILE}}))
    if chds: add_chds(common, chds)


def used(card):
    return sum(len(d) for d in tree(card).values())


@unittest.skipUnless(has_wasm_target() and importlib.util.find_spec('extism'),
                     'needs cargo with wasm32-wasip1 and the extism Python package')
class PocketPlugin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['cargo', 'build', '--release', '--quiet'], cwd=CRATE, check=True)

    def plugin(self, card, *answers, host=None, card_bytes=None, stop_at=None, verbose=True):
        cmd = [sys.executable, str(HOST), str(WASM), str(card)] + [a for x in answers for a in ('--answer', x)]
        if verbose: cmd += ['--config', 'verbose=1']   # a line per game file, as the script prints
        if host: cmd += ['--host-folder', str(host)]
        if card_bytes: cmd += ['--config', f'test_card_bytes={card_bytes}']
        if stop_at: cmd += ['--config', f'test_stop_at={stop_at}']
        run = subprocess.run(cmd, check=not stop_at, capture_output=True, text=True)
        if stop_at: self.assertNotEqual(run.returncode, 0, f'did not stop at {stop_at}')
        return run.stdout

    def script(self, card, *flags):
        return subprocess.run([sys.executable, str(ROOT/'scripts/make_pocket_instances.py'), str(card),
                               '--core', CORE.split('.', 1)[1], *flags], check=True, capture_output=True, text=True).stdout

    def assertSameTree(self, a, b):
        self.assertEqual(tree(a).keys(), tree(b).keys())
        for name, data in tree(a).items():
            self.assertEqual(tree(b)[name], data, name)

    def assertNoLargeFiles(self, before, card):
        """Every file added or changed is small (game files, tables), and no
        disc file is gone (old game files may be)."""
        after = tree(card)
        self.assertEqual({n: len(d) for n, d in after.items() if before.get(n) != d and len(d) > 2048}, {})
        self.assertEqual([n for n in before if n not in after and not n.endswith('.json')], [])

    def compare(self, script, plugin, answer, *flags):
        shutil.copytree(script, plugin)
        py = self.script(script, *flags)
        out = self.plugin(plugin, answer)
        self.assertEqual(py.splitlines()[:-1], report(out))
        self.assertSameTree(script, plugin)
        return out

    def test_keep_matches_the_script(self):
        with TemporaryDirectory() as tmp:
            script, plugin = Path(tmp)/'script', Path(tmp)/'plugin'
            make_card(script)
            help_entry = script/'Assets/playstation'/CORE/'! How to add games (see README).json'
            help_entry.write_text('{}')
            out = self.compare(script, plugin, KEEP, '--convert')
            self.assertIn('needs converting: Merged/Game.cue', out)
            games = plugin/'Assets/playstation'/CORE
            self.assertFalse((games/help_entry.name).exists())
            ff9 = json.loads((games/'Final Fantasy IX.json').read_text())['instance']
            self.assertEqual([s['id'] for s in ff9['data_slots']], [100, 101, 201, 301, 401, 16])
            self.assertEqual(ff9['data_slots'][-1]['filename'], 'Final Fantasy IX (v1.1).sav')
            self.assertEqual((plugin/'Saves/playstation/common/Final Fantasy IX (v1.1).sav').read_bytes(), b'disc 4')
            self.assertIn('change discs in Core Settings > Swap Discs.', out)   # no interact.json: the 0.5.3 name
            # 0.5.2 calls the menu "Disc": the plugin reads it from the core.
            (plugin/'Cores'/CORE/'interact.json').write_text(json.dumps({'interact': {'variables': [
                {'name': 'Disc', 'id': 21, 'type': 'list', 'address': '0x50000044'}]}}))
            self.assertIn('change discs in Core Settings > Disc.', self.plugin(plugin, MAKE))

    def test_skip_matches_the_script_without_convert(self):
        with TemporaryDirectory() as tmp:
            script, plugin = Path(tmp)/'script', Path(tmp)/'plugin'
            make_card(script)
            before = tree(script)
            out = self.compare(script, plugin, SKIP)
            self.assertIn('skip Merged/Game.cue: Game.bin: 3 tracks in one file (merged image)', out)
            self.assertNoLargeFiles(before, plugin)

    def test_one_file_core_keep_matches_the_script(self):
        with TemporaryDirectory() as tmp:
            script, plugin = Path(tmp)/'script', Path(tmp)/'plugin'
            one_file_card(script)
            out = self.compare(script, plugin, KEEP, '--convert')
            self.assertIn('wrote Rayman (USA).json (51 tracks, one file)', out)
            self.assertIn('  joining 51 files into Rayman (USA) (one file).bin', out)
            self.assertIn('LibCrypt Ape Escape (Europe).sbi', out)
            redump, merged, split = twin_tables(plugin/'Assets/playstation/common')
            self.assertEqual(redump, merged)
            self.assertEqual(redump, split)
            # Nothing is left to convert: the menu goes straight to the game files.
            before = tree(plugin)
            again = self.plugin(plugin, MAKE)
            self.assertIn('[Game files for GuardianSoul.PlayStation: Generate game files]', again)
            self.assertEqual(tree(plugin), before)

    def test_leftovers_of_a_stopped_run_go(self):
        # A run stopped part-way (closed, card pulled) leaves .part files and
        # the space check; the next run removes them and nothing else.
        with TemporaryDirectory() as tmp:
            card = Path(tmp)/'card'
            make_card(card)
            common = card/'Assets/playstation/common'
            leftovers = [common/'Merged/Game/Game (Track 01).bin.part', card/'Assets/playstation'/CORE/'Game.json.part',
                         card/'Saves/playstation/common/Game.sav.part', card/'.psx-space-check']
            for f in leftovers:
                f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b'unfinished')
            keep = common/'notes.part'
            keep.write_text('mine')
            out = self.plugin(card, SKIP)
            self.assertIn('Removed 4 unfinished file(s)', out)
            self.assertEqual([f for f in leftovers if f.exists()], [])
            self.assertTrue(keep.exists())

    def test_folder_discs_match_the_script(self):
        # Discs grouped by folder, folders that stay apart and the disc warnings.
        for reader in (False, True):
            with self.subTest(reader=reader), TemporaryDirectory() as tmp:
                script, plugin = Path(tmp)/'script', Path(tmp)/'plugin'
                if reader:
                    cue_reader_card(script)
                    declared = [dict(s, extensions=['cue', 'm3u']) if s['id'] == 100 else s for s in CUE_READER]
                    (script/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': declared}}))
                else:
                    make_card(script)
                folder_discs(script/'Assets/playstation/common')
                out = self.compare(script, plugin, MAKE if reader else SKIP)
                self.assertIn('Lost: 3 discs in one folder, grouped as one game', out)
                self.assertIn('warning: Spread: its discs are in different folders', out)
                out_dir = plugin/'Assets/playstation'/CORE
                self.assertTrue((out_dir/'Parasite Eve II (USA).json').exists())
                self.assertTrue((out_dir/'Parasite Eve II (Europe).json').exists())

    def test_cue_reader_core_matches_the_script(self):
        # --cue-mount core: merged and multi-file discs need no converting.
        with TemporaryDirectory() as tmp:
            script, plugin = Path(tmp)/'script', Path(tmp)/'plugin'
            cue_reader_card(script)
            before = tree(script)
            out = self.compare(script, plugin, MAKE)
            self.assertNotIn('needs converting', out)
            self.assertIn('wrote Game.json (3 tracks, cue)', out)
            self.assertIn('wrote Ape Escape (Europe).json (1 track, cue, LibCrypt Ape Escape (Europe).sbi)', out)
            # Cue FILE names that differ from the files: the game file names the real files.
            self.assertIn('skip Gone/Gone (USA).cue: Gone (USA) (Track 2).bin is missing: put it next to the .cue, '
                          'with the name the .cue gives it', out)
            self.assertIn('skip Stray/Stray (USA).cue: Stray (USA) (Track 1).bin is missing', out)
            self.assertIn('skip Gap: disc 2 is missing (found discs 1, 3)', out)
            self.assertIn('skip Long Game: 5 discs, but this core takes 4: update the core', out)
            slots = lambda name: [s['filename'] for s in json.loads(
                (plugin/'Assets/playstation'/CORE/f'{name}.json').read_text())['instance']['data_slots']]
            self.assertEqual(slots('Case'), ['Case.cue', 'Case Game (USA).bin'])
            self.assertEqual(slots('Renamed'), ['Renamed.cue', 'Renamed.bin'])
            self.assertEqual(slots('Tracks Renamed (USA)'), ['Tracks Renamed (USA).cue'] + [f'TR (Track 0{k}).bin' for k in (1, 2, 3)])
            self.assertIn("Final Fantasy IX: the discs' names differ by more than their number: wrote the playlist", out)
            self.assertNoLargeFiles(before, plugin)
            self.assertEqual([n for n in tree(plugin) if n not in before and not n.endswith(('.json', '.sav', '.m3u'))],
                             ['Assets/playstation/common/Bin Only/Bin Only (USA).cue'])
        # Slot 100 taking .m3u: one game file names FFIX's playlist.
        with TemporaryDirectory() as tmp:
            script, plugin = Path(tmp)/'script', Path(tmp)/'plugin'
            cue_reader_card(script)
            declared = [dict(s, extensions=['cue', 'm3u']) if s['id'] == 100 else s for s in CUE_READER]
            (script/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': declared}}))
            out = self.compare(script, plugin, MAKE)
            self.assertIn('wrote Final Fantasy IX.json (4 discs, playlist Final Fantasy IX (USA)/Final Fantasy IX.m3u)', out)
        # Playlists the user wrote (issue #25: a cheat disc shared by games):
        # a game file each; a missing disc or more than four is skipped.
        with TemporaryDirectory() as tmp:
            script, plugin = Path(tmp)/'script', Path(tmp)/'plugin'
            cue_reader_card(script)
            declared = [dict(s, extensions=['cue', 'm3u']) if s['id'] == 100 else s for s in CUE_READER]
            (script/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': declared}}))
            common = script/'Assets/playstation/common'
            one_track(common/'CodePatch', 'CodePatch')
            (common/'Ape - Cheats.m3u').write_text('#EXTM3U\r\nCodePatch/CodePatch.cue\r\nApe Escape (Europe)\\Ape Escape (Europe).cue  \r\n')
            (common/'Ape Escape (Europe)/Ape Inner.m3u').write_text('../CodePatch/CodePatch.cue\nApe Escape (Europe).cue\n')
            (common/'Broken.m3u').write_text('CodePatch/CodePatch.cue\nNope/Nope.cue\n')
            (common/'Many.m3u').write_text('CodePatch/CodePatch.cue\n' * 5)
            out = self.compare(script, plugin, MAKE)
            self.assertIn('wrote Ape - Cheats.json (playlist Ape - Cheats.m3u)', out)
            self.assertIn('wrote Ape Inner.json (playlist Ape Escape (Europe)/Ape Inner.m3u)', out)
            self.assertIn('skip Broken.m3u: Nope/Nope.cue not found', out)
            self.assertIn('skip Many.m3u: 5 discs (the core takes 4)', out)
            inner = json.loads((plugin/'Assets/playstation'/CORE/'Ape Inner.json').read_text())['instance']
            self.assertEqual(inner, {'magic': 'APF_VER_1', 'data_path': 'Ape Escape (Europe)/',
                                     'data_slots': [{'id': 100, 'filename': 'Ape Inner.m3u'}]})
        # Declaring slot 99 (0.5.4): a game file's .sbi goes there, as the cue has no path on the Pocket.
        with TemporaryDirectory() as tmp:
            script, plugin = Path(tmp)/'script', Path(tmp)/'plugin'
            cue_reader_card(script)
            (script/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': CUE_READER + [{'id': 99}]}}))
            self.compare(script, plugin, MAKE)
            ape = json.loads((plugin/'Assets/playstation'/CORE/'Ape Escape (Europe).json').read_text())['instance']
            self.assertIn({'id': 99, 'filename': 'Ape Escape (Europe).sbi'}, ape['data_slots'])
        # Declaring slot 98 (--cheats): the game's .cht goes there.
        with TemporaryDirectory() as tmp:
            script, plugin = Path(tmp)/'script', Path(tmp)/'plugin'
            cue_reader_card(script)
            (script/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': CUE_READER + [{'id': 98}, {'id': 99}]}}))
            common = script/'Assets/playstation/common'
            (common/'Ape Escape (Europe)/Ape Escape (Europe).cht').write_text('80012345 0063\n')
            (common/'Legend (Disc 1)/Legend (USA).cht').write_text('80012345 0063\n')
            out = self.compare(script, plugin, MAKE)
            self.assertIn('cheats Ape Escape (Europe).cht)', out)
            legend = json.loads((plugin/'Assets/playstation'/CORE/'Legend (USA).json').read_text())['instance']
            self.assertIn({'id': 98, 'filename': 'Legend (Disc 1)/Legend (USA).cht'}, legend['data_slots'])

    def test_cue_variants_match_the_script(self):
        # Other tools' cues and images: the same game files and lines as the script.
        with TemporaryDirectory() as tmp:
            script, plugin = Path(tmp)/'script', Path(tmp)/'plugin'
            cue_variants_card(script)
            lines = report(self.compare(script, plugin, MAKE))
            for line in VARIANT_LINES: self.assertIn(line, lines)

    @unittest.skipUnless(CHDMAN, 'chdman (MAME tools) not installed')
    def test_chd_keep_matches_chdman(self):
        for one_file in (False, True):
            with self.subTest(one_file=one_file), TemporaryDirectory() as tmp:
                script, plugin = Path(tmp)/'script', Path(tmp)/'plugin'
                make_card(script)
                add_chds(script/'Assets/playstation/common', Path(tmp)/'chd-sources')
                if one_file:
                    declared = ([{'id': 0}, {'id': 16}, {'id': 100}]
                                + [{'id': 100*n + k, 'extensions': ['bin' if k == 1 else 'trk']} for n in (1, 2) for k in (1, 2)])
                    (script/'Cores'/CORE/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': declared}}))
                out = self.compare(script, plugin, KEEP, '--convert')
                self.assertIn('converted CHD/Tracks (USA).chd -> CHD/Tracks (USA)/Tracks (USA).cue', out)
                self.assertIn('wrote Zstd (Europe).json (2 discs)', out)
                self.assertIn('wrote Tracks (USA).json (3 tracks', out)

    @unittest.skipUnless(CHDMAN, 'chdman (MAME tools) not installed')
    def test_replace_on_the_card(self):
        with TemporaryDirectory() as tmp:
            script, plugin = Path(tmp)/'script', Path(tmp)/'plugin'
            one_file_card(script, Path(tmp)/'chd-sources')
            shutil.copytree(script, plugin)
            common = plugin/'Assets/playstation/common'
            rayman = sorted((common/'Rayman').glob('*.bin'))
            joined = b''.join(p.read_bytes() for p in sorted(rayman, key=lambda p: int(p.stem.split()[-1][:-1])))
            self.script(script, '--convert')
            out = self.plugin(plugin, REPLACE)
            self.assertIn('removed the original Rayman/Rayman (USA).cue', out)
            self.assertIn('removed the original CHD/Tracks (USA).chd', out)
            self.assertEqual(sorted(p.name for p in (common/'Rayman').iterdir()),
                             ['Rayman (USA) (one file).bin', 'Rayman (USA) (one file).trk', 'Rayman (USA).cue'])
            self.assertEqual((common/'Rayman/Rayman (USA) (one file).bin').read_bytes(), joined)
            self.assertEqual(list(common.rglob('*.chd')), [])
            for name in ('CHD/Tracks (USA)/Tracks (USA).bin', 'CHD/Tracks (USA)/Tracks (USA).cue', 'Rayman/Rayman (USA) (one file).trk'):
                self.assertEqual((common/name).read_bytes(), (script/'Assets/playstation/common'/name).read_bytes(), name)
            self.assertIn('wrote Rayman (USA).json (51 tracks, one file)', out)
            # The rewritten cue gives the same game file and table as the script's.
            for name in ('Rayman (USA).json', 'Tracks (USA).json', 'Zstd (Europe).json'):
                self.assertEqual((plugin/'Assets/playstation'/CORE/name).read_bytes(),
                                 (script/'Assets/playstation'/CORE/name).read_bytes(), name)
            before = tree(plugin)
            self.assertIn('Generate game files]', self.plugin(plugin, MAKE))
            self.assertEqual(tree(plugin), before)

    def test_full_card(self):
        with TemporaryDirectory() as tmp:
            card, host = Path(tmp)/'card', Path(tmp)/'computer'
            one_file_card(card)
            host.mkdir()
            limit = used(card) + RESERVE + (1 << 20)   # 1 MB more than the reserve: no room for Rayman's copy
            out = self.plugin(card, KEEP, card_bytes=limit)
            self.assertIn('could not join Rayman/Rayman (USA).cue: not enough free space', out)
            self.assertIn('skip Rayman/Rayman (USA).cue: 51 files; the core needs the disc as one .bin (not converted)', out)
            self.assertIn('  joined Legend (Disc 2)/Legend (USA) (Disc 2).cue', out)   # small enough to fit
            rayman = card/'Assets/playstation/common/Rayman'
            originals = {p.name: p.read_bytes() for p in rayman.iterdir()}
            self.assertEqual([p for p in card.rglob('*.part')], [])
            self.assertLessEqual(used(card), limit - RESERVE)
            out = self.plugin(card, REPLACE, card_bytes=limit)   # no folder on the computer
            self.assertIn('could not join Rayman/Rayman (USA).cue: not enough free space', out)
            self.assertEqual({p.name: p.read_bytes() for p in rayman.iterdir()}, originals)
            out = self.plugin(card, REPLACE, host=host, card_bytes=limit)
            self.assertIn('the SD card is short of space: converting on the computer first', out)
            self.assertIn('wrote Rayman (USA).json (51 tracks, one file)', out)
            self.assertLessEqual(used(card), limit - RESERVE)
            self.assertEqual(list(host.rglob('*')), [host/'PlayStation conversions'])   # temp copy gone

    def test_stopped_replace_through_the_computer_is_finished_next_run(self):
        # Replace on a full card converts in the computer's folder, removes the
        # originals, then copies back. Stopped after each step (updater closed,
        # card pulled), the next run finishes it: the card ends up as after an
        # unbroken run.
        digest = lambda card: {n: hashlib.sha1(d).hexdigest() for n, d in tree(card).items()}
        with TemporaryDirectory() as tmp:
            whole, host = Path(tmp)/'whole', Path(tmp)/'computer'
            one_file_card(whole)
            limit = used(whole) + RESERVE + (1 << 20)
            cards = {}
            for step in ('journal', 'remove', 'copied'):
                card = cards[step] = Path(tmp)/step
                shutil.copytree(whole, card)
                shutil.rmtree(host, ignore_errors=True); host.mkdir()
                self.plugin(card, REPLACE, host=host, card_bytes=limit, stop_at=step)
                self.assertTrue(list(host.rglob('journal.json')), step)
                out = self.plugin(card, REPLACE, host=host, card_bytes=limit)
                self.assertIn('that an earlier run left part-way', out, step)
                self.assertEqual(list(host.rglob('*')), [host/'PlayStation conversions'], step)
            shutil.rmtree(host); host.mkdir()
            self.plugin(whole, REPLACE, host=host, card_bytes=limit)
            for step, card in cards.items():
                self.assertEqual(digest(card), digest(whole), step)

    def test_menus(self):
        with TemporaryDirectory() as tmp:
            card = Path(tmp)
            make_card(card)
            other = card/'Cores/Someone.PSX'   # another developer's core: no slots 100 and 101+
            other.mkdir()
            (other/'core.json').write_text(json.dumps({'core': {'metadata': {'platform_ids': ['psx']}}}))
            (other/'data.json').write_text(json.dumps({'data': {'data_slots': [{'id': 0, 'extensions': ['cue', 'bin']}]}}))
            shutil.copytree(card/'Cores'/CORE, card/'Cores/GuardianSoul.PSXTest')
            (card/'Cores/Someone.N64').mkdir()
            (card/'Cores/Someone.N64/core.json').write_text(json.dumps({'core': {'metadata': {'platform_ids': ['n64']}}}))
            out = self.plugin(card, 'Exit')
            self.assertIn("Not listed: Someone.PSX (another developer's PlayStation core", out)
            self.assertIn(f'choices: GuardianSoul.PSXTest | {CORE} | Exit', out)
            self.assertIn('[Which PlayStation core are the game files for?: Exit]', out)
            self.assertFalse((card/'Assets/playstation'/CORE/'Tekken 3 (USA).json').exists())
            out = self.plugin(card, CORE, 'Exit')
            self.assertIn('[1 disc needs converting first', out)
            self.assertIn('choices: Convert them, keep the originals (adds 2 MB to the SD card) | '
                          'Convert them, replace the originals (adds 1 MB) | Skip them, generate the other game files | Check the BIOS | Exit', out)
            self.plugin(card, CORE, SKIP)
            self.assertTrue((card/'Assets/playstation'/CORE/'Tekken 3 (USA).json').is_file())
            self.assertFalse((card/'Assets/playstation/common/Merged/Game').exists())
        with TemporaryDirectory() as tmp:
            self.assertIn('No PlayStation core found', self.plugin(Path(tmp)))

    def test_another_developers_core(self):
        # Only another developer's PlayStation core (its games in Assets/psx/common): say so, don't skip every disc.
        with TemporaryDirectory() as tmp:
            card = Path(tmp)
            core = card/'Cores/akoaysigod.PSX'
            core.mkdir(parents=True)
            (core/'core.json').write_text(json.dumps({'core': {'metadata': {'platform_ids': ['psx']}}}))
            (core/'data.json').write_text(json.dumps({'data': {'data_slots': [{'id': 0, 'extensions': ['cue']}, {'id': 1}]}}))
            one_track(card/'Assets/psx/common/Tekken 3 (USA)', 'Tekken 3 (USA)')
            out = self.plugin(card)
            self.assertIn("akoaysigod.PSX is another developer's PlayStation core. This plugin makes game files only for the "
                          'GuardianSoul.PlayStation core, which is not on this SD card.', out)
            self.assertNotIn('skip', out)
            self.assertFalse((card/'Assets/psx/akoaysigod.PSX').exists())

    def test_games_in_the_other_platform_folder(self):
        with TemporaryDirectory() as tmp:
            card = Path(tmp)
            make_card(card)
            shutil.move(card/'Assets/playstation/common', card/'Assets/psx/common')
            (card/'Assets/playstation/common').mkdir()
            out = self.plugin(card, MAKE)
            self.assertIn(f'Your games are in Assets/psx/common, but {CORE} looks for them in Assets/playstation/common: '
                          "move each game's folder there", out)
            self.assertIn('No games found in Assets/playstation/common', out)
            shutil.rmtree(card/'Assets/playstation/common')   # no common at all: only the move line, then the BIOS check
            out = self.plugin(card, MAKE)
            self.assertIn('Your games are in Assets/psx/common', out)
            self.assertNotIn('Stopped', out)
            self.assertIn('Checking the BIOS', out)

    def bios_image(self, version=b'System ROM Version 4.1 12/16/97 A', size=524288):
        data = bytearray(size)
        data[0x7ff32:0x7ff32 + len(version)] = version
        return bytes(data)

    def test_bios_check(self):
        with TemporaryDirectory() as tmp:
            card = Path(tmp)
            make_card(card)
            common = card/'Assets/playstation/common'
            out = self.plugin(card, 'Check the BIOS')
            self.assertIn('No BIOS found: the core can\'t start games without one. Copy your PlayStation BIOS to '
                          'Assets/playstation/common/ as scph5501.bin (USA), scph5500.bin (Japan) or scph5502.bin (Europe)', out)
            self.assertFalse((card/'Assets/playstation'/CORE/'Tekken 3 (USA).json').exists())   # the check alone writes nothing
            # A BIOS under another name, in the other platform's folder: where it should go.
            (card/'Assets/psx/common').mkdir(parents=True)
            (card/'Assets/psx/common/PSX BIOS.bin').write_bytes(self.bios_image(b'System ROM Version 4.1 12/16/97 E'))
            self.assertIn("Assets/psx/common/PSX BIOS.bin is a Europe PlayStation BIOS, but the core doesn't look for it there: "
                          'copy it to Assets/playstation/common/ as scph5502.bin', self.plugin(card, 'Check the BIOS'))
            # Wrong size, not a BIOS, and a good one (any case).
            (common/'scph1001.bin').write_bytes(bytes(1000))
            (common/'scph5500.bin').write_bytes(bytes(524288))
            (common/'SCPH5501.BIN').write_bytes(self.bios_image())
            (common/'Odd').mkdir()
            (common/'Odd/Odd.bin').write_bytes(bytes(1000))
            out = self.plugin(card, SKIP)
            self.assertIn('BIOS SCPH5501.BIN: USA (System ROM Version 4.1 12/16/97 A), OK', out)
            self.assertIn('BIOS scph1001.bin is 1 KB, but a PlayStation BIOS is exactly 512 KB', out)
            self.assertIn('BIOS scph5500.bin is not a PlayStation BIOS', out)
            self.assertNotIn('copy it to', out)
            # A BIOS in common is not a disc: no "skip" line for it, no .cue.
            self.assertNotIn('skip SCPH5501', out)
            self.assertNotIn('skip scph1001', out)   # named like a BIOS: the BIOS check reports it
            self.assertIn('skip Odd/Odd.bin: it is not a raw CD image (its size is not whole 2352-byte sectors)', out)
            self.assertFalse((common/'SCPH5501.cue').exists())

    def test_short_log_for_pocket_sync(self):
        """Without "verbose" (as Pocket Sync and pupdate run it) the log stays
        short: Pocket Sync's log stops following a burst that overflows its
        window and its "can be closed" line can drop below it, so a large
        library looked hung. No line per game file, progress at 100, 200,
        400, ... game files, at most 4 skip lines and a count of the rest,
        then the summary, the BIOS check and a line saying it has finished."""
        for broken in (0, 80):
            with self.subTest(broken=broken), TemporaryDirectory() as tmp:
                card = Path(tmp)
                make_card(card)
                common = card/'Assets/playstation/common'
                for k in range(230):
                    one_track(common/f'Library {k:03d} (USA)', f'Library {k:03d} (USA)')
                for k in range(broken):
                    one_track(common/f'Missing {k:02d}', f'Missing {k:02d}')
                    (common/f'Missing {k:02d}/Missing {k:02d}.bin').unlink()
                out = self.plugin(card, SKIP, verbose=False)
                lines = [l for l in out.splitlines() if not l.startswith(('  ', '['))]
                self.assertNotIn('wrote Library 000 (USA).json', out)
                self.assertNotIn('shared card ', out)
                self.assertIn('100 game files so far ...', lines)
                self.assertIn('200 game files so far ...', lines)
                self.assertNotIn('400 game files so far ...', lines)
                self.assertEqual(len([l for l in lines if l.startswith(('skip ', 'warning: ', 'wrote '))]), 4)
                summary = next(i for i, l in enumerate(lines) if ' game files in Assets/playstation/' + CORE in l)
                self.assertRegex(lines[summary - 1], r'^\.\.\. and \d+ more like these: the report file named below lists them all\.$')
                self.assertEqual(lines[-2:], [f'Every line, with each game file: Assets/playstation/{CORE}/Game files report.txt',
                                              'Finished. You can close this window.'])
                self.assertLessEqual(len(lines), 17)
                self.assertTrue((card/'Assets/playstation'/CORE/'Library 229 (USA).json').exists())
                # Nothing is lost: the report file has every line the script prints.
                report = (card/'Assets/playstation'/CORE/'Game files report.txt').read_text().splitlines()
                for line in ('wrote Library 000 (USA).json (1 track)', 'wrote Library 229 (USA).json (1 track)'):
                    self.assertIn(line, report)
                self.assertTrue(any(l.startswith('shared card ') for l in report))
                self.assertEqual(len([l for l in report if l.startswith('skip Missing')]), broken)
                self.assertFalse(any(l.startswith('... and') for l in report))
                self.assertFalse(any(l.startswith('Every line') for l in report))

    def test_report_file_cannot_be_saved(self):
        """If the report file can't be written, the window says so and shows
        the skip lines it left out, so nothing is lost."""
        with TemporaryDirectory() as tmp:
            card = Path(tmp)
            make_card(card)
            common = card/'Assets/playstation/common'
            for k in range(8):
                one_track(common/f'Missing {k:02d}', f'Missing {k:02d}')
                (common/f'Missing {k:02d}/Missing {k:02d}.bin').unlink()
            (card/'Assets/playstation'/CORE/'Game files report.txt').mkdir(parents=True)
            out = self.plugin(card, SKIP, verbose=False)
            self.assertIn(f'Could not save the report file Assets/playstation/{CORE}/Game files report.txt', out)
            self.assertNotIn('Every line, with each game file', out)
            for k in range(8):
                self.assertIn(f'skip Missing {k:02d}/', out)
            self.assertEqual(out.splitlines()[-1], 'Finished. You can close this window.')

    def test_non_ascii_names_elsewhere(self):
        """Another core's files with non-ASCII letters near the end of their
        names ("ポケモン.gb") don't stop the BIOS check that ends every run."""
        with TemporaryDirectory() as tmp:
            card = Path(tmp)
            make_card(card)
            (card/'Assets/gb/common').mkdir(parents=True)
            (card/'Assets/gb/common/ポケモン.gb').write_bytes(bytes(100))
            (card/'Assets/gb/common/ゲーム').write_bytes(bytes(100))
            (card/'Assets/playstation/common/メモ.md').write_bytes(bytes(100))
            out = self.plugin(card, SKIP)
            self.assertIn('Checking the BIOS', out)
            self.assertIn('No BIOS found', out)


if __name__ == '__main__':
    unittest.main()
