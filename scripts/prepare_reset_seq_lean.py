#!/usr/bin/env python3
"""Reset-only sequencer without its unobserved DDR address arithmetic.

In the release-init reset sequencer (prepare_release_init.py) the DDR port
carries a request only for the four header reads (Softmap_SaveState_ADDR +
k * SAVESTATESIZE) and for the VRAM / off-chip SPU clear writes, whose
addresses come from RAMAddrNext. Three other assignments move ddr3_ADDR
while ddr3_RD and ddr3_WE are both low: savestate_address after the last
header, savestate_address + savetypes(n).offset at each region start, and
ddr3_ADDR_save + 2 after each packet (with the ddr3_ADDR_save copy). The
DDR consumer (psx_vram_psram) reads ddr_addr only with ddr_rd or ddr_we up
(sel_bus needs take_bus), so those values are never seen. This step drops
them: ddr3_ADDR keeps its last value instead. Every request, strobe and
payload is unchanged; tb_mister_reset_only compares the address only with
a request up (make test-reset-seq-lean).
Usage: prepare_reset_seq_lean.py PROJECT (after prepare_pocket_early.py)
"""
from pathlib import Path
import re
import sys

EDITS = [
    # last header read answered: the next region start sets the address anyway
    ('''                  if (ssValidCnt = 3) then
                     ddr3_ADDR      <= std_logic_vector(to_unsigned(savestate_address, 26));
                     state          <= LOAD_HEADERAMOUNTCHECK;
''', '''                  if (ssValidCnt = 3) then
                     state          <= LOAD_HEADERAMOUNTCHECK;
'''),
    ('''                  ddr3_ADDR      <= std_logic_vector(to_unsigned(savestate_address + savetypes(savetype_counter).offset, 26));
                  ddr3_RD        <= not resetMode;
''', '''                  ddr3_RD        <= not resetMode;
'''),
    ('               ddr3_ADDR_save <= ddr3_ADDR;\n', ''),
    ('''                  ddr3_ADDR  <= std_logic_vector(unsigned(ddr3_ADDR_save) + 2);
''', ''),
    ('   signal ddr3_ADDR_save      : std_logic_vector(25 downto 0) := (others => \'0\');\n', ''),
]


def lean_text(text):
    if "constant resetMode : std_logic := '1';" not in text or 'clear_finished' not in text:
        raise ValueError('reset-seq-lean needs the release-init reset-only sequencer')
    for old, new in EDITS:
        if text.count(old) != 1: raise ValueError('reset-seq-lean: savestates.vhd changed: ' + old.strip()[:60])
        text = text.replace(old, new)
    if 'ddr3_ADDR_save' in text or 'savestate_address +' in text:
        raise ValueError('reset-seq-lean: an address copy is left')
    return text


def prepare_project(project):
    project = Path(project).resolve(); qsf = project/'apf_top.qsf'; q = qsf.read_text()
    hits = re.findall(r'"([^"]*/savestates\.vhd)"', q)
    if len(hits) != 1: raise ValueError('reset-seq-lean: expected one savestates.vhd in the project')
    out = project/'reset-seq-lean'/'savestates.vhd'; out.parent.mkdir(exist_ok=True)
    out.write_text('-- Modified by scripts/prepare_reset_seq_lean.py: see its docstring.\n' + lean_text(Path(hits[0]).read_text()))
    qsf.write_text(q.replace(f'"{hits[0]}"', f'"{out}"'))
    return out


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1])
    print(prepare_project(sys.argv[1]))
