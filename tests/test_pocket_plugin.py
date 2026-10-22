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
from tests.test_sd_card_page import CORE, CUE_READER, cue_reader_card, make_card, tree, with_audio

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
    end = next(i for i, l in enumerate(lines) if ' instance files in ' in l)
    return lines[start+1:end]


def one_file_card(card, chds=None):
    make_card(card)
    common = card/'Assets/playstation/common'
    with_audio(common/'Rayman', 'Rayman (USA)', 50)
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

    def plugin(self, card, *answers, host=None, card_bytes=None, stop_at=None):
        cmd = [sys.executable, str(HOST), str(WASM), str(card)] + [a for x in answers for a in ('--answer', x)]
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
            ff9 = json.loads((games/'Final Fantasy IX (v1.1).json').read_text())['instance']
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
            self.assertIn('skip Gone/Gone (USA).cue: Gone (USA) (Track 2).bin: file missing', out)
            self.assertIn('skip Stray/Stray (USA).cue: Stray (USA) (Track 1).bin: file missing', out)
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
            self.assertIn('wrote Final Fantasy IX (v1.1).json (4 discs, playlist Final Fantasy IX (USA)/Final Fantasy IX (v1.1).m3u)', out)
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
            other = card/'Cores/Someone.PSX'
            other.mkdir()
            (other/'core.json').write_text(json.dumps({'core': {'metadata': {'platform_ids': ['psx']}}}))
            (card/'Cores/Someone.N64').mkdir()
            (card/'Cores/Someone.N64/core.json').write_text(json.dumps({'core': {'metadata': {'platform_ids': ['n64']}}}))
            out = self.plugin(card, 'Exit')
            self.assertIn('[Which PlayStation core are the game files for?: Exit]', out)
            self.assertFalse((card/'Assets/playstation'/CORE/'Tekken 3 (USA).json').exists())
            out = self.plugin(card, CORE, 'Exit')
            self.assertIn('[1 disc needs converting first', out)
            self.assertIn('choices: Convert them, keep the originals (adds 2 MB to the SD card) | '
                          'Convert them, replace the originals (adds 1 MB) | Skip them, generate the other game files | Exit', out)
            self.plugin(card, CORE, SKIP)
            self.assertTrue((card/'Assets/playstation'/CORE/'Tekken 3 (USA).json').is_file())
            self.assertFalse((card/'Assets/playstation/common/Merged/Game').exists())
        with TemporaryDirectory() as tmp:
            self.assertIn('No PlayStation core found', self.plugin(Path(tmp)))


if __name__ == '__main__':
    unittest.main()
