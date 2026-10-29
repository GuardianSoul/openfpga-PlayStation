#!/usr/bin/env python3
"""spu-reverb-operands checks: SPU lockstep of prepare_spu_reverb_operands.py
against the SPU it transforms, every output compared each clk1x.

tb_spu_guest_m10k alone does not reach the IIR steps with nonzero data in its
run length (the reverb RAM starts at zero; both planted faults passed it), so
the main check is a directed copy of it (tb_spu_reverb_lockstep, generated
here): a 16-halfword reverb ring, reverb, CD audio and all voices on, random
traffic around that setup and vIIR sometimes -32768. Three seeds there, two
of the plain random bench, and two planted IIASM faults that must fail.
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_spu_static_readback
from prepare_spu_reverb_operands import operands_text
from tool_paths import ROOT

# Directed reverb traffic: a 16-halfword ring at the top of SPU RAM (mBASE
# 0xFFFC, every offset 1..3; the address steps once per sample pair), reverb, CD audio and all voices on, so the
# IIR outputs are read back within a few dozen samples; then random traffic
# that leaves the reverb setup alone except vIIR (sometimes -32768).
SETUP = """        for i in 1 to 20 loop tick; end loop;
        reverb_setup : for k in 0 to 43 loop   -- 12 + 32 registers 0x1C0..0x1FE
            case k is
                when 0 => address := 16#1A2#; data := x"FFFC";
                when 1 => address := 16#1AA#; data := x"C085";
                when 2 => address := 16#180#; data := x"3FFF";
                when 3 => address := 16#182#; data := x"3FFF";
                when 4 => address := 16#184#; data := x"4000";
                when 5 => address := 16#186#; data := x"C000";
                when 6 => address := 16#198#; data := x"FFFF";
                when 7 => address := 16#19A#; data := x"00FF";
                when 8 => address := 16#1B0#; data := x"4000";
                when 9 => address := 16#1B2#; data := x"4000";
                when 10 => address := 16#188#; data := x"FFFF";
                when 11 => address := 16#18A#; data := x"00FF";
                when others =>   -- 0x1C0..0x1FE: offsets 1..3, volumes random
                    address := 16#1C0# + 2 * (k - 12);
                    if address <= 16#1C2# or (address >= 16#1D4# and address <= 16#1FA#) then
                        data := std_logic_vector(to_unsigned(1 + rnd(3), 16));
                    else
                        data := std_logic_vector(to_unsigned(rnd(65536), 16));
                    end if;
            end case;
            bus_addr<=to_unsigned(address,10); bus_dataWrite<=data; bus_write<='1'; tick; bus_write<='0'; tick;
        end loop;
"""
REVERB_SAFE = """            if address = 16#1A2# or address = 16#1AA# or (address >= 16#180# and address <= 16#186#) or
               address = 16#198# or address = 16#19A# or address = 16#1B0# or address = 16#1B2# or
               (address >= 16#1C0# and address /= 16#1C4#) then address := 16#1A6#; end if;   -- keep the reverb setup
            if address = 16#1C4# and rnd(4) = 0 then data := x"8000"; end if;                    -- vIIR = -32768
"""


def reverb_bench(directory):
    text = (ROOT / 'sim/reference/tb_spu_guest_m10k.vhd').read_text()
    text = text.replace('tb_spu_guest_m10k', 'tb_spu_reverb_lockstep')
    anchor = "        reset<='0';\n        for i in 1 to 20 loop tick; end loop;\n"
    if text.count(anchor) != 1: raise ValueError('tb_spu_guest_m10k start changed')
    text = text.replace(anchor, "        reset<='0';\n" + SETUP)
    anchor = '            if address = 16#1A8# then address := 16#1A6#; end if;                     -- no FIFO data writes\n'
    if text.count(anchor) != 1: raise ValueError('tb_spu_guest_m10k traffic changed')
    text = text.replace(anchor, anchor + REVERB_SAFE)
    out = directory / 'tb_spu_reverb_lockstep.vhd'; out.write_text(text)
    return out


def run_reverb(directory, variant_text, seeds, operations):
    """run_spu_static_readback.run with the directed reverb bench."""
    directory.mkdir(parents=True, exist_ok=True)
    return run_spu_static_readback.run(directory, variant_text, seeds, operations, bench=reverb_bench(directory),
                                       top='tb_spu_reverb_lockstep')


OPERATIONS = 30000   # about 110 samples: the 32-sample ring comes round three times

FAULTS = {
    'iiasm-offset': ('soundmul2 <= to_signed(32768, 18) - resize(signed(rv_engine_q), 18);',
                     'soundmul2 <= to_signed(32767, 18) - resize(signed(rv_engine_q), 18);'),
    'operand-value': ('soundmul1 <= resize(reverbReadval4, 24);   -- spu-reverb-operands: factors swapped',
                      'soundmul1 <= resize(reverbReadval3, 24);   -- spu-reverb-operands: factors swapped'),
}


def main():
    directory = ROOT / 'build/sim/spu-reverb-operands'
    failures = []
    for name, (old, new) in FAULTS.items():
        def fault(text, old=old, new=new):
            text = operands_text(text)
            if old not in text: raise ValueError('fault anchor changed: ' + name)
            return text.replace(old, new)   # every occurrence (both IIR steps for the offset)
        caught = not run_reverb(directory / ('fault-' + name), fault, (1,), OPERATIONS)[0][0]
        print(f'fault {name}:', 'caught' if caught else 'NOT DETECTED', flush=True)
        if not caught: failures.append(name)
    for passed, tail in run_reverb(directory / 'reverb', operands_text, (1, 2, 3), OPERATIONS):
        print('reverb lockstep:', 'PASS' if passed else 'FAIL', *tail, flush=True)
        if not passed: failures.append('reverb lockstep')
    for passed, tail in run_spu_static_readback.run(directory / 'random', operands_text, (1, 2)):
        print('random lockstep:', 'PASS' if passed else 'FAIL', *tail, flush=True)
        if not passed: failures.append('random lockstep')
    if failures: raise SystemExit('spu-reverb-operands check failed: ' + ', '.join(failures))
    print('PASS spu-reverb-operands: directed reverb lockstep on 3 seeds, random lockstep on 2, planted faults caught')


if __name__ == '__main__':
    main()
