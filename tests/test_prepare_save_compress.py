# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_save_compress as cz


def sample(seed, size):
    rng = random.Random(seed)
    words = [bytes(rng.randrange(256) for _ in range(rng.randrange(3, 24))) for _ in range(200)]
    data = bytearray()
    while len(data) < size:
        data += rng.choice((rng.choice(words), bytes(rng.randrange(1, 900)), bytes(rng.randrange(256) for _ in range(rng.randrange(1, 40)))))
    return bytes(data[:size])


class CompressStep(unittest.TestCase):
    def test_round_trip(self):
        for seed in range(6):
            data = sample(seed, 6 * cz.PAGE_BYTES)
            stream = cz.compress(data)
            self.assertLess(len(stream), len(data))
            self.assertEqual(cz.decompress(stream, len(data)), data)

    def test_edges(self):
        for data in (bytes(2048), bytes(range(256)) * 8, bytes(70000) + b'abcd' + bytes(70000), b'\x01\x02\x03' + bytes(2045)):
            self.assertEqual(cz.decompress(cz.compress(data), len(data)), data)
        noise = random.Random(7).randbytes(8 * 2048)
        stream = cz.compress(noise)
        self.assertLessEqual(len(stream), len(noise) + len(noise) // 255 + 16)
        self.assertEqual(cz.decompress(stream, len(noise)), noise)

    def test_no_match_further_than_the_offset_holds(self):
        block = random.Random(3).randbytes(64)
        data = block + random.Random(4).randbytes(65536 - 64) + block      # exactly 65,536 back: no offset for it
        stream = cz.compress(data)
        self.assertEqual(cz.decompress(stream, len(data)), data)

    def test_refused_streams(self):
        data = sample(1, 4 * cz.PAGE_BYTES)
        stream = cz.compress(data)
        with self.assertRaises(cz.BadStream):
            cz.decompress(stream[:-1], len(data))
        with self.assertRaises(cz.BadStream):
            cz.decompress(stream + b'\0', len(data))
        with self.assertRaises(cz.BadStream):
            cz.decompress(stream, len(data) - cz.PAGE_BYTES)
        with self.assertRaises(cz.BadStream):
            cz.decompress(bytes((0x10, 0x41, 0x05, 0x00)) + bytes(60), 2048)     # a match from before the first byte
        with self.assertRaises(cz.BadStream):
            cz.decompress(bytes((0x10, 0x41, 0x00, 0x00)) + bytes(60), 2048)     # offset 0

    def test_step_text(self):
        base = cz.base_text()
        text = cz.firmware_text(base)
        self.assertIn('cz_save', text)
        with self.assertRaises(ValueError):
            cz.firmware_text(text)
        import prepare_save_mdec_tap as tap          # the step also applies to routine b (no size-mib)
        self.assertIn('sb      $v1, SIZE_LO', cz.firmware_text(tap.firmware_text(tap.big_build_text())))
        for name in cz.FAULTS:
            self.assertNotEqual(cz.fault_text(text, name), text)
        # everything outside the step's anchors is the base's text
        kept = [line for line in base.splitlines() if line in set(text.splitlines())]
        self.assertGreater(len(kept), len(base.splitlines()) - 12)


if __name__ == '__main__':
    unittest.main()
