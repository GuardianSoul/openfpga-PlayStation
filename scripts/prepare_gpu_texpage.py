#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""GPU texpage state as a PS1 keeps it (ps1-tests gpu/gp0-e1, core 5/10).

- GP1(09h) "allow texture disable" was a no-op, so E1 bit 11 (and a textured
  primitive's texpage bit 11) always disabled texturing. A PS1 stores and
  uses that bit only while GP1(09h) bit 0 is set; GP1(00h) clears it.
- A textured polygon's texpage word sets E1 bits 0-8 (and 11 when allowed)
  for drawing, but GPUSTAT kept showing the last GP0(E1h) values. GPUSTAT
  bits 0-8 and 15 now follow the polygon's texpage, as on a PS1.

Guest-visible: GPUSTAT reads and texture disable only. A game that sets
texpage bit 11 without GP1(09h) now draws textured, as on a PS1.
Usage: prepare_gpu_texpage.py PROJECT  (post-prepare step; patches every
gpu.vhd the project's apf_top.qsf compiles)
"""
import re
import sys
from pathlib import Path

TAG = 'prepare_gpu_texpage'
EDITS = (
    ('signal', '   signal GPUSTAT_TextureDisable    : std_logic;\n',
     '   signal GPUSTAT_TextureDisable    : std_logic;\n'
     f"   signal texDisableAllow           : std_logic := '0';  -- {TAG}: GP1(09h) bit 0\n"),
    ('GP1(09h)', '                     when 16#09# => -- Allow texture disable\n                        -- todo\n',
     '                     when 16#09# => -- Allow texture disable\n'
     f'                        texDisableAllow <= bus_dataWrite(0); -- {TAG}\n'),
    ('GP1(00h)', "                     when 16#00# => -- reset\n                        softReset    <= '1';\n",
     "                     when 16#00# => -- reset\n                        softReset    <= '1';\n"
     f"                        texDisableAllow <= '0'; -- {TAG}\n"),
    ('polygon texpage', '               drawMode(11)         <= poly_drawModeRec(11);\n',
     f'               drawMode(11)         <= poly_drawModeRec(11) and texDisableAllow; -- {TAG}\n'
     '               GPUSTAT_TextPageX      <= std_logic_vector(poly_drawModeRec(3 downto 0));\n'
     '               GPUSTAT_TextPageY      <= poly_drawModeRec(4);\n'
     '               GPUSTAT_Transparency   <= std_logic_vector(poly_drawModeRec(6 downto 5));\n'
     '               GPUSTAT_TextPageColors <= std_logic_vector(poly_drawModeRec(8 downto 7));\n'
     '               GPUSTAT_TextureDisable <= poly_drawModeRec(11) and texDisableAllow;\n'),
    ('GP0(E1h)', '                  GPUSTAT_TextureDisable <= fifoIn_Dout(11);\n'
                 '                  drawMode               <= unsigned(fifoIn_Dout(13 downto 0));\n',
     f'                  GPUSTAT_TextureDisable <= fifoIn_Dout(11) and texDisableAllow; -- {TAG}\n'
     '                  drawMode               <= unsigned(fifoIn_Dout(13 downto 0));\n'
     '                  drawMode(11)           <= fifoIn_Dout(11) and texDisableAllow;\n'),
)


def transform(text):
    if TAG in text:
        return text
    for what, old, new in EDITS:
        if text.count(old) != 1:
            raise ValueError(f'{what}: expected exactly one match, found {text.count(old)}')
        text = text.replace(old, new)
    return text


def apply(project):
    project = Path(project)
    qsf = (project / 'apf_top.qsf').read_text()
    gpus = sorted({Path(p) for p in re.findall(r'VHDL_FILE "([^"]*/gpu\.vhd)"', qsf)})
    if not gpus:
        raise SystemExit(f'{TAG}: the project compiles no gpu.vhd')
    for gpu in gpus:
        try:
            gpu.write_text(transform(gpu.read_text()))
        except ValueError as error:
            raise SystemExit(f'{TAG}: {gpu}: {error}')
    return gpus


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.split('Usage: ')[1])
    for p in apply(sys.argv[1]):
        print('patched', p)
