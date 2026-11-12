#!/usr/bin/env python3
"""Reset-only sequencer: the clear cursor is the address (no address copies).

After prepare_reset_seq_lean.py the reset sequencer still keeps two copies
of its clear cursor RAMAddrNext: SS_Adr_2x (19 bits, copied at each
scratchpad / main RAM strobe, then copied again into the clk1x SS_Adr) and
SS_SPURAM_Adr (copied at each SPU RAM request). Both copies exist only
because the cursor moves on in the same clock as the strobe. Here it moves
on later, so the copies read the cursor itself:

- scratchpad / main RAM: the cursor steps on the clk2x edge that clears the
  strobe (clk2xIndex 0 after it was set), so the clk1x edge in between
  takes SS_Adr from the cursor's old value, as it took SS_Adr_2x. The
  scratchpad's packet end is tested on that same pending edge, so its test
  is one lower (>= 255 instead of >= 256); main RAM's is tested after its
  SDRAM write, when the step has landed.
- SPU RAM: the cursor steps when the SPU answers the request (SPURAM_done2X),
  so SS_SPURAM_Adr is the cursor while the request is out.

Every strobe, its address and payload are unchanged (run_reset_seq_lean.py
--addr compares them to the pinned sequence on every 2x tick).
Usage: prepare_reset_seq_addr.py PROJECT (after prepare_reset_seq_lean.py)
"""
from pathlib import Path
import re
import sys

MARK = 'reset-seq-addr'
EDITS = [
    # scratchpad / main RAM: no copy at the strobe, the cursor steps when the strobe clears
    ('''                  RAMAddrNext      <= RAMAddrNext + 1;
                  SS_DataWrite_2x  <= ddr3_DOUT_saved(dwordcounter * 32 + 31 downto dwordcounter * 32);
                  SS_Adr_2x        <= RAMAddrNext;
''', '''                  SS_DataWrite_2x  <= ddr3_DOUT_saved(dwordcounter * 32 + 31 downto dwordcounter * 32);
'''),
    ('''         if (clk2xIndex = '0') then
            SS_wren_2x        <= (others => '0');
''', f'''         if (clk2xIndex = '0') then
            if (SS_wren_2x(12) = '1' or SS_wren_2x(16) = '1') then
               RAMAddrNext    <= RAMAddrNext + 1;   -- {MARK}: after the clk1x edge took SS_Adr
            end if;
            SS_wren_2x        <= (others => '0');
'''),
    ('            SS_Adr       <= SS_Adr_2x;      \n            -- Reset reaches only', '            SS_Adr       <= RAMAddrNext;   -- ' + MARK + '\n            -- Reset reaches only'),
    ('            SS_Adr       <= SS_Adr_2x;      \n            SS_rden', '            SS_Adr       <= RAMAddrNext;   -- ' + MARK + '\n            SS_rden'),
    ('   signal SS_Adr_2x           : unsigned(18 downto 0) := (others => \'0\');\n', ''),
    # the scratchpad end is tested with its last step still pending
    ('      (savetype_counter = 12 and RAMAddrNext >= 256) or\n', f'      (savetype_counter = 12 and RAMAddrNext >= 255) or   -- {MARK}: the step is pending here\n'),
    # SPU RAM: the address is the cursor, which steps on the answer
    ('''               SS_SPURAM_Adr       <= std_logic_vector(RAMAddrNext(17 downto 0) & '0');
''', ''),
    ('''               SS_SPURAM_rnw       <= '0';
               RAMAddrNext         <= RAMAddrNext + 1;
''', '''               SS_SPURAM_rnw       <= '0';
'''),
    ('''               if (SPURAM_done2X = '1') then
                  if (SPUwordcounter = 3) then
''', f'''               if (SPURAM_done2X = '1') then
                  RAMAddrNext <= RAMAddrNext + 1;   -- {MARK}
                  if (SPUwordcounter = 3) then
'''),
    ('      SS_SPURAM_Adr           : out std_logic_vector(18 downto 0) := (others => \'0\');\n',
     '      SS_SPURAM_Adr           : out std_logic_vector(18 downto 0);\n'),
]


def addr_text(text):
    if MARK in text: raise ValueError(f'{MARK}: already applied')
    if 'ddr3_ADDR_save' in text: raise ValueError(f'{MARK}: run prepare_reset_seq_lean.py first')
    for old, new in EDITS:
        if text.count(old) != 1: raise ValueError(f'{MARK}: anchor found {text.count(old)} times: ' + old.strip()[:70])
        text = text.replace(old, new)
    text = re.sub(r'(\nbegin[ \t]*\n)', r"\1\n   SS_SPURAM_Adr <= std_logic_vector(RAMAddrNext(17 downto 0) & '0');   -- " + MARK + '\n', text, count=1)
    if 'SS_Adr_2x' in text: raise ValueError(f'{MARK}: an SS_Adr_2x use is left')
    return text


def prepare_project(project):
    project = Path(project).resolve(); q = (project/'apf_top.qsf').read_text()
    hits = re.findall(r'"([^"]*/savestates\.vhd)"', q)
    if len(hits) != 1 or 'reset-seq-lean' not in hits[0]: raise ValueError(f'{MARK}: expected the reset-seq-lean savestates.vhd, found {hits}')
    path = Path(hits[0]); path.write_text(addr_text(path.read_text()))
    return path


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1])
    print(prepare_project(sys.argv[1]))
