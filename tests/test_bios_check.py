"""Chip32 BIOS check loader (support/chip32/psx_bios_check.asm)."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))

from chip32_vm import Chip32  # noqa: E402

BIOS, CARD = 0x20, 0x10
REGION, BIOS_REGION, ISPAL = 0x1100009c, 0x11000020, 0x11000154
SLOT_TABLE, HOST = 0xf8002000, 0xf8000000
SIZE_BLOCK, SIZE_MAGIC = 0x2c0001c0, 0x53495a45   # psx_cue_mount RAM 0x21C0
_built = {}


def loader(**options):
    """Assemble once per option set; skip when bass cannot be built here."""
    key = repr(sorted(options.items()))
    if key not in _built:
        from build_chip32_loader import build
        out = Path(tempfile.mkdtemp())/'loader.bin'
        try:
            build(out, **options)
        except (OSError, subprocess.CalledProcessError, SystemExit) as error:
            raise unittest.SkipTest(f'Chip32 assembler unavailable: {error}')
        _built[key] = out.read_bytes()
    return _built[key]


class BiosCheckTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def bios(self, version=b'System ROM Version 3.0 11/18/96 A', size=524288, at=0x7ff32, name='scph5501.bin'):
        data = bytearray(size)
        data[at:at+len(version)] = version
        folder = self.dir/str(len(list(self.dir.iterdir()))); folder.mkdir()
        (folder/name).write_bytes(data)
        return folder/name

    def run_loader(self, bios, card=None, program=None, regs=None, extra=None):
        vm = Chip32(program or loader(memcard_slot=CARD), {BIOS: bios, CARD: card, **(extra or {})}, regs=regs)
        vm.run()
        return vm, ''.join(vm.output)

    def run_names(self, files, card=None):
        """A --bios-names loader; files maps BIOS names to paths (renamed to match)."""
        files = {n: p.rename(p.with_name(n)) for n, p in files.items() if p}
        from build_chip32_loader import BIOS_NAMES
        slots = tuple((0x20+k, n) for k, n in enumerate(BIOS_NAMES))
        return self.run_loader(None, card, loader(slots=slots, memcard_slot=CARD),
                               extra={0x20+k: files.get(n) for k, n in enumerate(BIOS_NAMES)})

    def assert_started(self, vm, loads, region=0):
        # The region goes in the low and high byte (pmpw arrives byte-swapped).
        ispal = 0x01000001 if region == 2 else 0   # Europe: PAL, bit 0 in both byte lanes
        region = region | region << 24
        self.assertEqual(vm.events[0], ('core', 0))
        self.assertEqual([e[1] for e in vm.events if e[0] == 'loadf'], loads)
        self.assertEqual([e[1:] for e in vm.events if e[0] == 'pmpw' and e[1] < HOST and not SIZE_BLOCK <= e[1] < SIZE_BLOCK + 64],
                         [(REGION, region), (ISPAL, ispal), (BIOS_REGION, 0)])
        self.assertLess(vm.events.index(('pmpw', REGION, region)), vm.events.index(('host', 0x4002, 0x4002)))
        self.assertEqual(vm.events[-2:], [('host', 0x4002, 0x4002), ('exit', 0)])
        self.assertEqual(vm.output, [])
        self.assertEqual(vm.r[13] & 1, 1)

    def assert_stopped(self, vm, text, *parts):
        self.assertEqual(vm.result, 1)
        self.assertFalse([e for e in vm.events if e[0] in ('loadf', 'host', 'fault')])
        for part in parts: self.assertIn(part, text)

    def test_usa_bios_and_card(self):
        card = self.dir/'game.sav'; card.write_bytes(bytes(131072))
        self.assert_started(self.run_loader(self.bios(), card)[0], [BIOS, CARD])

    def test_other_usa_bios_and_no_card(self):
        vm, _ = self.run_loader(self.bios(b'System ROM Version 4.1 12/16/97 A'))
        self.assert_started(vm, [BIOS])

    def test_without_card_slot(self):
        vm, _ = self.run_loader(self.bios(), program=loader())
        self.assert_started(vm, [BIOS])

    def test_missing(self):
        vm, text = self.run_loader(None)
        self.assertEqual(text, 'Missing BIOS scph5501.bin')
        self.assert_stopped(vm, text)

    def test_wrong_size(self):
        vm, text = self.run_loader(self.bios(size=262144))
        self.assertEqual(text, 'Bad BIOS scph5501.bin')
        self.assert_stopped(vm, text)

    def test_not_a_bios(self):
        for version in (b'', b'System ROM Versio', b'Something else 3.0 11/18/96 A'):
            with self.subTest(version=version):
                vm, text = self.run_loader(self.bios(version))
                self.assertEqual(text, 'Bad BIOS scph5501.bin')
                self.assert_stopped(vm, text)

    def test_other_region_sets_the_cd_region(self):
        for letter, region in ((b'J', 1), (b'E', 2)):
            with self.subTest(region=region):
                vm, _ = self.run_loader(self.bios(b'System ROM Version 3.0 11/18/96 '+letter))
                self.assert_started(vm, [BIOS], region)

    def test_file_name_and_platform_follow_the_package(self):
        vm, text = self.run_loader(None, extra={0x21: None}, program=loader(slots=((0x21, 'bios.bin'),), platform='psxearly'))
        self.assertEqual(text, 'Missing BIOS bios.bin')
        self.assert_stopped(vm, text)

    def test_names_prefer_usa_then_japan_then_europe(self):
        usa, jp, eu = (b'System ROM Version 4.1 12/16/97 '+c for c in (b'A', b'J', b'E'))
        vm, _ = self.run_names({'scph1001.bin': self.bios(usa), 'scph5500.bin': self.bios(jp)})
        self.assert_started(vm, [0x22])
        vm, _ = self.run_names({'scph5502.bin': self.bios(eu), 'scph7000.bin': self.bios(jp)})
        self.assert_started(vm, [0x27], 1)
        vm, _ = self.run_names({'scph102.bin': self.bios(eu)})
        self.assert_started(vm, [0x2f], 2)
        vm, _ = self.run_names({'scph5501.bin': self.bios(size=5), 'scph101.bin': self.bios(usa)})
        self.assert_started(vm, [0x25])

    def disc(self, licence):
        """A raw (2352-byte sector) disc image with this licence region word."""
        data = bytearray(2352*6)
        text = b'          Licensed  by          Sony Computer Entertainment ' + licence
        data[4*2352+24:4*2352+24+len(text)] = text
        path = self.dir/f'disc{len(list(self.dir.iterdir()))}.bin'; path.write_bytes(data)
        return path

    def run_region(self, files, disc, cue=None):
        """A --bios-names loader with the package's cue (100) and disc image (101) slots."""
        files = {n: p.rename(p.with_name(n)) for n, p in files.items() if p}
        from build_chip32_loader import BIOS_NAMES, DISC_SLOT, CUE_SLOT
        slots = tuple((0x20+k, n) for k, n in enumerate(BIOS_NAMES))
        table = (0, CARD, 0x20, CUE_SLOT, DISC_SLOT) + tuple(0x21+k for k in range(len(BIOS_NAMES)-1))
        return self.run_loader(None, None, loader(slots=slots, memcard_slot=CARD, table=table),
                               extra={**{0x20+k: files.get(n) for k, n in enumerate(BIOS_NAMES)},
                                      CUE_SLOT: cue, DISC_SLOT: disc})

    def cue(self, name):
        path = self.dir/name; path.write_text('FILE "track.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
        return path

    def test_game_file_cue_name_picks_its_bios(self):
        # a game file's disc is its cue (slot 100), whose tracks only the core opens
        usa, jp, eu = (b'System ROM Version 4.1 12/16/97 '+c for c in (b'A', b'J', b'E'))
        for name, slot, region in (('Ape Escape (Europe) (En,Fr,De,Es,It).cue', 0x2a, 2),
                                   ('Ridge Racer (Japan).cue', 0x26, 1), ('Wipeout (UK).cue', 0x2a, 2),
                                   ('Crash Bandicoot (USA).cue', 0x20, 0), ('No Region.cue', 0x20, 0)):
            with self.subTest(name=name):
                files = {'scph5501.bin': self.bios(usa), 'scph5500.bin': self.bios(jp), 'scph5502.bin': self.bios(eu)}
                vm, _ = self.run_region(files, None, self.cue(name))
                self.assert_started(vm, [slot], region)
        # the licence text wins over the name
        files = {'scph5501.bin': self.bios(usa), 'scph5502.bin': self.bios(eu)}
        vm, _ = self.run_region(files, self.disc(b'Amer  ica '), self.cue('Game (Europe).cue'))
        self.assert_started(vm, [0x20], 0)

    def test_disc_region_picks_its_bios(self):
        usa, jp, eu = (b'System ROM Version 4.1 12/16/97 '+c for c in (b'A', b'J', b'E'))
        for licence, slot, region in ((b'Euro pe   ', 0x2a, 2), (b'Inc.', 0x26, 1), (b'Amer  ica ', 0x20, 0)):
            with self.subTest(licence=licence):
                files = {'scph5501.bin': self.bios(usa), 'scph5500.bin': self.bios(jp), 'scph5502.bin': self.bios(eu)}
                vm, _ = self.run_region(files, self.disc(licence))
                self.assert_started(vm, [slot], region)
                self.assertIsNone(vm.file)

    def test_disc_region_falls_back_to_slot_order(self):
        usa, eu = (b'System ROM Version 4.1 12/16/97 '+c for c in (b'A', b'E'))
        # no BIOS of the disc's region, an unreadable licence, no disc file
        for files, disc in (({'scph5501.bin': self.bios(usa), 'scph5502.bin': self.bios(eu)}, self.disc(b'Inc.')),
                            ({'scph5501.bin': self.bios(usa), 'scph5502.bin': self.bios(eu)}, self.disc(b'????')),
                            ({'scph5501.bin': self.bios(usa), 'scph5502.bin': self.bios(eu)}, None)):
            with self.subTest(disc=disc):
                vm, _ = self.run_region(files, disc)
                self.assert_started(vm, [0x20], 0)
        # a bad BIOS of the disc's region is skipped, not reported
        vm, text = self.run_region({'scph5502.bin': self.bios(size=1000), 'scph5501.bin': self.bios(usa)},
                                   self.disc(b'Euro pe   '))
        self.assert_started(vm, [0x20], 0)
        # a short disc image (no sector 4)
        short = self.dir/'short.bin'; short.write_bytes(bytes(2352))
        vm, _ = self.run_region({'scph5502.bin': self.bios(eu)}, short)
        self.assert_started(vm, [0x2a], 2)

    def test_names_missing_gives_examples(self):
        vm, text = self.run_names({})
        self.assertEqual(text, 'Missing BIOS scph5501.bin')
        self.assert_stopped(vm, text)

    def test_names_report_the_first_unusable_file(self):
        vm, text = self.run_names({'scph7001.bin': self.bios(size=1000), 'scph5502.bin': self.bios(b'nothing')})
        self.assertEqual(text, 'Bad BIOS scph7001.bin')
        self.assert_stopped(vm, text)
        self.assertNotIn('scph5502', text)

    def test_slot_table(self):
        disc = self.dir/'disc.bin'; disc.write_bytes(bytes(2352*3))
        game = self.dir/'game.json'; game.write_bytes(b'{}')
        table = (0, CARD, 100, BIOS, 101, 102)
        for complete in (False, True):
            with self.subTest(complete=complete):
                vm, _ = self.run_loader(self.bios(), program=loader(memcard_slot=CARD, table=table, send_complete=complete),
                                        extra={0: game, 101: disc})
                self.assert_started(vm, [BIOS])
                writes = [e[1:] for e in vm.events if e[0] == 'pmpw' and e[1] >= HOST]
                block = [e[1:] for e in vm.events if e[0] == 'pmpw' and SIZE_BLOCK <= e[1] < SIZE_BLOCK + 64]
                # slots 0 and 101 (and 100, 102: no file) for the cue reader, then the magic
                self.assertEqual(block, [(SIZE_BLOCK+4, 2), (SIZE_BLOCK+12, 0), (SIZE_BLOCK+16, 2352*3), (SIZE_BLOCK+20, 0),
                                         (SIZE_BLOCK, SIZE_MAGIC)])
                self.assertLess(vm.events.index(('pmpw', SIZE_BLOCK, SIZE_MAGIC)), vm.events.index(('host', 0x4002, 0x4002)))
                # the BIOS slot's size is written once the BIOS pass opens it
                expected = [w for k, (slot, size) in enumerate(zip(table, (2, 0, 0, 0, 2352*3, 0)))
                            for w in ((SLOT_TABLE+8*k, slot), (SLOT_TABLE+8*k+4, size))]
                self.assertEqual(writes, expected + [(SLOT_TABLE+8*3+4, 524288)] + ([(HOST, 0x434d0082)] if complete else []))
                self.assertLess(vm.events.index(('pmpw',)+writes[-1]), vm.events.index(('host', 0x4002, 0x4002)))
                self.assertIsNone(vm.file)

    def opens(self, vm):
        return [e[1] for e in vm.events if e[0] == 'open']

    def test_each_file_opened_once(self):
        # The Pocket's loading bar appears with the BIOS loadf: before it,
        # only the slots that have to be opened are, each once.
        usa, eu = (b'System ROM Version 4.1 12/16/97 '+c for c in (b'A', b'E'))
        card = self.dir/'game.sav'; card.write_bytes(bytes(131072))
        from build_chip32_loader import BIOS_NAMES, DISC_SLOT, CUE_SLOT
        slots = tuple((0x20+k, n) for k, n in enumerate(BIOS_NAMES))
        table = (0, CARD, 0x20, CUE_SLOT, DISC_SLOT) + tuple(0x21+k for k in range(len(BIOS_NAMES)-1))
        program = loader(slots=slots, memcard_slot=CARD, table=table)
        def run(files, disc=None, cue=None):
            files = {n: p.rename(p.with_name(n)) for n, p in files.items() if p}
            vm = Chip32(program, {**{0x20+k: files.get(n) for k, n in enumerate(BIOS_NAMES)},
                                  CARD: card, CUE_SLOT: cue, DISC_SLOT: disc},
                     query_declared=[0x20+k for k in range(len(BIOS_NAMES))])
            vm.run()
            return vm
        # a USA game and scph5501: the card, the disc, then that one BIOS
        vm = run({'scph5501.bin': self.bios(usa)}, self.disc(b'Amer  ica '))
        self.assert_started(vm, [0x20, CARD])
        self.assertEqual(self.opens(vm), [CARD, DISC_SLOT, 0x20])
        # a European cue: the European names first, the USA BIOS untouched
        vm = run({'scph5501.bin': self.bios(usa), 'scph1002.bin': self.bios(eu)}, None, self.cue('Game (Europe).cue'))
        self.assert_started(vm, [0x2c, CARD], 2)
        self.assertEqual(self.opens(vm), [CARD, CUE_SLOT, 0x2a, 0x2b, 0x2c])
        # no BIOS of its region: every name once, then the USA BIOS
        vm = run({'scph5501.bin': self.bios(usa)}, None, self.cue('Game (Europe).cue'))
        self.assert_started(vm, [0x20, CARD])
        opened = self.opens(vm)
        self.assertEqual(sorted(opened), sorted(set(opened)))
        self.assertEqual(len(opened), 2 + len(BIOS_NAMES))
        self.assertEqual(len([e for e in vm.events if e[0] == 'fault']), len(BIOS_NAMES) - 1)

    def test_card_outside_the_table_is_opened(self):
        card = self.dir/'game.sav'; card.write_bytes(bytes(131072))
        vm, _ = self.run_loader(self.bios(), card, program=loader(memcard_slot=CARD, table=(0, 0x20)))
        self.assert_started(vm, [BIOS, CARD])
        vm, _ = self.run_loader(self.bios(), None, program=loader(memcard_slot=CARD, table=(0, CARD, 0x20)))
        self.assert_started(vm, [BIOS])

    def test_later_run_exits(self):
        regs = [0]*16; regs[13] = 1
        vm, _ = self.run_loader(None, regs=regs)
        self.assertEqual(vm.events, [('exit', 0)])

    def test_messages_fit_the_screen(self):
        for case in (None, self.bios(size=1), self.bios(b''), self.bios(b'System ROM Version 3.0 11/18/96 X')):
            _, text = self.run_loader(case)
            self.assertLessEqual(len(text), 26, text)
            self.assertNotIn('\n', text)
            _, text = self.run_names({'scph5501.bin': case})
            self.assertLessEqual(len(text), 26, text)
            self.assertNotIn('\n', text)



class ApplyTest(unittest.TestCase):
    """build_chip32_loader.apply on a packaged core (the 0.5.2 slot layout)."""

    def test_apply(self):
        import json
        import build_chip32_loader
        loader()                                    # skip without bass
        core = Path(tempfile.mkdtemp())
        slots = [{'name': 'Game', 'id': 0, 'required': True, 'parameters': '0x12', 'extensions': ['json']},
                 {'name': 'Memory Card 1', 'id': 16, 'required': False, 'parameters': '0xA4', 'nonvolatile': True,
                  'extensions': ['sav'], 'size_maximum': '0x20000', 'address': '0x20000000'},
                 {'name': 'Disc (cue)', 'id': 100, 'required': False, 'parameters': '0x08', 'deferload': True,
                  'extensions': ['cue'], 'size_maximum': '0x4000'},
                 {'name': 'BIOS', 'id': '0x20', 'required': True, 'parameters': '0x008', 'deferload': False,
                  'filename': 'scph5501.bin', 'extensions': ['bin'], 'size_exact': 524288, 'address': '0x00000000'},
                 {'name': 'Track 1', 'id': 101, 'required': False, 'parameters': '0x08', 'deferload': True,
                  'extensions': ['bin'], 'size_maximum': '0x40000000'}]
        (core/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': slots}}))
        (core/'core.json').write_text(json.dumps({'core': {'framework': {'version_required': '1.1'}}}))
        build_chip32_loader.apply(core, 'playstation')
        self.assertEqual(json.loads((core/'core.json').read_text())['core']['framework']['chip32_vm'], 'chip32.bin')
        bios = json.loads((core/'data.json').read_text())['data']['data_slots'][3]
        self.assertEqual((bios['required'], bios['filename']), (False, 'scph5501.bin'))
        self.assertEqual(sorted(p.name for p in core.iterdir()), ['chip32.bin', 'core.json', 'data.json'])
        self.assertEqual((core/'chip32.bin').read_bytes(), loader(memcard_slot=CARD, table=(0, 16, 100, 0x20, 101)))
        (core/'data.json').write_text(json.dumps({'data': {'magic': 'APF_VER_1', 'data_slots': slots}}))
        build_chip32_loader.apply(core, 'playstation', names=True)
        table = json.loads((core/'data.json').read_text())['data']['data_slots']
        self.assertEqual(table[:5], [dict(s, required=False) if s['id'] == '0x20' else s for s in slots])
        self.assertEqual([(s['id'], s['filename']) for s in table[5:]],
                         [(f'{0x20+k:#04x}', n) for k, n in enumerate(build_chip32_loader.BIOS_NAMES) if k])
        self.assertTrue(all(len(s['name']) <= 15 and not s['required'] for s in table[5:]))


if __name__ == '__main__': unittest.main()
