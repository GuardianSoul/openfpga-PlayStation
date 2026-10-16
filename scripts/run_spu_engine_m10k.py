#!/usr/bin/env python3
"""Cycle-lockstep spu-engine-m10k against the SPU it transforms.

Same bench as spu-static-readback (tb_spu_guest_m10k: random writes and
readbacks of every SPU address, including reverb registers, while voices,
reverb and memory traffic proceed; every output compared on every clk1x
cycle, 3 seeds). A planted rising-edge read (one clk late) and a planted
wrong-entry read must fail.
"""
from prepare_spu_engine_m10k import engine_m10k_text
from run_spu_static_readback import run
from tool_paths import ROOT


def main():
    directory = ROOT / 'build/sim/spu-engine-m10k'
    failures = []
    for passed, tail in run(directory / 'variant', engine_m10k_text, (1, 2, 3)):
        print('PASS' if passed else 'FAIL', *tail)
        if not passed: failures.append('lockstep')
    faults = {
        'rising-edge': lambda t: engine_m10k_text(t).replace('         rv_engine_ram_q <= rvTable_engine(rv_engine_addr);   -- spu-engine-m10k\n', '').replace(
            "   rv_engine_q    <= rv_engine_ram_q when", "   process (clk1x) begin if rising_edge(clk1x) then rv_engine_ram_q <= rvTable_engine(rv_engine_addr); end if; end process;\n   rv_engine_q    <= rv_engine_ram_q when"),
        'address': lambda t: engine_m10k_text(t).replace('rv_engine_ram_q <= rvTable_engine(rv_engine_addr);',
                                                         'rv_engine_ram_q <= rvTable_engine((rv_engine_addr + 1) mod 32);'),
    }
    for name, fault in faults.items():
        caught = not run(directory / ('fault-' + name), fault, (1,), 2000)[0][0]
        print(f'fault {name}: {"rejected" if caught else "NOT DETECTED"}')
        if not caught: failures.append(name)
    if failures: raise SystemExit('SPU engine reverb table check failed: ' + ', '.join(failures))
    print('SPU reverb engine copy in falling-edge block RAM is cycle-identical; planted faults rejected')


if __name__ == '__main__': main()
