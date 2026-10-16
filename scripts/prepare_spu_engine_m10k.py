#!/usr/bin/env python3
"""SPU reverb engine coefficient copy in block RAM, read on the falling edge.

spu-reverb-table keeps two copies of the 32 reverb registers: a guest
readback copy (spu-guest-m10k moved it to falling-edge block RAM) and an
engine copy in an asynchronous-read MLAB, addressed by rv_engine_addr. That
address is decoded from clk1x registers (state, reverb_count, reverbRight),
so it changes only on rising clk1x edges, and register writes land on
rising edges too. A block RAM that registers rv_engine_addr on the falling
edge in between therefore shows, at every rising edge, the word the
asynchronous read showed, including a write made on the previous rising
edge. The valid bits stay in flip-flops, as for the guest copy. Needs
spu-reverb-table and spu-guest-m10k (their falling-edge process).
"""
import argparse
from pathlib import Path


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('spu-engine-m10k: SPU source changed near: ' + old.strip().splitlines()[0][:80])
    return text.replace(old, new)


def engine_m10k_text(text):
    text = _replace(text, '   attribute ramstyle of rvTable_engine : signal is "MLAB, no_rw_check";',
                    '   attribute ramstyle of rvTable_engine : signal is "M10K, no_rw_check";\n'
                    '   signal rv_engine_ram_q : std_logic_vector(15 downto 0);   -- spu-engine-m10k')
    text = _replace(text, '         rv_guest_ram_q <= rvTable_guest(rv_guest_addr);\n',
                    '         rv_guest_ram_q <= rvTable_guest(rv_guest_addr);\n'
                    '         rv_engine_ram_q <= rvTable_engine(rv_engine_addr);   -- spu-engine-m10k\n')
    text = _replace(text, "   rv_engine_q    <= rvTable_engine(rv_engine_addr) when rvValid(rv_engine_addr) = '1' else (others => '0');",
                    "   rv_engine_q    <= rv_engine_ram_q when rvValid(rv_engine_addr) = '1' else (others => '0');")
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.write_text(engine_m10k_text(args.source.read_text()))


if __name__ == '__main__':
    main()
