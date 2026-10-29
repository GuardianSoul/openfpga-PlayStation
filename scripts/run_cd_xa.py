#!/usr/bin/env python3
"""XA-ADPCM check: the pinned MiSTer cd_xa decoder against Python references.

Raw XA sectors go through cd_xa (tb_cd_xa.vhd) exactly as cd_top delivers
them, and every popped 44.1 kHz stereo frame is compared with two
independent decoders written here:

- "rtl": psx-spx ADPCM with the arithmetic cd_xa uses ((sum+32)/64 truncated
  toward zero, unclamped 24-bit history, zigzag products truncated toward
  zero, 18.9 kHz by sample doubling). The RTL must match it exactly.
- "duckstation": DuckStation's decoder (each filter product >>6, clamped
  history, zigzag products >>15). The difference is reported, not failed:
  it measures how far the MiSTer arithmetic is from the emulator reference.

Synthetic sectors cover every format (4/8-bit, mono/stereo, 37.8/18.9 kHz,
all filters and shifts, clipping). If the FFVII disc 1 image is present
(PSX_FFVII_DISC1 or roms/), real FMV/music sectors (file 1, channel 0) run
too. A planted fault (filter 2 coefficient 115 -> 116) must fail.

The cd-xa-history-clamp variant (clamped filter history, as psx-spx,
DuckStation and Mednafen do) runs the same sectors against an "rtl-clamp"
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
POS = (0, 60, 115, 98)
NEG = (0, 0, -52, -55)
ZIGZAG = (
    (0, 0, 0, 0, 0, -0x2, 0xA, -0x22, 0x41, -0x54, 0x34, 0x9, -0x10A, 0x400, -0xA78, 0x234C, 0x6794, -0x1780, 0xBCD,
     -0x623, 0x350, -0x16D, 0x6B, 0xA, -0x10, 0x11, -0x8, 0x3, -0x1),
    (0, 0, 0, -0x2, 0, 0x3, -0x13, 0x3C, -0x4B, 0xA2, -0xE3, 0x132, -0x43, -0x267, 0xC9D, 0x74BB, -0x11B4, 0x9B8,
     -0x5BF, 0x372, -0x1A8, 0xA6, -0x1B, 0x5, 0x6, -0x8, 0x3, -0x1, 0),
    (0, 0, -0x1, 0x3, -0x2, -0x5, 0x1F, -0x4A, 0xB3, -0x192, 0x2B1, -0x39E, 0x4F8, -0x5A6, 0x7939, -0x5A6, 0x4F8,
     -0x39E, 0x2B1, -0x192, 0xB3, -0x4A, 0x1F, -0x5, -0x2, 0x3, -0x1, 0, 0),
    (0, -0x1, 0x3, -0x8, 0x6, 0x5, -0x1B, 0xA6, -0x1A8, 0x372, -0x5BF, 0x9B8, -0x11B4, 0x74BB, 0xC9D, -0x267, -0x43,
     0x132, -0xE3, 0xA2, -0x4B, 0x3C, -0x13, 0x3, 0, -0x2, 0, 0, 0),
    (-0x1, 0x3, -0x8, 0x11, -0x10, 0xA, 0x6B, -0x16D, 0x350, -0x623, 0xBCD, -0x1780, 0x6794, 0x234C, -0xA78, 0x400,
     -0x10A, 0x9, 0x34, -0x54, 0x41, -0x22, 0xA, -0x1, 0, 0x1, 0, 0, 0),
    (0x2, -0x8, 0x10, -0x23, 0x2B, 0x1A, -0xEB, 0x27B, -0x548, 0xAFA, -0x16FA, 0x53E0, 0x3C07, -0x1249, 0x80E, -0x347,
     0x15B, -0x44, -0x17, 0x46, -0x23, 0x11, -0x5, 0, 0, 0, 0, 0, 0),
    (-0x5, 0x11, -0x23, 0x46, -0x17, -0x44, 0x15B, -0x347, 0x80E, -0x1249, 0x3C07, 0x53E0, -0x16FA, 0xAFA, -0x548,
     0x27B, -0xEB, 0x1A, 0x2B, -0x23, 0x10, -0x8, 0x2, 0, 0, 0, 0, 0, 0),
)


def tdiv(a, b):
    """Integer division truncating toward zero (VHDL '/')."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def wrap(value, bits):
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def clamp16(value):
    return max(-32768, min(32767, value))


class Decoder:
    def __init__(self, mode):
        self.mode = mode
        self.last = [[0, 0], [0, 0]]
        self.ring = [[0] * 32, [0] * 32]
        self.p = 0
        self.six = 0

    def adpcm(self, sector):
        """One sector -> list of (left, right) samples at the sector's input rate."""
        coding = sector[19]
        eight = (coding >> 4) & 3 == 1
        stereo = coding & 3 == 1
        left, right = [], []
        for chunk in range(18):
            base = 24 + chunk * 128
            for block in range(4 if eight else 8):
                header = sector[base + 4 + block]
                shift = header & 15
                if shift > 12:
                    shift = 9
                fpos, fneg = POS[(header >> 4) & 3], NEG[(header >> 4) & 3]
                channel = 1 if stereo and block & 1 else 0
                last = self.last[channel]
                for word in range(28):
                    if eight:
                        raw = sector[base + 16 + block + word * 4]
                        value = wrap(raw << 8, 16) >> shift
                    else:
                        raw = sector[base + 16 + block // 2 + word * 4]
                        nibble = raw >> 4 if block & 1 else raw & 15
                        value = wrap(nibble << 12, 16) >> shift
                    if self.mode.startswith('rtl'):
                        sample = wrap(tdiv(last[0] * fpos + last[1] * fneg + 32, 64) + value, 24)
                        out = clamp16(sample)
                        if self.mode == 'rtl-clamp':
                            sample = out
                    else:
                        sample = clamp16(value + ((last[0] * fpos) >> 6) + ((last[1] * fneg) >> 6))
                        out = sample
                    last[1], last[0] = last[0], sample
                    if stereo:
                        (right if block & 1 else left).append(out)
                    else:
                        left.append(out); right.append(out)
        return list(zip(left, right)), bool(coding & 4)

    def resample(self, frames, half_rate):
        out = []
        for frame in frames:
            for _ in range(2 if half_rate else 1):
                for c in (0, 1):
                    self.ring[c][self.p] = frame[c]
                self.p = (self.p + 1) & 31
                self.six += 1
                if self.six == 6:
                    self.six = 0
                    for table in ZIGZAG:
                        pair = []
                        for c in (0, 1):
                            total = 0
                            for i, tap in enumerate(table):
                                product = self.ring[c][(self.p - i) & 31] * tap
                                total += tdiv(product, 0x8000) if self.mode.startswith('rtl') else product >> 15
                            pair.append(clamp16(total))
                        out.append(tuple(pair))
        return out

    def decode(self, sectors):
        out = []
        for sector in sectors:
            frames, half = self.adpcm(sector)
            out += self.resample(frames, half)
        return out


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
    duck = Decoder('duckstation').decode(sectors)
    mismatches = [i for i, (a, b) in enumerate(zip(got, expected)) if a != b]
    exact = len(got) == len(expected) and not mismatches
    differ = [abs(a[c] - b[c]) for a, b in zip(got, duck) for c in (0, 1)]
    off = sum(1 for d in differ if d)
    detail = (f'{len(got)} frames; vs DuckStation arithmetic: {off}/{len(differ)} samples differ, '
              f'max {max(differ) if differ else 0} LSB')
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
