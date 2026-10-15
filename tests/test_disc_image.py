from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.disc_image import DiscImage, ImageError, inspect_disc, read_cue


def number(value, width):
    return value.to_bytes(width, 'little') + value.to_bytes(width, 'big')


def record(name, lba, size, directory=False):
    name = name.encode('ascii') if isinstance(name, str) else name
    data = bytearray(33 + len(name) + (len(name) % 2 == 0))
    data[0] = len(data)
    data[2:10] = number(lba, 4)
    data[10:18] = number(size, 4)
    data[25] = 2 if directory else 0
    data[28:32] = number(1, 2)
    data[32] = len(name)
    data[33:33+len(name)] = name
    return data


class DiscImageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / 'test disc.bin'
        self.cue = self.root / 'test.cue'
        self.cue.write_text('REM synthetic data only\nFILE "test disc.bin" BINARY\n TRACK 01 MODE2/2352\n INDEX 01 00:00:00\n')
        # All fixture content is generated; CI never needs the user's game files.
        self.sectors = []
        for _ in range(32):
            raw = bytearray(2352)
            raw[:12] = bytes([0]) + bytes([255])*10 + bytes([0])
            raw[12:16] = bytes([0, 2, 0, 2])
            raw[16:24] = bytes([0, 0, 8, 0])*2
            self.sectors.append(raw)
        licence = b'Licensed by Sony Computer Entertainment Amer  ica '
        self.sectors[4][24:24+len(licence)] = licence
        descriptor = bytearray(2048)
        descriptor[:7] = b'\x01CD001\x01'
        descriptor[40:72] = b'SYNTHETIC'.ljust(32, b' ')
        descriptor[80:88] = number(32, 4)
        descriptor[128:132] = number(2048, 2)
        descriptor[156:190] = record(b'\0', 20, 4096, True)
        self.sectors[16][24:24+2048] = descriptor
        self.sectors[17][24:31] = b'\xffCD001\x01'
        cnf = b'BOOT = cdrom:\\TEST_123.45;1\r\nSTACK = 801fff00\r\n'
        first_page = record(b'\0', 20, 4096, True) + record(b'\1', 20, 4096, True)
        self.sectors[20][24:24+len(first_page)] = first_page
        # File records live in the next block after zero directory padding.
        files = record('SYSTEM.CNF;1', 22, len(cnf)) + record('TEST_123.45;1', 23, 4096)
        self.sectors[21][24:24+len(files)] = files
        self.sectors[22][24:24+len(cnf)] = cnf
        header = bytearray(2048)
        header[:8] = b'PS-X EXE'
        for offset, value in ((0x10, 0x80010080), (0x18, 0x80010000), (0x1C, 2048), (0x30, 0x801FFFF0)):
            header[offset:offset+4] = value.to_bytes(4, 'little')
        self.sectors[23][24:24+2048] = header
        self.sectors[24][24:24+2048] = bytes(range(256))*8
        self.save()

    def save(self, prefix=b''):
        self.bin.write_bytes(prefix + b''.join(self.sectors))

    def test_boot_metadata_and_directory_padding(self):
        report = inspect_disc(self.cue)
        self.assertEqual(report['volume'], 'SYNTHETIC')
        self.assertEqual(report['boot_file'], 'TEST_123.45;1')
        self.assertEqual(report['boot_lba'], 23)
        self.assertEqual(report['licence_area'], 'America')
        self.assertEqual(report['executable']['initial_pc'], '0x80010080')
        self.assertEqual(report['executable']['payload_bytes'], 2048)
        self.assertEqual(report['compatibility'], 'untested')

    def test_index_offset_and_exact_raw_sector(self):
        self.cue.write_text('FILE "test disc.bin" BINARY\n TRACK 01 MODE2/2352\n INDEX 01 00:00:01\n')
        self.save(b'\xA5'*2352)
        with DiscImage(self.cue) as image:
            self.assertEqual(image.read_raw(24), bytes(self.sectors[24]))
            self.assertEqual(image.read_block(24), bytes(range(256))*8)
            self.assertEqual(image.read_extent(23, 2051)[2048:], b'\0\1\2')
            for lba in (-1, 32):
                with self.assertRaises(ImageError):
                    image.read_raw(lba)
        self.assertEqual(inspect_disc(self.cue)['track_sectors'], 32)

    def test_form_two_data_is_not_a_filesystem_block(self):
        sector = self.sectors[25]
        sector[18] = sector[22] = 0x28
        sector[24:2348] = bytes((lane*13) & 255 for lane in range(2324))
        self.save()
        with DiscImage(self.cue) as image:
            self.assertEqual(len(image.read_data(25)), 2324)
            self.assertEqual(image.read_data(25)[-1], (2323*13) & 255)
            with self.assertRaisesRegex(ImageError, 'Form 2'):
                image.read_block(25)

    def test_truncated_image_and_bad_index(self):
        self.bin.write_bytes(self.bin.read_bytes()[:-1])
        with self.assertRaises(ImageError):
            read_cue(self.cue)
        self.save()
        for time in ('00:60:00', '00:00:75', '00:01:00'):
            self.cue.write_text(f'FILE "test disc.bin" BINARY\n TRACK 01 MODE2/2352\n INDEX 01 {time}\n')
            with self.assertRaises(ImageError):
                read_cue(self.cue)

    def test_unsupported_tracks_are_rejected(self):
        self.cue.write_text('FILE "test disc.bin" BINARY\n TRACK 01 MODE1/2352\n INDEX 01 00:00:00\n')
        with self.assertRaises(ImageError):
            read_cue(self.cue)
        self.cue.write_text('FILE "test disc.bin" BINARY\n TRACK 01 MODE2/2352\n INDEX 01 00:00:00\n TRACK 02 AUDIO\n INDEX 01 00:00:01\n')
        with self.assertRaises(ImageError):
            read_cue(self.cue)

    def test_raw_sync_and_subheader_damage(self):
        for offset in (0, 15, 20):
            with self.subTest(offset=offset):
                self.sectors[16][offset] ^= 1
                self.save()
                with self.assertRaises(ImageError):
                    inspect_disc(self.cue)
                self.sectors[16][offset] ^= 1

    def test_volume_endian_disagreement_and_out_of_image_bounds(self):
        self.sectors[16][24+84] ^= 1
        self.save()
        with self.assertRaisesRegex(ImageError, 'endian'):
            inspect_disc(self.cue)
        self.sectors[16][24+80:24+88] = number(33, 4)
        self.save()
        with self.assertRaisesRegex(ImageError, 'beyond image'):
            inspect_disc(self.cue)

    def test_bad_executable_header_and_payload(self):
        self.sectors[23][24] = 0
        self.save()
        with self.assertRaisesRegex(ImageError, 'PS-X EXE header'):
            inspect_disc(self.cue)
        self.sectors[23][24] = ord('P')
        self.sectors[23][24+0x1C:24+0x20] = (4096).to_bytes(4, 'little')
        self.save()
        with self.assertRaisesRegex(ImageError, 'payload size'):
            inspect_disc(self.cue)

    def test_directory_entry_outside_volume(self):
        cnf_length = self.sectors[21][24]
        self.sectors[21][24+2:24+10] = number(32, 4)
        self.save()
        self.assertGreater(cnf_length, 0)
        with self.assertRaisesRegex(ImageError, 'beyond ISO volume'):
            inspect_disc(self.cue)
