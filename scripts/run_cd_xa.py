#!/usr/bin/env python3
"""XA-ADPCM check: the pinned MiSTer cd_xa decoder against Python references.

Raw XA sectors go through cd_xa (tb_cd_xa.vhd) exactly as cd_top delivers
them, and every popped 44.1 kHz stereo frame is compared with two
independent decoders written here:

- "rtl": psx-spx ADPCM with the arithmetic cd_xa uses ((sum+32)/64 truncated
  toward zero, unclamped 24-bit history, zigzag products truncated toward
  zero, 18.9 kHz by sample doubling). The RTL must match it exactly.
- "psx-spx": psx-spx's decode_28_nibbles and zigzag interpolation as
  written there (clamped history, the divisions as arithmetic shifts). The
  difference is reported, not failed: it measures how far the MiSTer
  arithmetic is from the documented console behaviour.

Synthetic sectors cover every format (4/8-bit, mono/stereo, 37.8/18.9 kHz,
all filters and shifts, clipping). If the FFVII disc 1 image is present
(PSX_FFVII_DISC1 or roms/), real FMV/music sectors (file 1, channel 0) run
too. A planted fault (filter 2 coefficient 115 -> 116) must fail.

The cd-xa-history-clamp variant (clamped filter history, as psx-spx,
Mednafen does) runs the same sectors against an "rtl-clamp"
reference: the MiSTer arithmetic with clamped history.
"""
import os
import random
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from prepare_cd_xa_history_clamp import history_clamp_text
from tool_paths import ROOT, find_tool

UPSTREAM = ROOT / 'references/PSX_MiSTer'
SECTOR = 2352
# psx-spx "CDROM XA Audio ADPCM Compression": the four XA filters
# (pos_xa_adpcm_table / neg_xa_adpcm_table; XA has no fifth filter).
POS = (0, 60, 115, 98)
NEG = (0, 0, -52, -55)
# psx-spx "25-point Zigzag Interpolation": index 1..29 (rows), Table1..Table7
# (columns), as printed there. ZIGZAG[k][i-1] is TableK+1[i].
_ZIGZAG_ROWS = (
    (0, 0, 0, 0, -0x0001, +0x0002, -0x0005),
    (0, 0, 0, -0x0001, +0x0003, -0x0008, +0x0011),
    (0, 0, -0x0001, +0x0003, -0x0008, +0x0010, -0x0023),
    (0, -0x0002, +0x0003, -0x0008, +0x0011, -0x0023, +0x0046),
    (0, 0, -0x0002, +0x0006, -0x0010, +0x002B, -0x0017),
    (-0x0002, +0x0003, -0x0005, +0x0005, +0x000A, +0x001A, -0x0044),
    (+0x000A, -0x0013, +0x001F, -0x001B, +0x006B, -0x00EB, +0x015B),
    (-0x0022, +0x003C, -0x004A, +0x00A6, -0x016D, +0x027B, -0x0347),
    (+0x0041, -0x004B, +0x00B3, -0x01A8, +0x0350, -0x0548, +0x080E),
    (-0x0054, +0x00A2, -0x0192, +0x0372, -0x0623, +0x0AFA, -0x1249),
    (+0x0034, -0x00E3, +0x02B1, -0x05BF, +0x0BCD, -0x16FA, +0x3C07),
    (+0x0009, +0x0132, -0x039E, +0x09B8, -0x1780, +0x53E0, +0x53E0),
    (-0x010A, -0x0043, +0x04F8, -0x11B4, +0x6794, +0x3C07, -0x16FA),
    (+0x0400, -0x0267, -0x05A6, +0x74BB, +0x234C, -0x1249, +0x0AFA),
    (-0x0A78, +0x0C9D, +0x7939, +0x0C9D, -0x0A78, +0x080E, -0x0548),
    (+0x234C, +0x74BB, -0x05A6, -0x0267, +0x0400, -0x0347, +0x027B),
    (+0x6794, -0x11B4, +0x04F8, -0x0043, -0x010A, +0x015B, -0x00EB),
    (-0x1780, +0x09B8, -0x039E, +0x0132, +0x0009, -0x0044, +0x001A),
    (+0x0BCD, -0x05BF, +0x02B1, -0x00E3, +0x0034, -0x0017, +0x002B),
    (-0x0623, +0x0372, -0x0192, +0x00A2, -0x0054, +0x0046, -0x0023),
    (+0x0350, -0x01A8, +0x00B3, -0x004B, +0x0041, -0x0023, +0x0010),
    (-0x016D, +0x00A6, -0x004A, +0x003C, -0x0022, +0x0011, -0x0008),
    (+0x006B, -0x001B, +0x001F, -0x0013, +0x000A, -0x0005, +0x0002),
    (+0x000A, +0x0005, -0x0005, +0x0003, -0x0001, 0, 0),
    (-0x0010, +0x0006, -0x0002, 0, 0, 0, 0),
    (+0x0011, -0x0008, +0x0003, -0x0002, +0x0001, 0, 0),
    (-0x0008, +0x0003, -0x0001, 0, 0, 0, 0),
    (+0x0003, -0x0001, 0, 0, 0, 0, 0),
    (-0x0001, 0, 0, 0, 0, 0, 0),
)
ZIGZAG = tuple(tuple(row[k] for row in _ZIGZAG_ROWS) for k in range(7))


def tdiv(a, b):
    """Integer division truncating toward zero (VHDL '/')."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def wrap(value, bits):
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def clamp16(value):
    return max(-32768, min(32767, value))


def resize_signed(value, bits):
    """numeric_std resize of a SIGNED to fewer bits: keeps the sign bit and the low bits-1 bits."""
    low = value & ((1 << (bits - 1)) - 1)
    return low - (1 << (bits - 1)) if value < 0 else low


def sign_extend(value, bits):
    return wrap(value, bits)


class Coding:
    """Subheader coding info (psx-spx "CDROM XA Subheader"): bits 0-1 stereo, bit 2 18.9 kHz, bits 4-5 8-bit."""
    def __init__(self, sector):
        coding = sector[19]
        self.stereo = coding & 3 == 1
        self.half_rate = bool(coding & 4)
        self.eight_bit = (coding >> 4) & 3 == 1


def sound_units(sector, coding):
    """(unit, header byte, 28 raw sample codes) for each unit of each of the 18 sound groups, in order.
    psx-spx: header byte 4+unit; data word j (bytes 16+4j..) holds the units' codes, 4 bits (8 units) or 8 bits
    (4 units) each, the lowest first."""
    for group in range(18):
        base = 24 + group * 128
        for unit in range(4 if coding.eight_bit else 8):
            header = sector[base + 4 + unit]
            if coding.eight_bit:
                codes = [sector[base + 16 + 4 * j + unit] for j in range(28)]
            else:
                codes = [(sector[base + 16 + 4 * j + unit // 2] >> (4 * (unit & 1))) & 15 for j in range(28)]
            yield unit, header, codes


class RtlDecoder:
    """The pinned MiSTer cd_xa arithmetic, as its VHDL computes it (the RTL must match this exactly):
    the 4/8-bit code left-aligned in 16 bits and shifted right (shift 13-15 as 9), the filter sum
    (last0*pos + last1*neg + 32) / 64 truncated toward zero in 32 bits and resized to 24, plus the
    sample, in 24 bits; 16-bit clamped output, 24-bit unclamped history (clamp_history: the clamped
    value, cd-xa-history-clamp). Resampling: a 32-entry ring per channel, after every sixth write seven
    outputs, output k = sum over c of ring[ptr - c] * ZIGZAG[k][c] / 0x8000 (truncated, 17 bits; sum
    22 bits) for c = 0..28 from the next write slot, clamped; 18.9 kHz writes each sample twice."""
    def __init__(self, clamp_history=False):
        self.clamp_history = clamp_history

    def decode(self, sectors):
        history = {0: [0, 0], 1: [0, 0]}   # channel -> [last0, last1]
        rings = {0: [0] * 32, 1: [0] * 32}
        pointer, six, frames = 0, 0, []
        for sector in sectors:
            coding = Coding(sector)
            left, right = [], []
            for unit, header, codes in sound_units(sector, coding):
                shift = header & 15 if header & 15 < 13 else 9
                pos, neg = POS[(header >> 4) & 3], NEG[(header >> 4) & 3]
                channel = 1 if coding.stereo and unit & 1 else 0
                last = history[channel]
                for code in codes:
                    aligned = sign_extend(code, 8) << 8 if coding.eight_bit else sign_extend(code, 4) << 12
                    new = aligned >> shift
                    total = wrap(wrap(last[0] * pos + last[1] * neg, 32) + 32, 32)
                    sample = wrap(resize_signed(tdiv(total, 64), 24) + new, 24)
                    out = clamp16(sample)
                    last[1], last[0] = last[0], (clamp16(sample) if self.clamp_history else sample)
                    if coding.stereo:
                        (right if unit & 1 else left).append(out)
                    else:
                        left.append(out); right.append(out)
            for pair in zip(left, right):
                for _ in range(2 if coding.half_rate else 1):
                    for channel in (0, 1):
                        rings[channel][pointer] = pair[channel]
                    pointer, six = (pointer + 1) & 31, six + 1
                    if six == 6:
                        six = 0
                        for k in range(7):
                            frame = []
                            for channel in (0, 1):
                                total = 0
                                for c in range(29):
                                    product = resize_signed(tdiv(rings[channel][(pointer - c) & 31] * ZIGZAG[k][c], 0x8000), 17)
                                    total = wrap(total + product, 22)
                                frame.append(clamp16(total))
                            frames.append(tuple(frame))
        return frames


class PsxSpxDecoder:
    """psx-spx's decode_28_nibbles and 25-point zigzag interpolation, as written there: sample =
    (code << (12 - shift)) + (old*f0 + older*f1 + 32) / 64, clamped, and the clamped value is the
    history; 44.1 kHz output = sum for i = 1..29 of ring[p - i] * TableK[i] / 8000h, clamped, seven
    outputs after every sixth 37.8 kHz sample. psx-spx writes the divisions as '/'; they are taken as
    arithmetic right shifts here (floor). Reserved shifts 13-15 act as 9; 8-bit codes are left-aligned
    by 8 (psx-spx "XA-ADPCM Header Bytes"). psx-spx gives no 18.9 kHz algorithm: each sample is fed
    twice, as in the RTL. align_rtl: the RTL's taps instead (TableK[i] on ring[p - i + 1], so the
    newest sample takes TableK[2]); that leaves only the arithmetic differences to compare."""
    def __init__(self, align_rtl=False):
        self.tap = 1 if align_rtl else 0

    def decode(self, sectors):
        history = {0: [0, 0], 1: [0, 0]}   # channel -> [old, older]
        rings = {0: [0] * 32, 1: [0] * 32}
        p, sixstep, frames = 0, 6, []
        for sector in sectors:
            coding = Coding(sector)
            left, right = [], []
            for unit, header, codes in sound_units(sector, coding):
                shift = header & 15 if header & 15 < 13 else 9
                f0, f1 = POS[(header >> 4) & 3], NEG[(header >> 4) & 3]
                channel = 1 if coding.stereo and unit & 1 else 0
                state = history[channel]
                for code in codes:
                    aligned = sign_extend(code, 8) << 8 if coding.eight_bit else sign_extend(code, 4) << 12
                    s = clamp16((aligned >> shift) + ((state[0] * f0 + state[1] * f1 + 32) >> 6))
                    state[1], state[0] = state[0], s
                    if coding.stereo:
                        (right if unit & 1 else left).append(s)
                    else:
                        left.append(s); right.append(s)
            for pair in zip(left, right):
                for _ in range(2 if coding.half_rate else 1):
                    for channel in (0, 1):
                        rings[channel][p & 31] = pair[channel]
                    p, sixstep = p + 1, sixstep - 1
                    if sixstep == 0:
                        sixstep = 6
                        for table in ZIGZAG:
                            frames.append(tuple(clamp16(sum((rings[channel][(p - i + self.tap) & 31] * table[i - 1]) >> 15
                                                            for i in range(1, 30))) for channel in (0, 1)))
        return frames


def Decoder(mode):
    """'rtl' / 'rtl-clamp': the pinned cd_xa (without / with cd-xa-history-clamp); 'psx-spx': psx-spx;
    'psx-spx-taps': psx-spx's arithmetic on the RTL's zigzag taps."""
    return {'rtl': lambda: RtlDecoder(), 'rtl-clamp': lambda: RtlDecoder(clamp_history=True),
            'psx-spx': lambda: PsxSpxDecoder(), 'psx-spx-taps': lambda: PsxSpxDecoder(align_rtl=True)}[mode]()


def synthetic_sectors(coding, count, seed):
    rng = random.Random(seed)
    sectors = []
    for index in range(count):
        data = bytearray(rng.getrandbits(8) for _ in range(SECTOR))
        data[0:12] = bytes([0] + [0xFF] * 10 + [0])
        data[16:24] = bytes([1, 0, 0x64 | (0x80 if index == count - 1 else 0), coding]) * 2
        for chunk in range(18):
            base = 24 + chunk * 128
            # Header bytes 0-3 repeat at 4-15; the decoder reads 4..11.
            for block in range(16):
                shift = rng.choice((0, 1, 2, 4, 8, 11, 12, 13, 15)) if rng.random() < 0.5 else rng.randrange(16)
                data[base + block] = (rng.randrange(4) << 4) | shift
            if index % 2:
                # Quieter stretch: small nibbles, so filters ring without clipping.
                for word in range(28 * 4):
                    data[base + 16 + word] = rng.choice((0x00, 0x01, 0x10, 0xFF, 0xF1, 0x1F, 0x11, 0xEE))
        sectors.append(bytes(data))
    return sectors


def ffvii_sectors(count):
    candidates = [os.environ.get('PSX_FFVII_DISC1', ''),
                  str(ROOT / 'roms/Final Fantasy VII (Disc 1)/Final Fantasy VII (Disc 1).bin')]
    path = next((Path(c) for c in candidates if c and Path(c).is_file()), None)
    if path is None:
        return None
    sectors = []
    with path.open('rb') as image:
        image.seek(129523 * SECTOR)
        while len(sectors) < count:
            data = image.read(SECTOR)
            if len(data) < SECTOR:
                break
            # File 1, channel 0, Form 2 real-time audio, coding 0x01 (stereo 37.8 kHz 4-bit).
            if data[15] == 2 and data[16] == 1 and data[17] == 0 and data[18] & 0x24 == 0x24 and data[19] == 0x01:
                sectors.append(data)
    return sectors


def write_hex(path, sectors):
    with path.open('w') as f:
        for sector in sectors:
            for word in range(588):
                f.write(f'{int.from_bytes(sector[word * 4:word * 4 + 4], "little"):08x}\n')


def build(directory, xa_text):
    ghdl = find_tool('ghdl')
    directory.mkdir(parents=True, exist_ok=True)
    log = (directory / 'build.log').open('w')

    def call(*args):
        subprocess.run([ghdl, *args], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    mem = UPSTREAM / 'sim/system/src/mem'
    call('-a', '--std=08', '-frelaxed', '--work=work', str(mem / 'dpram.vhd'))
    call('-a', '--std=08', '-frelaxed', '--work=mem', str(mem / 'SyncFifo.vhd'))
    call('-a', '--std=08', '-frelaxed', '--work=mem', str(UPSTREAM / 'rtl/SyncFifoFallThrough.vhd'))
    call('-a', '--std=08', '-frelaxed', str(UPSTREAM / 'rtl/cd_xa_zigzag.vhd'))
    source = directory / 'cd_xa.vhd'
    # The simulation-only debug writer opens Windows paths; drop it.
    text = re.sub(r'-- synthesis translate_off.*?-- synthesis translate_on', '', xa_text, flags=re.S)
    source.write_text(text)
    call('-a', '--std=08', '-frelaxed', str(source))
    call('-a', '--std=08', '-frelaxed', str(ROOT / 'sim/reference/tb_cd_xa.vhd'))
    call('-e', '--std=08', '-frelaxed', 'tb_cd_xa')


def simulate(directory, name, sectors, frames_per_sector):
    ghdl = find_tool('ghdl')
    hexfile, outfile = directory / f'{name}.hex', directory / f'{name}.out'
    write_hex(hexfile, sectors)
    result = subprocess.run([ghdl, '-r', '--std=08', '-frelaxed', 'tb_cd_xa', f'-gSECTOR_FILE={hexfile}',
                             f'-gOUT_FILE={outfile}', f'-gSECTORS={len(sectors)}', f'-gFRAMES={frames_per_sector}',
                             '--assert-level=error', '--ieee-asserts=disable'],
                            cwd=directory, capture_output=True, text=True, timeout=7200)
    if result.returncode or 'PASS cd_xa' not in result.stdout + result.stderr:
        return None, (result.stdout + result.stderr).strip().splitlines()[-3:]
    return [tuple(map(int, line.split())) for line in outfile.read_text().splitlines()], None


def compare(name, sectors, got, reference='rtl'):
    expected = Decoder(reference).decode(sectors)
    mismatches = [i for i, (a, b) in enumerate(zip(got, expected)) if a != b]
    exact = len(got) == len(expected) and not mismatches
    detail = f'{len(got)} frames'
    for label, mode in (('vs psx-spx', 'psx-spx'), ('on the RTL taps', 'psx-spx-taps')):
        differ = [abs(a[c] - b[c]) for a, b in zip(got, Decoder(mode).decode(sectors)) for c in (0, 1)]
        off = sum(1 for d in differ if d)
        detail += f'; {label}: {off}/{len(differ)} samples differ, max {max(differ) if differ else 0} LSB'
    if not exact:
        first = mismatches[0] if mismatches else min(len(got), len(expected))
        detail += f'; first mismatch at frame {first}: got {got[first:first + 1]} expected {expected[first:first + 1]}'
    return exact, detail


FORMATS = {  # coding byte -> output frames per sector
    0x01: 2352,  # stereo 37.8 kHz 4-bit
    0x00: 4704,  # mono 37.8 kHz 4-bit
    0x11: 1176,  # stereo 37.8 kHz 8-bit
    0x04: 9408,  # mono 18.9 kHz 4-bit
    0x05: 4704,  # stereo 18.9 kHz 4-bit
}


def main():
    directory = ROOT / 'build/sim/cd-xa'
    upstream_text = (UPSTREAM / 'rtl/cd_xa.vhd').read_text()
    variant_text = history_clamp_text(upstream_text)
    fault = upstream_text.replace('filterPos <= 115', 'filterPos <= 116')
    assert fault != upstream_text
    build(directory / 'rtl', upstream_text)
    build(directory / 'fault', fault)
    build(directory / 'clamp', variant_text)
    jobs = []  # (label, bench, sectors, frames per sector, must match, reference)
    synthetic = int(os.environ.get('PSX_XA_SYNTHETIC', '2'))
    for coding, frames in FORMATS.items():
        sectors = synthetic_sectors(coding, synthetic, coding + 11)
        jobs.append((f'synthetic coding 0x{coding:02x}', 'rtl', sectors, frames, True, 'rtl'))
        jobs.append((f'cd-xa-history-clamp, synthetic coding 0x{coding:02x}', 'clamp', sectors, frames, True, 'rtl-clamp'))
    real = ffvii_sectors(int(os.environ.get('PSX_XA_SECTORS', '16')))
    if real is None:
        print('SKIP FFVII sectors: disc 1 image not found (set PSX_FFVII_DISC1)')
    else:
        # Independent 4-sector streams (each starts from XA reset) so runs go in parallel.
        for first in range(0, len(real), 4):
            jobs.append((f'FFVII disc 1 file 1 channel 0, sectors {first}-{min(first + 4, len(real)) - 1}', 'rtl',
                         real[first:first + 4], 2352, True, 'rtl'))
        jobs.append(('cd-xa-history-clamp, FFVII sectors 0-3', 'clamp', real[:4], 2352, True, 'rtl-clamp'))
    jobs.append(('planted filter fault', 'fault', synthetic_sectors(0x01, 2, 5), 2352, False, 'rtl'))

    def work(index):
        label, bench, sectors, frames, _, reference = jobs[index]
        got, error = simulate(directory / bench, f'job{index}', sectors, frames)
        return (False, f'simulation failed: {error}') if got is None else compare(label, sectors, got, reference)

    failures = []
    with ThreadPoolExecutor(max_workers=min(len(jobs), os.cpu_count() or 4, 12)) as pool:
        for (label, _, _, _, expect, _), (exact, detail) in zip(jobs, pool.map(work, range(len(jobs)))):
            if expect:
                print('PASS' if exact else 'FAIL', f'{label}: {detail}', flush=True)
                if not exact:
                    failures.append(label)
            elif exact:
                print(f'FAIL {label} was not detected'); failures.append(label)
            else:
                print(f'Planted filter-coefficient fault rejected')
    if failures:
        sys.exit(f'cd_xa check failed: {failures}')

if __name__ == '__main__':
    main()
