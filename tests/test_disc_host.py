"""Track-info boundary/encoding checks independent of the RTL fixture."""
from pathlib import Path
import tempfile
import unittest

from scripts.psx_disc_host import DiscHost, metadata_words


class DiscHostTests(unittest.TestCase):
    def test_metadata_protocol_fields_and_bcd_leadout(self):
        # 61 seconds of file data plus the two-second pregap => 01:03:00.
        words = metadata_words(4575)
        self.assertEqual(words[:8], [0x0101, 4725, 0x0103, 0x00020000,
                                    0, 4724, 0x0002, 0])
        self.assertEqual(words[8:], [0] * 392)
        for region in range(4):
            self.assertEqual(metadata_words(1, region)[3], region << 16)

    def test_invalid_metadata_rejected_before_host_file_access(self):
        for sectors in (0, -1, 524139, 449850, 1.5, True):
            with self.subTest(sectors=sectors), self.assertRaises(ValueError):
                metadata_words(sectors)
        for region in (-1, 4, 1.5, True):
            with self.subTest(region=region), self.assertRaises(ValueError):
                metadata_words(1, region)
        # Last representable lead-out time is 99:59:74 (fraction omitted).
        self.assertEqual(metadata_words(449849)[2], 0x9959)

    def test_physical_boundaries_respect_index_and_exclude_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'Disc Image.bin').write_bytes(bytes([0x11]) * 2352 + bytes([0x22]) * 2352)
            cue = directory / 'Disc Image.cue'
            cue.write_text('FILE "Disc Image.bin" BINARY\nTRACK 01 MODE2/2352\nINDEX 01 00:00:01\n')
            with DiscHost(cue) as host:
                self.assertEqual(host.read_physical(149), bytes(2352))
                self.assertEqual(host.read_physical(150), bytes([0x22]) * 2352)
                self.assertEqual(host.read_physical(151), bytes([0xaa]) * 2352)
                self.assertEqual(host.read_physical(0xffffffff), bytes([0xaa]) * 2352)
                for lba in (-1, 0x100000000, 150.5, True):
                    with self.subTest(lba=lba), self.assertRaises(ValueError):
                        host.read_physical(lba)


if __name__ == '__main__':
    unittest.main()
