"""Save-handler ROM updates preserve fitted logic and replace every emitted ROM word."""
from pathlib import Path
import hashlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from update_save_fit import EXT_LANE, LANE, stage


def digest(data):
    return hashlib.sha256(data).hexdigest()


def rom_text(words):
    return ''.join(f'{w:010x}\n' for w in words).encode()


class SaveFit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name) / 'fit'
        (self.p / 'db').mkdir(parents=True)
        (self.p / 'save-states').mkdir()
        self.old = [(k * 0x9E3779B1 + 7) & (2**40 - 1) for k in range(1024)]
        self.new = [(k * 0x7F4A7C15 + 3) & (2**40 - 1) for k in range(1024)]
        a, b = rom_text(self.old), rom_text(self.new)
        (self.p / LANE).write_bytes(a)
        self.rom = Path(self.tmp.name) / 'new.hex'
        self.rom.write_bytes(b)
        self.req = dict(before_sha256=digest(a), after_sha256=digest(b))
        text = ('-- begin_signature\n-- psx_save_exp1(ROM_HEX\n'
                f'-- "/somewhere/else/cl-x-s12/{LANE}"\n-- SAVE_BASE\n-- 32\'b0)\n-- end_signature\n'
                'WIDTH=40;\nDEPTH=1024;\n\nADDRESS_RADIX=UNS;\nDATA_RADIX=BIN;\n\nCONTENT BEGIN\n' +
                ''.join(f'\t{k} :\t{self.old[k]:040b};\n' for k in reversed(range(1024))) + 'END;\n')
        (self.p / 'db/apf_top.ram0_psx_save_exp1_1.hdl.mif').write_text(text)
        (self.p / 'db/other.hdl.mif').write_text('-- psx_cue_ram_lane(FIRMWARE\nWIDTH=8;\n')
        (self.p / 'db/routing').write_bytes(b'placed routing is unchanged')

    def test_every_word_matches_an_independently_decoded_mif(self):
        before = (self.p / 'db/routing').read_bytes()
        rows = stage(self.p, self.rom, self.req)
        self.assertEqual(len(rows), 1)
        body = (self.p / 'db' / rows[0]['mif']).read_text().split('CONTENT BEGIN\n')[1].split('END;')[0]
        actual = {int(l.split(':')[0]): int(l.split(':')[1].strip(' \t;'), 2) for l in body.splitlines()}
        self.assertEqual([actual[k] for k in range(1024)], self.new)
        self.assertEqual((self.p / LANE).read_bytes(), self.rom.read_bytes())
        self.assertEqual((self.p / 'db/routing').read_bytes(), before)

    def assertRefused(self, pattern):
        before = {p: p.read_bytes() for p in self.p.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError, pattern):
            stage(self.p, self.rom, self.req)
        self.assertEqual({p: p.read_bytes() for p in self.p.rglob('*') if p.is_file()}, before)

    def test_stale_new_firmware_refused(self):
        self.rom.write_bytes(rom_text([0] * 1024))
        self.assertRefused('hash mismatch')

    def test_identical_rom_refresh_checks_every_word_and_keeps_routing(self):
        self.rom.write_bytes(rom_text(self.old))
        self.req['after_sha256'] = self.req['before_sha256']
        before = (self.p / 'db/routing').read_bytes()
        rows = stage(self.p, self.rom, self.req)
        body = (self.p / 'db' / rows[0]['mif']).read_text().split('CONTENT BEGIN\n')[1].split('END;')[0]
        actual = {int(l.split(':')[0]): int(l.split(':')[1].strip(' \t;'), 2) for l in body.splitlines()}
        self.assertEqual([actual[k] for k in range(1024)], self.old)
        self.assertEqual(rows[0]['before_sha256'], rows[0]['after_sha256'])
        self.assertEqual((self.p / 'db/routing').read_bytes(), before)

    def test_identical_rom_does_not_bypass_corrupt_fitted_mif_guard(self):
        self.rom.write_bytes(rom_text(self.old))
        self.req['after_sha256'] = self.req['before_sha256']
        self.test_corrupt_fitted_mif_refused_before_any_write()

    def test_corrupt_fitted_mif_refused_before_any_write(self):
        m = self.p / 'db/apf_top.ram0_psx_save_exp1_1.hdl.mif'
        s = m.read_text(); start = s.index('1023 :\t') + len('1023 :\t')
        m.write_text(s[:start] + ('1' if s[start] == '0' else '0') + s[start + 1:])
        self.assertRefused('differs from fitted')

    def test_duplicate_fitted_rom_refused(self):
        (self.p / 'db/duplicate.hdl.mif').write_bytes((self.p / 'db/apf_top.ram0_psx_save_exp1_1.hdl.mif').read_bytes())
        self.assertRefused('exactly one')

    def test_geometry_change_refused(self):
        m = self.p / 'db/apf_top.ram0_psx_save_exp1_1.hdl.mif'
        m.write_text(m.read_text().replace('DEPTH=1024;', 'DEPTH=2048;'))
        self.assertRefused('geometry')

    def test_short_new_rom_refused(self):
        b = rom_text(self.new[:1023]); self.rom.write_bytes(b); self.req['after_sha256'] = digest(b)
        self.assertRefused('1024 words')



class SaveFitWithExtension(SaveFit):
    """A fit with the extension ROM (prepare_save_rom_ext.py): both ROMs are updated together."""
    def setUp(self):
        super().setUp()
        self.old_ext = bytes(1024)
        self.new_ext = bytes((k * 37 + 11) & 255 for k in range(1024))
        ext = lambda data: ''.join(f'{b:02x}\n' for b in data).encode()
        (self.p / EXT_LANE).write_bytes(ext(self.old_ext))
        self.ext = Path(self.tmp.name) / 'new-ext.hex'
        self.ext.write_bytes(ext(self.new_ext))
        self.req['ext'] = dict(before_sha256=digest(ext(self.old_ext)), after_sha256=digest(ext(self.new_ext)))
        # as quartus_map writes them (#1248): the device's signature now lists EXT_HEX after ROM_HEX
        m = self.p / 'db/apf_top.ram0_psx_save_exp1_1.hdl.mif'
        m.write_text(m.read_text().replace('-- SAVE_BASE\n', f'-- EXT_HEX\n-- "/somewhere/else/cl-x-s12/{EXT_LANE}"\n-- SAVE_BASE\n'))
        self.ext_mif = self.p / 'db/apf_top.ram0_psx_save_rom_ext_2.hdl.mif'
        self.ext_mif.write_text('-- begin_signature\n-- psx_save_rom_ext(HEX\n'
                                f'-- "/somewhere/else/cl-x-s12/{EXT_LANE}")\n-- end_signature\n'
                                'WIDTH=8;\nDEPTH=1024;\n\nADDRESS_RADIX=UNS;\nDATA_RADIX=BIN;\n\nCONTENT BEGIN\n' +
                                ''.join(f'\t{k} :\t{self.old_ext[k]:08b};\n' for k in reversed(range(1024))) + 'END;\n')
        plain = stage
        globals()['stage'] = lambda project, rom, request: plain(project, rom, request, self.ext)
        self.addCleanup(globals().__setitem__, 'stage', plain)
        self.plain = plain

    def mif_bytes(self, name):
        body = (self.p / 'db' / name).read_text().split('CONTENT BEGIN\n')[1].split('END;')[0]
        rows = {int(l.split(':')[0]): int(l.split(':')[1].strip(' \t;'), 2) for l in body.splitlines()}
        return [rows[k] for k in range(1024)]

    def test_every_word_matches_an_independently_decoded_mif(self):
        self.test_both_roms_are_replaced()

    def test_both_roms_are_replaced(self):
        rows = self.plain(self.p, self.rom, self.req, self.ext)
        self.assertEqual([r['lane'] for r in rows], [LANE, EXT_LANE])
        self.assertEqual(self.mif_bytes(rows[0]['mif']), self.new)
        self.assertEqual(bytes(self.mif_bytes(rows[1]['mif'])), self.new_ext)
        self.assertEqual((self.p / EXT_LANE).read_bytes(), self.ext.read_bytes())
        self.assertEqual((self.p / LANE).read_bytes(), self.rom.read_bytes())
        self.assertEqual(rows[1]['words'], 1024)

    def test_extension_alone_changes(self):
        self.rom.write_bytes(rom_text(self.old)); self.req['after_sha256'] = self.req['before_sha256']
        rows = self.plain(self.p, self.rom, self.req, self.ext)
        self.assertEqual(self.mif_bytes(rows[0]['mif']), self.old)
        self.assertEqual(bytes(self.mif_bytes(rows[1]['mif'])), self.new_ext)
        self.assertEqual(rows[0]['before_sha256'], rows[0]['after_sha256'])
        self.assertNotEqual(rows[1]['before_sha256'], rows[1]['after_sha256'])

    def test_update_without_the_extension_refused(self):
        before = {p: p.read_bytes() for p in self.p.rglob('*') if p.is_file()}
        request = {k: v for k, v in self.req.items() if k != 'ext'}
        for args in ((self.p, self.rom, request), (self.p, self.rom, self.req), (self.p, self.rom, request, self.ext)):
            with self.assertRaisesRegex(ValueError, 'names both ROMs'):
                self.plain(*args)
        self.assertEqual({p: p.read_bytes() for p in self.p.rglob('*') if p.is_file()}, before)

    def test_fit_without_the_extension_refuses_one(self):
        (self.p / EXT_LANE).unlink(); self.ext_mif.unlink()
        with self.assertRaisesRegex(ValueError, 'no extension ROM'):
            self.plain(self.p, self.rom, self.req, self.ext)

    def test_corrupt_extension_mif_refused_before_any_write(self):
        s = self.ext_mif.read_text(); start = s.index('1023 :\t') + len('1023 :\t')
        self.ext_mif.write_text(s[:start] + ('1' if s[start] == '0' else '0') + s[start + 1:])
        self.assertRefused('differs from fitted')          # the routine ROM was valid: it is not written either

    def test_stale_new_extension_refused(self):
        self.ext.write_bytes(b'00\n' * 1024)
        self.assertRefused(r'hash mismatch \(extension ROM\)')

    def test_short_extension_refused(self):
        b = b'00\n' * 1023; self.ext.write_bytes(b); self.req['ext']['after_sha256'] = digest(b)
        self.assertRefused('1024 words of 8 bits')

    def test_missing_extension_mif_refused(self):
        self.ext_mif.unlink()
        self.assertRefused(r'exactly one fitted save ROM MIF \(extension ROM\)')


if __name__ == '__main__':
    unittest.main()
