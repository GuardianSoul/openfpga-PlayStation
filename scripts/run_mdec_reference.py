#!/usr/bin/env python3
"""MDEC check: the pinned MiSTer mdec against independent Python models.

Two models are written here:

- "mister": the arithmetic the pinned RTL implements (psx-spx run-length
  decode, a full-precision IDCT truncated once, floored YUV products, output
  always unsigned, the colour quant table for monochrome). The RTL must
  match it word for word, which proves the bench and the reading of the RTL.
- "mednafen": DuckStation's default routines (Mednafen's arithmetic: 4 extra
  coefficient bits with a +-8 bias, per-pass (sum + 0x20000) >> 18 IDCT,
  9-bit wrap before clamping, BT.601 with Mednafen's rounding). The test
  reports how far the RTL is from it.

Macroblocks carry real AC data (short and long runs, small and extreme
coefficients, qscale 0 and nonzero) with the standard PSX IDCT matrix and
random matrices, in every output depth, signed and unsigned, with and
without bit 15. Like a game, the bench waits for each command's output
before the next command.
"""
import math
import os
import random
import subprocess
import sys
from pathlib import Path

from prepare_mdec_idct_pipe import idct_pipe_text
from prepare_mdec_mednafen import mednafen_text
from tool_paths import ROOT, find_tool

UPSTREAM = ROOT / 'references/PSX_MiSTer'
ZIGZAG = (0, 8, 1, 2, 9, 16, 24, 17, 10, 3, 4, 11, 18, 25, 32, 40, 33, 26, 19, 12, 5, 6, 13, 20, 27, 34, 41, 48, 56,
          49, 42, 35, 28, 21, 14, 7, 15, 22, 29, 36, 43, 50, 57, 58, 51, 44, 37, 30, 23, 31, 38, 45, 52, 59, 60, 53,
          46, 39, 47, 54, 61, 62, 55, 63)
ZAGZIG = (0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7,
          14, 21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46,
          53, 60, 61, 54, 47, 55, 62, 63)
WORDS = {0: 8, 1: 16, 2: 192, 3: 128}


def tdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def sext(value, bits):
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def clamp(value, low, high):
    return max(low, min(high, value))


def standard_scale():
    table = []
    for k in range(8):
        for n in range(8):
            c = math.sqrt(0.5) if k == 0 else 1.0
            table.append(clamp(round(32768 * c * math.cos((2 * n + 1) * k * math.pi / 16)), -32768, 32767))
    return table


class Model:
    """mode 'mednafen': DuckStation's default routines. mode 'mister': the
    pinned MiSTer arithmetic (psx-spx run-length decode, full-precision IDCT
    truncated once, YUV products floored, output always unsigned, colour
    quant table for monochrome)."""
    def __init__(self, mode='mednafen'):
        self.mode = mode
        self.iq_y = [0] * 64
        self.iq_uv = [0] * 64
        self.scale = [0] * 64

    def set_quant(self, data):
        raw = b''.join(w.to_bytes(4, 'little') for w in data)
        self.iq_y = list(raw[:64])
        if len(raw) > 64:
            self.iq_uv = list(raw[64:128])

    def set_scale(self, data):
        values = [sext(h, 16) for w in data for h in (w & 0xFFFF, w >> 16)]
        self.scale = [values[x * 8 + y] for y in range(8) for x in range(8)]

    def rle(self, halfwords, qt):
        if self.mode == 'mister':
            return self.rle_mister(halfwords, qt)
        while halfwords[0] == 0xFE00:
            halfwords.pop(0)
        n = halfwords.pop(0)
        blk = [0] * 64
        qscale = n >> 10
        val = sext(n, 10)
        coeff = (val << 5) if qscale == 0 else (((val * qt[0]) << 4) + ((8 if val < 0 else -8) if val else 0))
        blk[ZIGZAG[0]] = clamp(coeff, -0x4000, 0x3FFF)
        k = 0
        while True:
            n = halfwords.pop(0)
            k += (n >> 10) + 1
            if k < 64:
                val = sext(n, 10)
                scq = qscale * qt[k]
                coeff = (val << 5) if scq == 0 else ((((val * scq) >> 3) << 4) + ((8 if val < 0 else -8) if val else 0))
                blk[ZIGZAG[k]] = clamp(coeff, -0x4000, 0x3FFF)
            if k >= 63:
                return blk

    def rle_mister(self, halfwords, qt):
        while halfwords[0] == 0xFE00:
            halfwords.pop(0)
        n = halfwords.pop(0)
        blk = [0] * 64
        qscale = n >> 10
        k = 0
        while True:
            val = sext(n, 10)
            if qscale == 0:
                value = val * 2
            elif k == 0:
                value = val * qt[0]
            else:
                value = tdiv(val * qt[k] * qscale + 4, 8)
            blk[k if qscale == 0 else ZAGZIG[k]] = clamp(value, -1024, 1023)
            n = halfwords.pop(0)
            k += (n >> 10) + 1
            if k >= 63:
                if k == 63:
                    val = sext(n, 10)
                    value = val * 2 if qscale == 0 else tdiv(val * qt[k] * qscale + 4, 8)
                    blk[k if qscale == 0 else ZAGZIG[k]] = clamp(value, -1024, 1023)
                return blk

    def idct_mister(self, blk):
        m = [self.scale[y * 8 + x] for x in range(8) for y in range(8)]  # as uploaded: m[k*8+n]
        temp = [0] * 64
        for x in range(8):
            for y in range(8):
                temp[x + y * 8] = sext(sum(blk[u * 8 + x] * m[u * 8 + y] for u in range(8)), 30)
        out = [0] * 64
        for x in range(8):
            for y in range(8):
                out[x + y * 8] = clamp(sum(temp[u + y * 8] * m[u * 8 + x] for u in range(8)) >> 32, -128, 127)
        return out

    def idct(self, blk):
        if self.mode == 'mister':
            return self.idct_mister(blk)
        def row(src, r, m):
            total = sum(src[r * 8 + i] * self.scale[m * 8 + i] for i in range(8))
            return sext((total + 0x20000) >> 18, 16)
        temp = [0] * 64
        for x in range(8):
            for y in range(8):
                temp[y * 8 + x] = row(blk, x, y)
        out = [0] * 64
        for x in range(8):
            for y in range(8):
                out[x * 8 + y] = clamp(sext(row(temp, x, y), 9), -128, 127)
        return out

    def macroblock(self, halfwords, depth, signed):
        add = 0 if signed and self.mode != 'mister' else 0x80
        if depth <= 1:
            # MiSTer decodes monochrome blocks with the colour quant table.
            y = self.idct(self.rle(halfwords, self.iq_uv if self.mode == 'mister' else self.iq_y))
            return [(clamp(v, -128, 127) + add) & 0xFF for v in y]
        cr = self.idct(self.rle(halfwords, self.iq_uv))
        cb = self.idct(self.rle(halfwords, self.iq_uv))
        rgb = [0] * 256
        for index, (xx, yy) in enumerate(((0, 0), (8, 0), (0, 8), (8, 8))):
            yb = self.idct(self.rle(halfwords, self.iq_y))
            for y in range(8):
                for x in range(8):
                    c = (xx + x) // 2 + ((yy + y) // 2) * 8
                    Y, Cr, Cb = yb[y * 8 + x], cr[c], cb[c]
                    if self.mode == 'mister':
                        r = clamp(Y + ((Cr * 359) >> 8), -128, 127) + 128
                        g = clamp(Y + ((Cb * -88 + Cr * -183) >> 8), -128, 127) + 128
                        b = clamp(Y + ((Cb * 454) >> 8), -128, 127) + 128
                        rgb[xx + x + (yy + y) * 16] = r | (g << 8) | (b << 16)
                        continue
                    r = clamp(sext(Y + ((359 * Cr + 0x80) >> 8), 9), -128, 127) + add
                    g = clamp(sext(Y + ((((-88 * Cb) & ~0x1F) + ((-183 * Cr) & ~0x07) + 0x80) >> 8), 9), -128, 127) + add
                    b = clamp(sext(Y + ((454 * Cb + 0x80) >> 8), 9), -128, 127) + add
                    rgb[xx + x + (yy + y) * 16] = (r & 0xFF) | ((g & 0xFF) << 8) | ((b & 0xFF) << 16)
        return rgb

    def pack(self, pixels, depth, bit15):
        words = []
        if depth == 0:
            for i in range(0, 64, 8):
                words.append(sum((pixels[i + j] >> 4) << (4 * j) for j in range(8)))
        elif depth == 1:
            for i in range(0, 64, 4):
                words.append(sum(pixels[i + j] << (8 * j) for j in range(4)))
        elif depth == 2:
            raw = b''.join(p.to_bytes(3, 'little') for p in pixels)
            words = [int.from_bytes(raw[i:i + 4], 'little') for i in range(0, len(raw), 4)]
        else:
            def five(c): return min((c + 4) >> 3, 0x1F)
            halves = [five(p & 0xFF) | (five((p >> 8) & 0xFF) << 5) | (five(p >> 16) << 10) | (bit15 << 15) for p in pixels]
            words = [halves[i] | (halves[i + 1] << 16) for i in range(0, 256, 2)]
        return words


def block(rng, style):
    """One block's RLE halfwords (with a leading pad sometimes)."""
    out = [0xFE00] if rng.random() < 0.1 else []
    qscale = rng.choice((0, 1, 2, 8, 31, 63)) if style != 'small' else rng.choice((1, 2, 4))
    def value():
        if style == 'extreme':
            return rng.choice((-512, 511, -1, 1, -300, 300, rng.randrange(-512, 512)))
        if style == 'small':
            return rng.choice((-3, -2, -1, 1, 2, 3, rng.randrange(-20, 21)))
        return rng.randrange(-512, 512)
    out.append((qscale << 10) | (value() & 1023))
    index = 0
    while True:
        run = rng.choice((0, 0, 0, 1, 2, 5, 9, 20)) if style != 'small' else rng.choice((0, 0, 1, 3))
        if index + run + 1 > 62 or rng.random() < (0.05 if style == 'extreme' else 0.12):
            break
        index += run + 1
        out.append((run << 10) | (value() & 1023))
    out.append(0xFE00)
    return out


def vectors(seed, mode='mister'):
    """Returns (vector lines, expected words, labels per word)."""
    rng = random.Random(seed)
    model = Model(mode)
    lines = ['B 04 60000000']
    expected, labels = [], []

    def command(word, data):
        lines.append(f'B 00 {word:08x}')
        lines.extend(f'D 00 {w:08x}' for w in data)

    plans = []
    for depth in (2, 3, 1, 0):
        for signed in (0, 1):
            for bit15 in ((0, 1) if depth == 3 else (0,)):
                plans.append((depth, signed, bit15))
    rng.shuffle(plans)
    for number, (depth, signed, bit15) in enumerate(plans):
        if number % 3 == 0:
            quant = [rng.getrandbits(32) & 0x7F7F7F7F | 0x01010101 for _ in range(32)]
            if rng.random() < 0.3:
                quant[0] = (quant[0] & ~0xFF) | rng.choice((0, 2))  # DC quant byte is ignored except for qscale>0
            command(0x40000001, quant); model.set_quant(quant)
            scale = standard_scale() if number % 2 == 0 else [rng.randrange(-32768, 32768) for _ in range(64)]
            data = [(scale[i] & 0xFFFF) | ((scale[i + 1] & 0xFFFF) << 16) for i in range(0, 64, 2)]
            command(0x60000000, data); model.set_scale(data)
        style = rng.choice(('small', 'normal', 'extreme'))
        count = 3
        blocks = 6 if depth >= 2 else 1
        halfwords = [h for _ in range(count * blocks) for h in block(rng, style)]
        if len(halfwords) % 2:
            halfwords.append(0xFE00)
        data = [halfwords[i] | (halfwords[i + 1] << 16) for i in range(0, len(halfwords), 2)]
        command(0x20000000 | (depth << 27) | (signed << 26) | (bit15 << 25) | len(data), data)
        queue = list(halfwords)
        for _ in range(count):
            words = model.pack(model.macroblock(queue, depth, signed), depth, bit15)
            expected += words
            labels += [f'depth{depth} signed{signed} bit15{bit15} {style}'] * len(words)
        lines.append(f'R 00 {count * WORDS[depth]:08x}')
    return lines, expected, labels


def build(directory, mdec_text=None):
    ghdl = find_tool('ghdl'); directory.mkdir(parents=True, exist_ok=True)
    log = (directory / 'build.log').open('w')
    def call(*args): subprocess.run([ghdl, *args], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
    for library in ('mem', 'work'):
        call('-a', '--std=08', '-frelaxed', f'--work={library}', str(UPSTREAM / 'sim/system/src/mem/dpram.vhd'))
    for name in ('RamMLAB.vhd', 'SyncFifo.vhd', 'SyncFifoFallThrough.vhd'):
        source = UPSTREAM / 'sim/system/src/mem' / name
        call('-a', '--std=08', '-frelaxed', '--work=mem', str(source if source.exists() else UPSTREAM / 'rtl' / name))
    source = UPSTREAM / 'rtl/mdec.vhd'
    if mdec_text is not None:
        source = directory / 'mdec.vhd'; source.write_text(mdec_text)
    call('-a', '--std=08', '-frelaxed', str(source))
    call('-a', '--std=08', '-frelaxed', str(ROOT / 'sim/reference/tb_mdec_reference.vhd'))
    (directory / 'mdec-reference.txt').write_text('R 00 00000000\n')
    call('-e', '--std=08', '-frelaxed', 'tb_mdec_reference')


def run(directory, seed):
    ghdl = find_tool('ghdl')
    lines, expected, labels = vectors(seed)
    path = directory / f'vectors-{seed}.txt'; out = directory / f'out-{seed}.txt'
    path.write_text('\n'.join(lines) + '\n')
    result = subprocess.run([ghdl, '-r', '--std=08', '-frelaxed', 'tb_mdec_reference', f'-gVECTOR_FILE={path}',
                             f'-gOUT_FILE={out}', '--assert-level=error', '--ieee-asserts=disable'],
                            cwd=directory, capture_output=True, text=True, timeout=3600)
    text = result.stdout + result.stderr
    got = [int(l, 16) for l in out.read_text().split()] if out.exists() else []
    return result.returncode == 0 and 'PASS MDEC reference run' in text, got, text


def differences(got, expected, labels):
    bad = {}
    for i, (a, b) in enumerate(zip(got, expected)):
        if a != b:
            bad.setdefault(labels[i], []).append(i)
    return bad


def pixel_error(got, expected, labels):
    """Largest per-channel difference, by output depth (8-bit channels)."""
    worst = {}
    for a, b, label in zip(got, expected, labels):
        depth = label.split()[0]
        if depth == 'depth3':
            pairs = [((a >> s) & 31, (b >> s) & 31) for s in (0, 5, 10, 16, 21, 26)]
        elif depth == 'depth0':
            pairs = [((a >> s) & 15, (b >> s) & 15) for s in range(0, 32, 4)]
        else:
            pairs = [((a >> s) & 255, (b >> s) & 255) for s in range(0, 32, 8)]
        worst[depth] = max(worst.get(depth, 0), max(abs(x - y) for x, y in pairs))
    return worst


def main():
    directory = ROOT / 'build/sim/mdec-reference'
    build(directory)
    failures = []
    for seed in range(int(os.environ.get('PSX_MDEC_SEEDS', '3'))):
        ok, got, text = run(directory, seed)
        if not ok:
            print(f'FAIL seed {seed}: bench', *text.strip().splitlines()[-2:]); failures.append(seed); continue
        _, expected, labels = vectors(seed, 'mister')
        bad = differences(got, expected, labels)
        if len(got) != len(expected):
            print(f'FAIL seed {seed}: {len(got)} words read, model has {len(expected)}'); failures.append(seed)
        for label, indices in sorted(bad.items()):
            i = indices[0]
            print(f'FAIL seed {seed} {label}: {len(indices)} words differ from the MiSTer-arithmetic model; '
                  f'first word {i}: got {got[i]:08x} model {expected[i]:08x}')
        if bad:
            failures.append(seed); continue
        _, reference, labels = vectors(seed, 'mednafen')
        off = differences(got, reference, labels)
        print(f'PASS seed {seed}: {len(got)} words equal the MiSTer-arithmetic model; versus DuckStation/Mednafen '
              f'{sum(len(v) for v in off.values())} words differ, worst channel error by depth '
              f'{pixel_error(got, reference, labels)}', flush=True)
    # The mdec-mednafen variant must equal the Mednafen model word for word.
    text = (UPSTREAM / 'rtl/mdec.vhd').read_text()
    build(directory / 'mednafen', mednafen_text(text))
    for seed in range(int(os.environ.get('PSX_MDEC_SEEDS', '3'))):
        ok, got, bench = run(directory / 'mednafen', seed)
        _, expected, labels = vectors(seed, 'mednafen')
        bad = differences(got, expected, labels) if ok else {'bench': [0]}
        if not ok:
            print(f'FAIL mdec-mednafen seed {seed}: bench', *bench.strip().splitlines()[-2:])
        for label, indices in sorted(bad.items()):
            if label == 'bench': continue
            i = indices[0]
            print(f'FAIL mdec-mednafen seed {seed} {label}: {len(indices)} words differ; first word {i}: '
                  f'got {got[i]:08x} model {expected[i]:08x}')
        if bad or len(got) != len(expected):
            failures.append(f'mednafen-{seed}')
        else:
            print(f'PASS mdec-mednafen seed {seed}: {len(got)} words equal the DuckStation/Mednafen model', flush=True)
    # Combined with mdec-idct-pipe (as in the fitted candidate).
    build(directory / 'mednafen-pipe', idct_pipe_text(mednafen_text(text)))
    ok, got, _ = run(directory / 'mednafen-pipe', 1)
    _, expected, labels = vectors(1, 'mednafen')
    if ok and not differences(got, expected, labels) and len(got) == len(expected):
        print(f'PASS mdec-mednafen + mdec-idct-pipe seed 1: {len(got)} words equal the model')
    else:
        print('FAIL mdec-mednafen + mdec-idct-pipe'); failures.append('mednafen-pipe')
    variant_fault = mednafen_text(text).replace("to_signed(16#20000#, idct_sum'length)", "to_signed(0, idct_sum'length)")
    build(directory / 'mednafen-fault', variant_fault)
    ok, got, _ = run(directory / 'mednafen-fault', 0)
    _, expected, labels = vectors(0, 'mednafen')
    if ok and not differences(got, expected, labels):
        print('FAIL planted IDCT rounding fault was not detected'); failures.append('mednafen-fault')
    else:
        print('Planted mdec-mednafen IDCT rounding fault rejected')
    # Planted fault: Cr->R coefficient 359 -> 360.
    fault = text.replace('* 359), 17)', '* 360), 17)')
    assert fault != text
    build(directory / 'fault', fault)
    ok, got, _ = run(directory / 'fault', 0)
    _, expected, labels = vectors(0, 'mister')
    if ok and not differences(got, expected, labels):
        print('FAIL planted YUV coefficient fault was not detected'); failures.append('fault')
    else:
        print('Planted YUV coefficient fault rejected')
    if failures:
        sys.exit('MDEC reference check failed')

if __name__ == '__main__':
    main()
