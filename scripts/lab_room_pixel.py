# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""lab-room step for gpu_pixelpipeline.vhd: the pixel position and background delay in an MLAB.

Before: x (10), y (9), oldPixel (16) and oldPixel2 (16) ride through stage0..stage4 flip-flops
(stage5 too). Only stage5 reads them (stage6 position, the blend's background: oldPixel 14:0,
oldPixel2 8:0), and ditherAdd reads stage3_x / stage3_y bits 1:0. Every stage moves on the
same enable (reset low, ce high, pipeline_stall low) and none of these has a reset value
that is read (stage valid bits gate them).

After: on that enable the stage0 value (43 live bits: x, y, oldPixel 14:0, oldPixel2 8:0)
is written into an 8-entry MLAB ring at delayWr and delayWr advances; stage5 loads the entry
written five enables earlier (delayWr - 5, read asynchronously, never the entry being
written). So stage5 gets what stage4 held, as before. stage0..3 keep 2+2 bits of x / y for
ditherAdd. stage5 stays a register, so the blend path does not see the MLAB read.
Removed: 5 x 51 FF (synthesis already dropped the unread oldPixel bits, about 5 x 43 live);
added: one 8 x 43 MLAB ring (3 MLABs), a 3-bit pointer, 16 FF of dither bits.

Check: scripts/run_lab_room_gpu.py (whole-GPU lockstep).
"""
import re

TAG = 'lab-room-pixel'

DECL = re.compile(r'(?m)^ +signal stage[0-4]_(?:x|y|oldPixel|oldPixel2) +:[^\n]*\n')
STAGE0 = re.compile(r'(?m)^ +stage0_(?:x|y|oldPixel|oldPixel2) +<= [^\n]*\n')
SHIFT = re.compile(r'(?m)^ +stage([1-4])_(x|y|oldPixel|oldPixel2) +<= stage([0-3])_\2;[ \t]*\n')
STAGE5 = re.compile(r'(?m)^( +)stage5_(x|y|oldPixel|oldPixel2) +<= stage4_\2;[ \t]*\n')
FIELD = {'x': 'unsigned(delayOut(42 downto 33))', 'y': 'unsigned(delayOut(32 downto 24))',
         'oldPixel': "'0' & delayOut(23 downto 9)", 'oldPixel2': '"0000000" & delayOut(8 downto 0)'}

DECLS = '''   -- lab-room-pixel: x, y and the background words of stage0..stage4 in an MLAB ring
   type tDelayRam is array(0 to 7) of std_logic_vector(42 downto 0);
   signal delayRam            : tDelayRam := (others => (others => '0'));
   attribute ramstyle : string;
   attribute ramstyle of delayRam : signal is "MLAB, no_rw_check";
   signal delayWr             : unsigned(2 downto 0) := (others => '0');
   signal delayIn             : std_logic_vector(42 downto 0);
   signal delayOut            : std_logic_vector(42 downto 0);
   signal stage0_xd, stage1_xd, stage2_xd, stage3_xd : unsigned(1 downto 0) := (others => '0');
   signal stage0_yd, stage1_yd, stage2_yd, stage3_yd : unsigned(1 downto 0) := (others => '0');
'''

BODY = '''
   -- lab-room-pixel: the stage0..stage4 delay of x / y / background (written on the stage enable)
   delayIn <= std_logic_vector(stageS_x) & std_logic_vector(stageS_y) & stageS_oldPixel(14 downto 0) & stageS_oldPixel2(8 downto 0)
              when pipeline_stall_1 = '1' else
              std_logic_vector(pipeline_x) & std_logic_vector(pipeline_y) & vramLineData(14 downto 0) & vramLineData2(8 downto 0);
   delayOut <= delayRam(to_integer(delayWr - 5));
   process (clk2x)
   begin
      if rising_edge(clk2x) then
         if (reset = '0' and ce = '1' and pipeline_stall = '0') then
            delayRam(to_integer(delayWr)) <= delayIn;
            delayWr   <= delayWr + 1;
            stage0_xd <= unsigned(delayIn(34 downto 33));
            stage0_yd <= unsigned(delayIn(25 downto 24));
            stage1_xd <= stage0_xd; stage1_yd <= stage0_yd;
            stage2_xd <= stage1_xd; stage2_yd <= stage1_yd;
            stage3_xd <= stage2_xd; stage3_yd <= stage2_yd;
         end if;
      end if;
   end process;
'''

DITHER_OLD = 'DITHERMATRIX(to_integer(stage3_y(1 downto 0)), to_integer(stage3_x(1 downto 0)))'
DITHER_NEW = 'DITHERMATRIX(to_integer(stage3_yd), to_integer(stage3_xd))'

FAULTS = {
    # one enable too short: stage5 gets what stage3 held
    'depth-4': ('delayRam(to_integer(delayWr - 5))', 'delayRam(to_integer(delayWr - 4))'),
    # the skid branch writes the live input (a stall loses the held pixel's position)
    'skid-lost': ("              when pipeline_stall_1 = '1' else\n", "              when false else\n"),
    # the ring also advances while stalled
    'stall-moves': ("         if (reset = '0' and ce = '1' and pipeline_stall = '0') then\n            delayRam",
                    "         if (reset = '0' and ce = '1') then\n            delayRam"),
    # dither position one stage late
    'dither-late': (DITHER_NEW, 'DITHERMATRIX(to_integer(stage2_yd), to_integer(stage2_xd))'),
}


def once(text, old, new):
    n = text.count(old)
    if n != 1:
        raise ValueError(f'{TAG}: expected one match, found {n}: {old.strip()[:100]!r}')
    return text.replace(old, new)


def count_sub(pattern, repl, text, expected, what):
    text, n = pattern.subn(repl, text)
    if n != expected:
        raise ValueError(f'{TAG}: expected {expected} {what}, found {n}')
    return text


def transform(text):
    if TAG in text:
        raise ValueError(f'{TAG}: already applied')
    first = DECL.search(text)
    if not first:
        raise ValueError(f'{TAG}: stage declarations not found')
    text = text[:first.start()] + DECLS + text[first.start():]
    text = count_sub(DECL, '', text, 20, 'stage0..4 declarations')
    text = count_sub(STAGE0, '', text, 8, 'stage0 loads')
    text = count_sub(SHIFT, '', text, 16, 'stage1..4 shifts')
    text = count_sub(STAGE5, lambda m: f'{m[1]}stage5_{m[2]} <= {FIELD[m[2]]};   -- lab-room-pixel\n', text, 4, 'stage5 loads')
    text = once(text, DITHER_OLD, DITHER_NEW)
    if text.count('\nbegin \n') != 1:
        raise ValueError(f'{TAG}: architecture begin not found')
    return text.replace('\nbegin \n', '\nbegin \n' + BODY, 1)


def fault_text(text, name):
    return once(text, *FAULTS[name])
