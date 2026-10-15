"""Read-only, single-track MODE2/2352 CUE reader for local PSX disc tests.

Format sources: ECMA-119 directory records and psx-spx CDROM format/EXE docs.
This does not emulate a CD drive or verify sector EDC/ECC or retail dump hashes.
"""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import re
import shlex

RAW_SECTOR_SIZE = 2352
BLOCK_SIZE = 2048
SYNC = b'\x00' + b'\xff' * 10 + b'\x00'


class ImageError(ValueError):
    pass


@dataclass(frozen=True)
class CueTrack:
    cue: Path
    image: Path
    index_sector: int
    sectors: int


def read_cue(path):
    cue = Path(path).resolve()
    filename = None
    track_seen = False
    index = None
    for line_number, line in enumerate(cue.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip() or line.lstrip().upper().startswith('REM '):
            continue
        try:
            fields = shlex.split(line, posix=True)
        except ValueError as error:
            raise ImageError(f'CUE line {line_number}: {error}') from error
        command = fields[0].upper()
        if command == 'FILE' and len(fields) == 3 and fields[2].upper() == 'BINARY' and filename is None and not track_seen:
            filename = fields[1]
        elif command == 'TRACK' and fields[1:] == ['01', 'MODE2/2352'] and filename and not track_seen:
            track_seen = True
        elif command == 'INDEX' and len(fields) == 3 and fields[1] == '01' and track_seen and index is None:
            match = re.fullmatch(r'(\d+):(\d{2}):(\d{2})', fields[2])
            if not match:
                raise ImageError('Malformed INDEX time')
            minute, second, frame = map(int, match.groups())
            if second >= 60 or frame >= 75:
                raise ImageError('INDEX time out of range')
            index = (minute * 60 + second) * 75 + frame
        else:
            raise ImageError(f'CUE line {line_number}: only one FILE, TRACK 01 MODE2/2352 and INDEX 01 are supported')
    if filename is None or not track_seen or index is None:
        raise ImageError('Incomplete CUE')
    image = (cue.parent / filename).resolve()
    size = image.stat().st_size
    if size == 0 or size % RAW_SECTOR_SIZE:
        raise ImageError('BIN size is not a positive multiple of 2352 bytes')
    sectors = size // RAW_SECTOR_SIZE - index
    if sectors <= 0:
        raise ImageError('INDEX 01 is beyond the image')
    return CueTrack(cue, image, index, sectors)


class DiscImage:
    def __init__(self, cue):
        self.track = read_cue(cue)
        self.file = self.track.image.open('rb')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.file.close()

    def read_raw(self, lba):
        if not isinstance(lba, int) or lba < 0 or lba >= self.track.sectors:
            raise ImageError(f'LBA out of range: {lba}')
        self.file.seek((self.track.index_sector + lba) * RAW_SECTOR_SIZE)
        data = self.file.read(RAW_SECTOR_SIZE)
        if len(data) != RAW_SECTOR_SIZE:
            raise ImageError('Truncated sector')
        return data

    def read_data(self, lba):
        raw = self.read_raw(lba)
        if raw[:12] != SYNC or raw[15] != 2:
            raise ImageError(f'LBA {lba}: not a raw Mode 2 sector')
        if raw[16:20] != raw[20:24]:
            raise ImageError(f'LBA {lba}: XA subheader copies differ')
        size = 2324 if raw[18] & 0x20 else BLOCK_SIZE
        return raw[24:24 + size]

    def read_block(self, lba):
        data = self.read_data(lba)
        if len(data) != BLOCK_SIZE:
            raise ImageError(f'LBA {lba}: Form 2 cannot be used as a 2048-byte filesystem block')
        return data

    def read_extent(self, lba, size, limit=1024*1024):
        count = (size + BLOCK_SIZE - 1) // BLOCK_SIZE
        if size < 0 or size > limit or lba < 0 or lba + count > self.track.sectors:
            raise ImageError('Extent size or location out of range')
        return b''.join(self.read_block(lba + offset) for offset in range(count))[:size]

    def sha256(self):
        digest = hashlib.sha256()
        self.file.seek(0)
        for chunk in iter(lambda: self.file.read(4*1024*1024), b''):
            digest.update(chunk)
        return digest.hexdigest()


def dual_number(data, offset, width):
    field = data[offset:offset + width*2]
    if len(field) != width*2:
        raise ImageError('Truncated ISO numeric field')
    little = int.from_bytes(field[:width], 'little')
    if little != int.from_bytes(field[width:], 'big'):
        raise ImageError('ISO little/big-endian values disagree')
    return little


def directory_record(data):
    if len(data) < 34 or data[0] != len(data) or 33 + data[32] > len(data):
        raise ImageError('Malformed ISO directory record')
    if data[1] or data[26] or data[27] or data[25] & 0x80:
        raise ImageError('Extended-attribute, interleaved or multi-extent records are unsupported')
    if dual_number(data, 28, 2) != 1:
        raise ImageError('Multi-volume record is unsupported')
    return {
        'name': data[33:33 + data[32]].decode('ascii'),
        'lba': dual_number(data, 2, 4),
        'bytes': dual_number(data, 10, 4),
        'directory': bool(data[25] & 2),
    }


def root_directory(image):
    pvd = None
    for lba in range(16, min(80, image.track.sectors)):
        descriptor = image.read_block(lba)
        if descriptor[1:7] != b'CD001\x01':
            raise ImageError('Invalid ISO volume descriptor signature')
        if descriptor[0] == 1:
            pvd = descriptor
            break
        if descriptor[0] == 255:
            break
    if pvd is None:
        raise ImageError('No primary ISO volume descriptor found')
    if dual_number(pvd, 128, 2) != BLOCK_SIZE:
        raise ImageError('Only 2048-byte ISO logical blocks are supported')
    blocks = dual_number(pvd, 80, 4)
    if blocks <= 16 or blocks > image.track.sectors:
        raise ImageError('ISO volume extends beyond image')
    root = directory_record(pvd[156:156 + pvd[156]])
    if not root['directory'] or root['name'] != '\0':
        raise ImageError('Invalid ISO root directory')
    def check_extent(record):
        if record['lba'] + (record['bytes'] + BLOCK_SIZE - 1) // BLOCK_SIZE > blocks:
            raise ImageError('Directory entry extends beyond ISO volume')
    check_extent(root)
    contents = image.read_extent(root['lba'], root['bytes'])
    entries = {}
    offset = 0
    while offset < len(contents):
        length = contents[offset]
        if length == 0:
            offset = (offset // BLOCK_SIZE + 1) * BLOCK_SIZE
            continue
        if offset + length > len(contents) or offset % BLOCK_SIZE + length > BLOCK_SIZE:
            raise ImageError('Directory record crosses a block boundary')
        record = directory_record(contents[offset:offset + length])
        check_extent(record)
        if record['name'] not in ('\0', '\1'):
            key = record['name'].upper()
            if key in entries:
                raise ImageError('Duplicate root entry')
            entries[key] = record
        offset += length
    return pvd[40:72].decode('ascii').rstrip(' \0'), blocks, entries


def inspect_disc(cue, include_hash=False):
    with DiscImage(cue) as image:
        volume, blocks, entries = root_directory(image)
        cnf_record = entries.get('SYSTEM.CNF;1')
        if not cnf_record or cnf_record['directory']:
            raise ImageError('SYSTEM.CNF;1 not found in root')
        cnf = image.read_extent(cnf_record['lba'], cnf_record['bytes'], limit=65536).decode('ascii')
        settings = {}
        for line in cnf.splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                settings[key.strip().upper()] = value.strip()
        boot = re.fullmatch(r'cdrom:\\?([A-Z0-9_.-]+;\d+)', settings.get('BOOT', ''), re.IGNORECASE)
        if not boot:
            raise ImageError('Only a root boot executable without arguments is supported')
        boot_name = boot[1].upper()
        executable = entries.get(boot_name)
        if not executable or executable['directory'] or executable['bytes'] < BLOCK_SIZE:
            raise ImageError('Boot executable missing or too short')
        header = image.read_block(executable['lba'])
        if header[:8] != b'PS-X EXE':
            raise ImageError('Boot executable does not have a PS-X EXE header')
        word = lambda offset: int.from_bytes(header[offset:offset+4], 'little')
        payload_size = word(0x1C)
        if payload_size % BLOCK_SIZE or payload_size > executable['bytes'] - BLOCK_SIZE:
            raise ImageError('PS-X EXE payload size does not fit file')
        # Whitespace can split the licence's region name across its display lines.
        licence = re.sub(rb'\s+', b'', image.read_block(4)[:70])
        licence_area = 'America' if b'America' in licence else 'Europe' if b'Europe' in licence else 'Japan' if b'Inc.' in licence else 'unknown'
        report = {
            'cue': str(image.track.cue), 'image': str(image.track.image),
            'format': 'MODE2/2352', 'index_sector': image.track.index_sector,
            'image_bytes': image.track.image.stat().st_size, 'track_sectors': image.track.sectors,
            'volume': volume, 'volume_blocks': blocks, 'licence_area': licence_area,
            'boot_file': boot_name, 'boot_lba': executable['lba'], 'boot_file_bytes': executable['bytes'],
            'executable': {'initial_pc': f'0x{word(0x10):08x}', 'initial_gp': f'0x{word(0x14):08x}',
                           'load_address': f'0x{word(0x18):08x}', 'payload_bytes': payload_size,
                           'stack_base': f'0x{word(0x30):08x}', 'stack_offset': f'0x{word(0x34):08x}'},
            'system_cnf': settings, 'compatibility': 'untested',
        }
        if include_hash:
            report['sha256'] = image.sha256()
        return report
