"""Cue-only updates preserve fitted logic and replace every emitted ROM byte."""
from pathlib import Path
import hashlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from update_cue_fit import stage


def digest(data):
    return hashlib.sha256(data).hexdigest()


class CueFit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name) / 'fit'
        self.f = Path(self.tmp.name) / 'firmware'
        (self.p/'db').mkdir(parents=True)
        self.f.mkdir()
        self.req = {}
        self.expected = {}
        for i in range(4):
            name = f'psx_cue_fw{i}.hex'
            old = bytes((k*13+i) & 255 for k in range(4096))
            new = bytes((k*7+i*31) & 255 for k in range(4096))
            a = ''.join(f'{v:02x}\n' for v in old).encode()
            b = ''.join(f'{v:02x}\n' for v in new).encode()
            (self.p/name).write_bytes(a)
            (self.f/name).write_bytes(b)
            self.req[name] = dict(before_sha256=digest(a), after_sha256=digest(b))
            text = (f'-- psx_cue_ram_lane(FIRMWARE\n-- "{name}")\nWIDTH=8;\nDEPTH=4096;\n'
                    'ADDRESS_RADIX=UNS;\nDATA_RADIX=BIN;\nCONTENT BEGIN\n' +
                    ''.join(f'{k} : {old[k]:08b};\n' for k in reversed(range(4096))) + 'END;\n')
            (self.p/'db'/f'lane{i}.hdl.mif').write_text(text)
            self.expected[name] = new
        (self.p/'db/routing').write_bytes(b'placed routing is unchanged')

    def test_all_four_lanes_match_independently_decoded_mif(self):
        before = (self.p/'db/routing').read_bytes()
        rows = stage(self.p, self.f, self.req)
        self.assertEqual(len(rows), 4)
        for row in rows:
            text = (self.p/'db'/row['mif']).read_text()
            body = text.split('CONTENT BEGIN\n')[1].split('END;')[0]
            actual = {int(l.split(':')[0]): int(l.split(':')[1].strip(' ;'), 2)
                      for l in body.splitlines()}
            self.assertEqual(bytes(actual[k] for k in range(4096)), self.expected[row['lane']])
            self.assertEqual((self.p/row['lane']).read_bytes(), (self.f/row['lane']).read_bytes())
        self.assertEqual((self.p/'db/routing').read_bytes(), before)

    def assertRefused(self, pattern):
        before = {p: p.read_bytes() for p in self.p.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError, pattern):
            stage(self.p, self.f, self.req)
        self.assertEqual({p: p.read_bytes() for p in self.p.rglob('*') if p.is_file()}, before)

    def test_missing_lane_refused(self):
        del self.req['psx_cue_fw3.hex']
        self.assertRefused('All four')

    def test_stale_new_firmware_refused(self):
        (self.f/'psx_cue_fw3.hex').write_text('00\n'*4096)
        self.assertRefused('hash mismatch')

    def test_corrupt_fitted_mif_refused_before_any_write(self):
        m = self.p/'db/lane3.hdl.mif'
        # Plant a bit fault independently of how the update helper reads the table.
        s = m.read_text(); start = s.index('4095 : ') + len('4095 : ')
        m.write_text(s[:start] + ('1' if s[start]=='0' else '0') + s[start+1:])
        self.assertRefused('differs from fitted')

    def test_duplicate_fitted_lane_refused(self):
        (self.p/'db/duplicate.hdl.mif').write_bytes((self.p/'db/lane0.hdl.mif').read_bytes())
        self.assertRefused('exactly one')

    def test_out_of_range_fitted_address_refused(self):
        p = self.p/'db/lane3.hdl.mif'
        p.write_text(p.read_text().replace('4095 :', '4096 :'))
        self.assertRefused('row duplicate/range/format')

    def test_duplicate_fitted_address_refused(self):
        p = self.p/'db/lane3.hdl.mif'
        p.write_text(p.read_text().replace('4095 :', '4094 :'))
        self.assertRefused('row duplicate/range/format')

    def test_short_lane_refused(self):
        p = self.f/'psx_cue_fw3.hex';p.write_text('01\n'*4095)
        self.req[p.name]['after_sha256'] = digest(p.read_bytes())
        self.assertRefused('exactly 4096')


if __name__ == '__main__':
    unittest.main()
