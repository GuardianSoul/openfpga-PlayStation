import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import decode_perf_log  # noqa: E402


def video_log(records, slot):
    """A --video-log save as psx_perf_log writes it: header, then 9-word records."""
    words = [decode_perf_log.MAGIC, 1 << 30 | 1 << 29 | len(records), 0, 2 << 30 | 4 << 20 | 226 << 10 | slot]
    ring = [0] * (9 * 227)
    for i, (frame, beat, alt, w4, w5, w6, w7) in enumerate(records):
        ring[9 * i:9 * i + 9] = [677_000, 0, 0, 0, w4, w5, w6, w7, frame << 12 | alt << 1 | beat]
    return words + ring


class DecodeVideoLogTest(unittest.TestCase):
    def test_change_records(self):
        # PAL BIOS Fill: 288 lines, slot 7, kept 10..277, picture on 16..271, x 8..631
        w4 = 288 << 22 | 573 << 12 | 7 << 9 | 1 << 8 | 1 << 7 | 1 << 6 | 1 << 3 | 1 << 2 | 1 << 1 | 1
        w5 = 268 << 22 | 10 << 12 | 277 << 2
        w6 = 311 << 22 | 640 << 12 | 8 << 2
        w7 = 16 << 22 | 271 << 12 | 631 << 2
        black = 1023 << 22
        recs = [(0, 0, 0, w4, w5, w6, black), (5, 0, 1, w4, w5, w6, w7), (256, 1, 1, w4 ^ 1 << 12, w5, w6, w7)]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'log.bin'
            path.write_bytes(struct.pack(f'<{len(video_log(recs, 3))}I', *video_log(recs, 3)))
            rows = decode_perf_log.load(path)
            self.assertEqual([r['frame'] for r in rows], [0, 5, 256])
            r = rows[1]
            self.assertEqual((r['lines'], r['hash'], r['slot_used'], r['pal'], r['bob'], r['fill'], r['out']),
                             (288, 573, 7, 1, 1, 1, 268))
            self.assertEqual((r['first_kept'], r['last_kept'], r['hs'], r['line_w']), (10, 277, 311, 640))
            self.assertEqual((r['pic_y0'], r['pic_y1'], r['pic_x0'], r['pic_x1']), (16, 271, 8, 631))
            self.assertEqual((rows[2]['heartbeat'], rows[2]['alternating']), (1, 1))
            out = subprocess.run([sys.executable, str(ROOT/'scripts/decode_perf_log.py'), str(path)],
                                 capture_output=True, text=True, check=True).stdout
            # 6 black lines above and below the picture in the 268 sent: 95.5% picture
            self.assertIn('   6   6  95.5', out)
            self.assertIn('beat alt', out)
            self.assertIn('(black)', out)


if __name__ == '__main__':
    unittest.main()
