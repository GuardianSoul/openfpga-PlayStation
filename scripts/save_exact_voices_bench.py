#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""CPU round-trip bench model of the exact-voices save port (PXS9) for
run_save_project_cpu.py --staged-exact-voices.

A text transform on the PXS6 port model (prepare_spu_ring_paired.cpu_bench): the
circulating ring is 8 words at port 32-39 instead of 16 at 32-47, and port words 7 and
40-62 are not kept (prepare_spu_exact_voices.py: the routine's port table is
32-39, 256-303, 320-369, 384-479). The model's guest-read words move into the 8-word
ring; as in the PXS6 model they only feed the routine's staging copy and the dump.
AUTHORED MODEL of the port, as the PXS6 one: the SPU itself is qualified on its own
bench (scripts/run_spu_exact_voices.py). The routine side of the port is real.
"""

# sha256 of the exact-voices SPU of the big build's graph (build/ss/d2/save-states/spu.vhd)
SPU_SHAS = {'e6fc7fe931724f5015d19d81744b13d69b8b5eb0f559cb20c107f2aef4c5a433'}


def cpu_bench(text):
    """text: the bench after prepare_spu_ring_paired.cpu_bench -> the PXS9 model."""
    def swap(old, new, count):
        nonlocal text
        if text.count(old) != count:
            raise ValueError(f'exact-voices bench anchor: expected {count}, found {text.count(old)}: {old[:80]}')
        text = text.replace(old, new)
    for word in ('ss(37)', 'ss(38)', 'ss(39)'):
        if word in text:
            raise ValueError('exact-voices bench: the PXS6 model already uses ' + word)
    swap('return n = 7 or (n >= 32 and n <= 62) or', 'return (n >= 32 and n <= 39) or', 1)
    swap('elsif ss_adr >= 32 and ss_adr <= 47 then', 'elsif ss_adr >= 32 and ss_adr <= 39 then', 1)
    swap('for k in 32 to 46 loop ss(k) := ss(k+1); end loop;', 'for k in 32 to 38 loop ss(k) := ss(k+1); end loop;', 1)
    for old, new in (('ss(47)', 'ss(39)'), ('ss(46)', 'ss(38)'), ('ss(45)', 'ss(37)')):
        if old not in text:
            raise ValueError('exact-voices bench: no ' + old)
        text = text.replace(old, new)
    swap('x"36" report "Expected a PXS6 source state"', 'x"39" report "Expected a PXS9 source state"', 1)
    swap('x"35"; -- preceding indexed PXS5 format', 'x"36"; -- preceding PXS6 format', 1)
    return text
