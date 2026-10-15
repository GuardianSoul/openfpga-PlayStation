#!/usr/bin/env python3
"""Check the exact reverb resampler against the 39-tap model, sample by sample.

The RTL SPU (context RAM + reverb table + resampler, as in the reset-only
build) replays synthetic traces: CD audio drives the reverb input directly
with an impulse train and with full-scale random noise (clipping both
resampler clamps), under a fixed and a random reverb configuration. The
bench logs every tick's reverb input pair, decimated value, new reverb
output and both interpolated outputs; an independent model of the
Mednafen/Beetle resampler recomputes them from the logged streams alone.
Planted faults (one tap, a shift, the middle-tap offset, the channel phase,
the output feed) must each be rejected. --trace adds a recorded game trace.
"""
import argparse
from pathlib import Path
import random
import struct

from prepare_spu_reverb_resample import COEFFICIENTS
from run_spu_replay import replay
from tool_paths import ROOT

SPUCNT_CD_REVERB = 0xC085  # enable, unmute, reverb master, CD audio, CD reverb


def clamp16(value):
    return max(-32768, min(32767, value))


def check(probe):
    """Recompute the resampler from the logged streams; return mismatches and ticks."""
    lines = Path(probe).read_text().split('\n')
    inputs = ([0] * 64, [0] * 64); outputs = ([0] * 32, [0] * 32)
    errors = []; ticks = 0; nonzero = 0; clamps = 0; wet = 0; tick = None
    for line in lines:
        f = line.split()
        if not f: continue
        if f[0] == 'I':
            ch, in_l, in_r, down = map(int, f[1:])
            inputs[0].append(in_l); inputs[1].append(in_r)
            h = inputs[ch]
            total = sum(c * h[-39 + 2 * i] for i, c in enumerate(COEFFICIENTS)) + 0x4000 * h[-20]
            expect = clamp16(total >> 15); clamps += expect != total >> 15
            if down != expect: errors.append(f'tick {ticks} down ch{ch} rtl={down} model={expect}')
            tick = ch
        elif f[0] == 'O':
            fresh, ivb_l, ivb_r, up_l, up_r = map(int, f[1:])
            if fresh != tick: errors.append(f'tick {ticks} channel I={tick} O={fresh}')
            outputs[fresh].append(ivb_r if fresh else ivb_l)
            h = outputs[fresh]
            total = sum(c * h[-20 + i] for i, c in enumerate(COEFFICIENTS))
            up = clamp16(total >> 14); clamps += up != total >> 14
            other = outputs[1][-11] if fresh == 0 else outputs[0][-10]
            expect = (other, up) if fresh else (up, other)
            if (up_l, up_r) != expect: errors.append(f'tick {ticks} up rtl={(up_l, up_r)} model={expect}')
            nonzero += any(expect) or bool(down); wet += bool(ivb_l or ivb_r); ticks += 1
    return errors, ticks, nonzero, clamps, wet


def write_trace(path, samples, cd, registers):
    with open(path, 'wb') as out:
        for address, value in registers:
            out.write(struct.pack('<IBBHI', 0, 1, 0, address, value))
        for sample in range(samples):
            left, right = cd(sample)
            out.write(struct.pack('<IBBHI', sample, 3, 0, 0, ((left & 0xFFFF) << 16) | (right & 0xFFFF)))


def registers(rng):
    common = [(0x180, 0x3FFF), (0x182, 0x3FFF), (0x184, 0x7FFF), (0x186, 0x7FFF),
              (0x1B0, 0x7FFF), (0x1B2, 0x7FFF), (0x1A2, 0xF000)]
    if rng is None:
        # psx-spx "Room" preset.
        block = [0x007D, 0x005B, 0x6D80, 0x54B8, 0xBED0, 0x0000, 0x0000, 0xBA80, 0x5800, 0x5300,
                 0x04D6, 0x0333, 0x03F0, 0x0227, 0x0374, 0x01EF, 0x0334, 0x01B5, 0x0000, 0x0000,
                 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x01B4, 0x0136, 0x00B8, 0x005C,
                 0x8000, 0x8000]
    else:
        # Random coefficients; short random buffer offsets so the wet output
        # appears within the run.
        block = [rng.randrange(0x10000) if 2 <= i <= 9 or i >= 30 else rng.randrange(1, 0x40) for i in range(32)]
    return common + [(0x1C0 + 2 * i, v) for i, v in enumerate(block)] + [(0x1AA, SPUCNT_CD_REVERB)]


def cases(directory):
    rng = random.Random(0x5245)
    impulse = lambda s: ((32767, -32768) if s % 97 == 5 else (0, 0))
    noise = lambda s, r=random.Random(7): (r.choice((-32768, 32767, r.randrange(-32768, 32768))),
                                           r.choice((-32768, 32767, r.randrange(-32768, 32768))))
    for name, samples, cd, regs in (('impulse-room', 8000, impulse, registers(None)),
                                    ('noise-random', 1200, noise, registers(rng))):
        path = directory / f'{name}.trace'; write_trace(path, samples, cd, regs)
        yield name, path, samples


FAULTS = {
    'tap': ('to_signed(-2960, 16), to_signed(10246, 16)', 'to_signed(-2961, 16), to_signed(10246, 16)'),
    'shift': ('shift_right(rsAccIn - resize(newestIn, 32), 15)', 'shift_right(rsAccIn - resize(newestIn, 32), 14)'),
    'middle': ('rsRAddr <= "100" & (rsOutPosL - 10);', 'rsRAddr <= "100" & (rsOutPosL - 11);'),
    'phase': ("if (reverbRight = '1') then newestIn := rsInR; else newestIn := rsInL; end if;",
              "if (reverbRight = '0') then newestIn := rsInR; else newestIn := rsInL; end if;"),
    'feed': ('rsRAddr <= "0" & rsCh & (rsInPos - 19);', 'rsRAddr <= "0" & rsCh & (rsInPos - 18);'),
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trace', type=Path, help='Also check a recorded trace (for example the FFVII intro)')
    p.add_argument('--skip', type=int, default=0); p.add_argument('--samples', type=int, default=0)
    args = p.parse_args()
    directory = ROOT / 'build/sim/spu-reverb-resample'; directory.mkdir(parents=True, exist_ok=True)
    failures = []
    runs = list(cases(directory))
    if args.trace: runs.append(('trace', args.trace, args.samples))
    for name, path, samples in runs:
        _, _, work = replay(path, samples, 'reverb-resample', skip=args.skip if name == 'trace' else 0, name='resample-' + name)
        errors, ticks, nonzero, clamps, wet = check(work / 'resample_probe.txt')
        print(f'{name}: ticks={ticks} nonzero={nonzero} wet={wet} clamped={clamps} mismatches={len(errors)}', *errors[:3], sep='\n  ')
        expected = samples - (args.skip if name == 'trace' else 0)
        if errors or ticks < expected - 4 or not nonzero or wet < ticks // 4: failures.append(name)
        if name == 'noise-random' and clamps == 0: failures.append(name + ' (no clamping exercised)')
    name, path, samples = runs[1]
    for fault, (old, new) in FAULTS.items():
        def mutate(text, old=old, new=new):
            if text.count(old) != 1: raise SystemExit('Fault site missing: ' + fault)
            return text.replace(old, new)
        _, _, work = replay(path, samples, 'reverb-resample', name='resample-fault-' + fault, mutate=mutate)
        errors, ticks, _, _, _ = check(work / 'resample_probe.txt')
        print(f'fault {fault}: mismatches={len(errors)}')
        if not errors: failures.append('fault ' + fault + ' not detected')
    if failures: raise SystemExit('Reverb resampler check failed: ' + ', '.join(failures))
    print('Exact reverb resampler matches the 39-tap model; all planted faults rejected')


if __name__ == '__main__': main()
