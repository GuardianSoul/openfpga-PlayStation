#!/usr/bin/env python3
"""spu-reverb-work-ram checks: SPU lockstep of prepare_spu_reverb_work_ram.py
(on top of prepare_spu_reverb_operands.py) against the operands SPU, every
output compared each clk1x.

The directed reverb bench of run_spu_reverb_operands.py (16-halfword ring,
reverb, CD audio and all voices on, vIIR sometimes -32768) on three seeds,
the plain random bench on two, and three planted read-address faults (MDB's
valueOut5, the IIR_B write-back, the IIR old-value read) that must fail.
--last checks the --last variant (reverb outputs in the RAM too), with a
fourth planted fault (left and right outputs swapped). --capture adds the
capture volumes, with a fault that swaps the voice 1 and 3 words.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_spu_reverb_operands import operands_text
from prepare_spu_reverb_work_ram import work_ram_text
from run_spu_reverb_operands import OPERATIONS, run_reverb
from tool_paths import ROOT

FAULTS = {
    'mdb-value5': ('               when 9 => rv_work_raddr <= 4;', '               when 9 => rv_work_raddr <= 5;'),
    'iir-b-writeback': ('               when 1 => rv_work_raddr <= 8;', '               when 1 => rv_work_raddr <= 7;'),
    'iir-old-value': ('               when 4 => rv_work_raddr <= 2;', '               when 4 => rv_work_raddr <= 1;'),
}


LAST_FAULTS = {'last-swapped': ('               when 0 => rv_work_raddr <= 11;', '               when 0 => rv_work_raddr <= 12;')}
CAPTURE_FAULTS = {'capture-swapped': ('            rv_work_raddr <= 13;', '            rv_work_raddr <= 14;')}


def main():
    last = '--last' in sys.argv[1:]; capture = '--capture' in sys.argv[1:]
    directory = ROOT / ('build/sim/spu-reverb-work-ram' + ('-last' if last else '') + ('-capture' if capture else ''))
    variant = lambda text: work_ram_text(text, last, capture)
    failures = []
    # the lockstep compares the variant against the bench's base SPU; give
    # both the operands step by running the variant against operands_text
    def against_operands(fn):
        def run(directory, seeds, operations, reverb):
            import run_spu_static_readback as r
            original = r.guest_m10k_text
            r.guest_m10k_text = lambda t: operands_text(original(t))
            try:
                if reverb: return run_reverb(directory, fn, seeds, operations)
                return r.run(directory, fn, seeds)
            finally:
                r.guest_m10k_text = original
        return run
    for name, (old, new) in dict(FAULTS, **(LAST_FAULTS if last else {}), **(CAPTURE_FAULTS if capture else {})).items():
        def fault(text, old=old, new=new):
            text = variant(text)
            if text.count(old) != 1: raise ValueError('fault anchor changed: ' + name)
            return text.replace(old, new)
        caught = not against_operands(fault)(directory / ('fault-' + name), (1,), OPERATIONS, True)[0][0]
        print(f'fault {name}:', 'caught' if caught else 'NOT DETECTED', flush=True)
        if not caught: failures.append(name)
    for passed, tail in against_operands(variant)(directory / 'reverb', (1, 2, 3), OPERATIONS, True):
        print('reverb lockstep:', 'PASS' if passed else 'FAIL', *tail, flush=True)
        if not passed: failures.append('reverb lockstep')
    for passed, tail in against_operands(variant)(directory / 'random', (1, 2), None, False):
        print('random lockstep:', 'PASS' if passed else 'FAIL', *tail, flush=True)
        if not passed: failures.append('random lockstep')
    if failures: raise SystemExit('spu-reverb-work-ram check failed: ' + ', '.join(failures))
    print('PASS spu-reverb-work-ram: directed reverb lockstep on 3 seeds, random lockstep on 2, planted faults caught')


if __name__ == '__main__':
    main()
