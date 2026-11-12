#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""gpu_poly sets up its colour/texture bases in the per-pixel work registers.

gpu_poly kept five 20-bit base registers (base_R/G/B/U/V) with their own
adders: CALCBOUNDARY loads them from the core vertex, CALCCOLOR4 and
CALCTEXTURE4 add the gradient products to them one per step, and every
scanline copies them into work_R/G/B/U/V. The work registers are idle during
setup and already add mulResult32clk in CALCLINEADD, so this step does the
setup in them instead (same order, same operands) and copies the five sums
into base_* once, on the first PREPAREHALF of each triangle (the state every
flat, Gouraud and textured setup reaches next). The base registers become
plain load registers: their five adders and input muxes go.

Pixels, memory traffic and timing are unchanged: the copy rides on the
existing first PREPAREHALF clock. The vertex writes, the mulStep declaration
and the CALCCOLOR2/CALCTEXTURE2 product captures pinned by
prepare_gpu_poly_vt_multicycle.py are untouched (checked).
Usage: prepare_gpu_poly_base_share.py PROJECT  (post-prepare step; patches the
gpu_poly.vhd the project's apf_top.qsf compiles)
"""
import re
import sys
from pathlib import Path

TAG = 'prepare_gpu_poly_base_share'
CHANNELS = 'RGBUV'
INIT_OLD = ''.join(f'                  base_{c} <= vt(coreVertex).{c.lower()} & x"800";\n' for c in CHANNELS)
COPY_OLD = ("               when PREPAREHALF =>\n"
            "                  state <= PREPARELINE;\n"
            "                  if (firsthalf = '1') then\n")
# Pinned by prepare_gpu_poly_vt_multicycle / run_gpu_poly_vt_multicycle.guard_text: must not change.
PINNED = ('vt(0) <= readVertex;', 'vt(1) <= readVertex;', 'vt(2) <= readVertex;',
          'muldivResult1clk <= muldivResult1;', 'muldivResult2clk <= muldivResult2;',
          '   signal mulStep             : integer range 0 to 7;')


def section(text, state, following):
    start = text.index(f'               when {state} =>\n')
    return start, text.index(f'               when {following} =>\n', start)


def transform(text):
    if TAG in text:
        return text
    pinned = {p: text.count(p) for p in PINNED}
    if text.count(INIT_OLD) != 1:
        raise ValueError('base initialisation: expected exactly one match')
    if text.count(COPY_OLD) != 1:
        raise ValueError('first PREPAREHALF: expected exactly one match')
    text = text.replace(INIT_OLD, f'                  -- {TAG}: bases are set up in the work registers\n' +
                        INIT_OLD.replace('base_', 'work_'))
    for state, following, channels in (('CALCCOLOR4', 'CALCTEXTURE1', 'RGB'), ('CALCTEXTURE4', 'PREPAREHALFWAIT', 'UV')):
        start, end = section(text, state, following)
        block = text[start:end]
        for c in channels:
            old = f'base_{c} <= base_{c} + mulResult32clk;'
            if block.count(old) < 1:
                raise ValueError(f'{state}: no {old}')
            block = block.replace(old, f'work_{c} <= work_{c} + mulResult32clk;')
        if 'base_' in block:
            raise ValueError(f'{state}: unexpected base register use')
        text = text[:start] + block + text[end:]
    text = text.replace(COPY_OLD, COPY_OLD +
                        f'                     -- {TAG}: the five set-up sums, once per triangle\n' +
                        ''.join(f'                     base_{c} <= work_{c};\n' for c in CHANNELS))
    writes = re.findall(r'\bbase_[RGBUV] <= ([^;]*);', text)
    if sorted(writes) != sorted(f'work_{c}' for c in CHANNELS):
        raise ValueError(f'base registers still have other writers: {writes}')
    if {p: text.count(p) for p in PINNED} != pinned:
        raise ValueError('multicycle-pinned text changed')
    return text


ENTRY_OLD = {'CALCCOLOR1': '               when CALCCOLOR1 =>\n                  mulStep <= 0;\n',
             'CALCTEXTURE1': '               when CALCTEXTURE1 =>\n                  mulStep <= 0;\n'}


def transform2(text):
    """Variant 2: base_* keep their vertex load; work_* gain no new source.

    CALCCOLOR1 / CALCTEXTURE1 load work <= base (a source work already has for every
    scanline), CALCCOLOR4 / CALCTEXTURE4 add the products in work, and the first
    PREPAREHALF copies back the channels the triangle set up (RGB when shaded, UV when
    textured). base_* then have two sources (vertex, work) and no adder."""
    if TAG in text:
        return text
    pinned = {p: text.count(p) for p in PINNED}
    if text.count(COPY_OLD) != 1:
        raise ValueError('first PREPAREHALF: expected exactly one match')
    for state, channels in (('CALCCOLOR1', 'RGB'), ('CALCTEXTURE1', 'UV')):
        if text.count(ENTRY_OLD[state]) != 1:
            raise ValueError(f'{state}: expected exactly one match')
        text = text.replace(ENTRY_OLD[state], ENTRY_OLD[state] +
                            f'                  -- {TAG}: set up in the work registers\n' +
                            ''.join(f'                  work_{c} <= base_{c};\n' for c in channels))
    for state, following, channels in (('CALCCOLOR4', 'CALCTEXTURE1', 'RGB'), ('CALCTEXTURE4', 'PREPAREHALFWAIT', 'UV')):
        start, end = section(text, state, following)
        block = text[start:end]
        for c in channels:
            old = f'base_{c} <= base_{c} + mulResult32clk;'
            if block.count(old) < 1:
                raise ValueError(f'{state}: no {old}')
            block = block.replace(old, f'work_{c} <= work_{c} + mulResult32clk;')
        if re.search(r'base_[RGBUV] <=', block):
            raise ValueError(f'{state}: unexpected base register write')
        text = text[:start] + block + text[end:]
    text = text.replace(COPY_OLD, COPY_OLD +
                        f'                     -- {TAG}: the set-up sums, once per triangle\n'
                        "                     if (rec_shading = '1') then\n" +
                        ''.join(f'                        base_{c} <= work_{c};\n' for c in 'RGB') +
                        '                     end if;\n'
                        "                     if (rec_texture = '1') then\n" +
                        ''.join(f'                        base_{c} <= work_{c};\n' for c in 'UV') +
                        '                     end if;\n')
    if {p: text.count(p) for p in PINNED} != pinned:
        raise ValueError('multicycle-pinned text changed')
    return text


def apply(project, variant=1):
    project = Path(project).resolve()
    qsf = (project / 'apf_top.qsf').read_text()
    polys = sorted({Path(p) for p in re.findall(r'VHDL_FILE "([^"]*/gpu_poly\.vhd)"', qsf)})
    if len(polys) != 1:
        raise SystemExit(f'{TAG}: expected one compiled gpu_poly.vhd, found {len(polys)}')
    poly = polys[0].resolve()
    if project not in poly.parents:
        raise SystemExit(f'{TAG}: {poly} is outside the project; refusing to patch a shared source')
    try:
        poly.write_text((transform2 if variant == 2 else transform)(poly.read_text()))
    except ValueError as error:
        raise SystemExit(f'{TAG}: {poly}: {error}')
    return poly


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--variant')]
    variant = 2 if '--variant=2' in sys.argv[1:] else 1
    if len(args) != 1:
        raise SystemExit(__doc__.split('Usage: ')[1])
    print('patched', apply(args[0], variant))
