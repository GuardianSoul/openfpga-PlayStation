"""Real staged image packing and versioned entry contract; no FPGA fit claim."""
import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_staged_save_fw import image_parts, lz_pack, rom40_hex


def unpack(stream, length):
    result = bytearray()
    index = 0
    while len(result) < length:
        flags = stream[index]
        index += 1
        for bit in range(8):
            if len(result) == length:
                break
            if flags & (1 << bit):
                result.append(stream[index])
                index += 1
            else:
                lo, hi = stream[index:index + 2]
                index += 2
                distance = lo | ((hi & 0xf0) << 4)
                if not 0 < distance <= len(result):
                    raise ValueError('Invalid back reference')
                for _ in range(3 + (hi & 15)):
                    result.append(result[-distance])
    if len(result) != length:
        raise ValueError('Decoded size exceeds declared stage 2')
    return bytes(result)


class FullSaveImageTest(unittest.TestCase):
    def test_overlap_and_window_boundary_round_trip(self):
        cases = [b'x' * 16384, bytes(range(256)) * 40,
                 bytes((i * 73 + (i >> 8)) & 255 for i in range(5000))]
        for original in cases:
            self.assertEqual(unpack(lz_pack(original), len(original)), original)
        with self.assertRaisesRegex(ValueError, 'back reference'):
            unpack(bytes([0, 0, 0]), 3)

    def test_complete_native_image_fits_and_decodes_exactly(self):
        image, stage2 = image_parts(ram_pages=8, vram_rows=5, spu_pages=2)
        self.assertEqual(len(image), 8192)
        self.assertEqual(image[:8], bytes.fromhex('68101aac6c101bac'))
        self.assertEqual(image[0x1000:0x1c00], bytes(0xc00))
        self.assertEqual([int.from_bytes(image[p:p + 4], 'little') for p in (0xff4, 0xff8, 0xffc)], [2, 5, 8])
        header = stage2[:4] + len(stage2).to_bytes(4, 'little')
        offset = image[:0xff4].find(header)
        self.assertGreater(offset, 0)
        self.assertEqual(image[:0xff4].count(header), 1)
        stream = image[offset + 8:0xff4] + image[0x1c00:]
        self.assertEqual(unpack(stream, len(stage2)), stage2)
        self.assertEqual(int.from_bytes(stage2[:4], 'little'), zlib.crc32(stage2[4:]) or 1)
        # The selected physical ROM still has the original 1024 x 40 geometry.
        rows = rom40_hex(image[:0x1000], image[0x1c00:]).splitlines()
        self.assertEqual(len(rows), 1024)
        self.assertTrue(all(len(row) == 10 for row in rows))
        self.assertEqual(bytes(int(row[:2], 16) for row in rows), image[0x1c00:])
        self.assertEqual(b''.join(int(row[2:], 16).to_bytes(4, 'little') for row in rows), image[:0x1000])


if __name__ == '__main__':
    unittest.main()
