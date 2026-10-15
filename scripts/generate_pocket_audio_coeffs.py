#!/usr/bin/env python3
"""Generate an integer windowed-sinc bank for the Pocket audio converter.

128 source taps, 160 fractional phases plus the phase-1 endpoint. A second
ROM read supplies the next phase for fine interpolation. Writes build assets,
never BIOS/disc data. This is an output converter, not SPU reverb arithmetic.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path


TAPS = 128
PHASES = 160
Q = 17
BETA = 8.6
UNITY_SENTINEL = 1 << Q


def i0(x):
    total = term = 1.0
    for k in range(1, 100):
        term *= (x*x/4)/(k*k)
        total += term
        if term < total*1e-17:
            return total
    raise ValueError('Bessel series did not converge')


def bank():
    rows = []
    denominator = i0(BETA)
    for phase in range(PHASES+1):
        weights = []
        for tap in range(TAPS):
            distance = tap + phase/PHASES - TAPS/2
            if abs(distance) > TAPS/2:
                weight = 0.0
            else:
                sinc = 1.0 if distance == 0 else math.sin(math.pi*distance)/(math.pi*distance)
                window = i0(BETA*math.sqrt(max(0.0, 1-(distance/(TAPS/2))**2)))/denominator
                weight = sinc*window
            weights.append(weight)
        gain = sum(weights)
        values = [round(value/gain*(1 << Q)) for value in weights]
        # Every stored phase preserves DC exactly. Per-tap fine-interpolation
        # rounding has a small separately measured gain error.
        values[max(range(TAPS), key=lambda i: abs(values[i]))] += (1 << Q)-sum(values)
        if sum(values) != 1 << Q or any(not -(1 << Q) < v <= 1 << Q for v in values):
            raise ValueError('Coefficient range/DC invariant failed')
        rows.append(values)
    # At the endpoint the kernel shifts by exactly one source sample.
    if rows[-1] != rows[0][1:]+[0]:
        raise ValueError('Fractional-phase endpoint is discontinuous')
    delta = max(abs(b-a) for arow, brow in zip(rows, rows[1:]) for a, b in zip(arow, brow))
    if delta >= 4096:
        raise ValueError('Adjacent coefficient delta does not fit signed 13 bits')
    return rows, delta


def generate(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    rows, delta = bank()
    path = directory/'audio-sinc.hex18'
    path.write_text(''.join(f'{value & 0x3ffff:05x}\n' for row in rows for value in row))
    # All phases except the self-mirror midpoint are exact reversed pairs.
    # Keep midpoint row 80 directly: its DC residual is placed on one tap.
    # RTL selects either tap order for each ROM port, preserving every integer
    # coefficient including that midpoint, rather than changing quantization.
    if any(rows[PHASES-p] != rows[p][::-1] for p in range(PHASES+1) if p != 80):
        raise ValueError('Coefficient-bank mirror invariant failed')
    compact = directory/'audio-sinc-mirror.hex18'
    compact.write_text(''.join(f'{value & 0x3ffff:05x}\n' for row in rows[:81] for value in row))
    record = {'taps':TAPS,'phases':PHASES,'endpoint_phase':PHASES,'coefficient_q':Q,
        'kaiser_beta':BETA,'group_delay_input_samples':TAPS//2,
        'group_delay_ms_at_44100':1000*(TAPS//2)/44100,
        'coefficient_bits':18,'unity_encoding':hex(UNITY_SENTINEL),
        'unity_encoding_semantics':'Positive 1.0; the otherwise unused most-negative encoding',
        'coefficient_words':(PHASES+1)*TAPS,'maximum_adjacent_delta':delta,
        'all_phase_sums':1 << Q,'endpoint_shift_exact':True,
        'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'compact_path':str(compact),'compact_sha256':hashlib.sha256(compact.read_bytes()).hexdigest(),
        'compact_words':81*TAPS,'mirror_rows_exact_except_direct_midpoint':True,
        'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'conversion_ratio':{'input':44100,'output':48000,'source_step':147,'phase_denominator':160},
        'fine_interpolation_fraction_bits':16,'quality_verified':False,
        'rtl_verified':False,'fit_verified':False,'hardware_verified':False}
    (directory/'coefficients.json').write_text(json.dumps(record,indent=2)+'\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    print(json.dumps(generate(args.output),indent=2))


if __name__ == '__main__':
    main()
