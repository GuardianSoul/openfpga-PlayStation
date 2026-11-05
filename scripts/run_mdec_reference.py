#!/usr/bin/env python3
"""MDEC check: the pinned MiSTer mdec against independent Python models.

Two models are written here:

- "mister": the arithmetic the pinned RTL implements (psx-spx run-length
  decode, a full-precision IDCT truncated once, floored YUV products, output
  always unsigned, the colour quant table for monochrome). The RTL must
  match it word for word, which proves the bench and the reading of the RTL.
- "mednafen": Mednafen PSX's MDEC arithmetic, written from Beetle PSX
  (Mednafen PSX) mednafen/psx/mdec.c @ e57860d8 and psx-spx (dequantization
  with 4 extra bits and an 8 toward zero, IDCT matrix = scale >> 3 with each
  pass rounded (sum + 0x4000) >> 15, 9-bit wrap before clamping, its YCbCr to
  RGB rounding, signed output). The test reports how far the RTL is from it;
  the mdec-mednafen variant (prepare_mdec_mednafen.py) must equal it.

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
    """mode 'mister': the pinned MiSTer arithmetic (psx-spx run-length decode,
    full-precision IDCT truncated once, YUV products floored, output always
    unsigned, colour quant table for monochrome). mode 'mednafen': Mednafen
    PSX's MDEC arithmetic, written from Beetle PSX (Mednafen PSX)
    mednafen/psx/mdec.c @ e57860d8 (scalar paths) and psx-spx: WriteImageData's
    dequantization, IDCT_1D_Pass1/Pass2 with IDCTMatrix = scale >> 3,
    Mask9ClampS8, YCbCr_to_RGB, RGB_to_RGB555 and EncodeImage's packing.
    Both keep the pinned core's block parsing and output order."""
    def __init__(self, mode='mednafen'):
        self.mode = mode
        self.iq_y = [0] * 64
        self.iq_uv = [0] * 64
        self.scale = [0] * 64
        self.upload = [0] * 64   # scale values in upload order (Mednafen's input)

    def set_quant(self, data):
        raw = b''.join(w.to_bytes(4, 'little') for w in data)
        self.iq_y = list(raw[:64])
        if len(raw) > 64:
            self.iq_uv = list(raw[64:128])

    def set_scale(self, data):
        values = [sext(h, 16) for w in data for h in (w & 0xFFFF, w >> 16)]
        self.upload = values
        self.scale = [values[x * 8 + y] for y in range(8) for x in range(8)]

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

    # --- Mednafen (Beetle PSX mdec.c @ e57860d8, scalar paths) ---
    def rle_mednafen(self, halfwords, qt):
        """WriteImageData's arithmetic: Coeff[ZigZag[k]] for each value, with the
        pinned core's run-length parsing (one block per call)."""
        def coefficient(k, n, qscale):
            ci = sext(n, 10)
            q = qt[0] if k == 0 else qscale * qt[k]   # DC: the quant byte alone
            if q:
                p = ci * q if k == 0 else (ci * q) >> 3
                v = p * 16 + ((8 if ci < 0 else -8) if ci else 0)
            else:
                v = ci * 32
            return clamp(v, -0x4000, 0x3FFF)
        while halfwords[0] == 0xFE00:
            halfwords.pop(0)
        n = halfwords.pop(0)
        coeff = [0] * 64
        qscale = n >> 10
        k = 0
        while True:
            coeff[ZIGZAG[k]] = coefficient(k, n, qscale)
            n = halfwords.pop(0)
            k += (n >> 10) + 1
            if k >= 63:
                if k == 63:
                    coeff[ZIGZAG[k]] = coefficient(k, n, qscale)
                return coeff

    def idct_mednafen(self, coeff):
        """IDCT_1D_Pass1 (int16 store, transposed) then IDCT_1D_Pass2 (Mask9ClampS8);
        IDCTMatrix[((i & 7) << 3) | ((i >> 3) & 7)] = upload[i] >> 3."""
        matrix = [0] * 64
        for i, value in enumerate(self.upload):
            matrix[((i & 7) << 3) | ((i >> 3) & 7)] = value >> 3
        tmp = [0] * 64
        for col in range(8):
            for x in range(8):
                s = sum(coeff[col * 8 + u] * matrix[x * 8 + u] for u in range(8))
                tmp[x * 8 + col] = sext((s + 0x4000) >> 15, 16)
        out = [0] * 64
        for col in range(8):
            for x in range(8):
                s = sum(tmp[col * 8 + u] * matrix[x * 8 + u] for u in range(8))
                out[col * 8 + x] = mask9(( s + 0x4000) >> 15)
        return out   # block[y][x] = out[y * 8 + x]

    def rle(self, halfwords, qt):
        return self.rle_mister(halfwords, qt) if self.mode == 'mister' else self.rle_mednafen(halfwords, qt)

    def idct(self, blk):
        return self.idct_mister(blk) if self.mode == 'mister' else self.idct_mednafen(blk)

    def rgb(self, y, cb, cr):
        """One pixel's (r, g, b) as unsigned bytes (value + 128)."""
        if self.mode == 'mister':
            r = clamp(y + ((cr * 359) >> 8), -128, 127)
            g = clamp(y + ((cb * -88 + cr * -183) >> 8), -128, 127)
            b = clamp(y + ((cb * 454) >> 8), -128, 127)
        else:   # YCbCr_to_RGB
            r = mask9(y + ((359 * cr + 0x80) >> 8))
            g = mask9(y + ((((-88 * cb) & ~0x1F) + ((-183 * cr) & ~0x07) + 0x80) >> 8))
            b = mask9(y + ((454 * cb + 0x80) >> 8))
        return (r + 128) & 255, (g + 128) & 255, (b + 128) & 255

    def macroblock(self, halfwords, depth, signed):
        """The pixels of one macroblock in the core's output order: 16x16
        (r, g, b) unsigned bytes for depth 2/3, 8x8 bytes for depth 0/1 (the
        byte whose top nibble is the 4-bit pixel), signed applied for Mednafen."""
        mednafen = self.mode != 'mister'
        if depth < 2:
            # monochrome: the core's colour quant table (mister), luminance (Mednafen)
            y = self.idct(self.rle(halfwords, self.iq_y if mednafen else self.iq_uv))
            if not mednafen:
                return [(v + 128) & 255 for v in y]
            out = []
            for v in y:
                if depth == 0:
                    v = min(v + 8, 127)
                out.append((v & 255) ^ (0 if signed else 0x80))
            return out
        cr = self.idct(self.rle(halfwords, self.iq_uv))
        cb = self.idct(self.rle(halfwords, self.iq_uv))
        ys = [self.idct(self.rle(halfwords, self.iq_y)) for _ in range(4)]
        pixels = []
        for py in range(16):
            for px in range(16):
                y = ys[(py >= 8) * 2 + (px >= 8)][(px & 7) + (py & 7) * 8]
                c = px // 2 + (py // 2) * 8
                r, g, b = self.rgb(y, cb[c], cr[c])
                if mednafen and signed and depth == 2:
                    r, g, b = r ^ 0x80, g ^ 0x80, b ^ 0x80
                pixels.append((r, g, b))
        return pixels

    def pack(self, pixels, depth, bit15, signed=0):
        if depth == 0:
            nibbles = [p >> 4 for p in pixels]
            return [sum(nibbles[i + j] << (4 * j) for j in range(8)) for i in range(0, len(nibbles), 8)]
        if depth == 1:
            return [sum(pixels[i + j] << (8 * j) for j in range(4)) for i in range(0, len(pixels), 4)]
        if depth == 2:
            data = [c for p in pixels for c in p]
            return [sum(data[i + j] << (8 * j) for j in range(4)) for i in range(0, len(data), 4)]
        def five(c): return min((c + 4) >> 3, 0x1F)   # RGB_to_RGB555 (and the core's roundUp8to5)
        flip = 0x4210 if signed and self.mode != 'mister' else 0   # Mednafen's signed 15-bit XOR
        halves = [((five(r) | (five(g) << 5) | (five(b) << 10)) ^ flip) | (bit15 << 15) for r, g, b in pixels]
        return [halves[i] | (halves[i + 1] << 16) for i in range(0, len(halves), 2)]


def mask9(value):
    """Mednafen's Mask9ClampS8: the low 9 bits as a signed value, clamped to -128..127."""
    return clamp(sext(value, 9), -128, 127)


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
            words = model.pack(model.macroblock(queue, depth, signed), depth, bit15, signed)
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
        print(f'PASS seed {seed}: {len(got)} words equal the MiSTer-arithmetic model; versus Mednafen '
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
            print(f'PASS mdec-mednafen seed {seed}: {len(got)} words equal the Mednafen model', flush=True)
    # Combined with mdec-idct-pipe (as in the fitted candidate).
    build(directory / 'mednafen-pipe', idct_pipe_text(mednafen_text(text)))
    ok, got, _ = run(directory / 'mednafen-pipe', 1)
    _, expected, labels = vectors(1, 'mednafen')
    if ok and not differences(got, expected, labels) and len(got) == len(expected):
        print(f'PASS mdec-mednafen + mdec-idct-pipe seed 1: {len(got)} words equal the model')
    else:
        print('FAIL mdec-mednafen + mdec-idct-pipe'); failures.append('mednafen-pipe')
    # planted fault: the second pass loses its rounding
    variant_fault = mednafen_text(text).replace('to_signed(16#4000#, 31)', 'to_signed(0, 31)')
    assert variant_fault != mednafen_text(text)
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
