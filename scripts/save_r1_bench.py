#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Bench side of the save routine R1 (scripts/prepare_save_r1.py) for
run_save_project_cpu.py --routine r1. Everything here is a text transform on
the generated CPU round-trip bench (sim/reference/tb_staged_save_cpu.vhd after
run_staged_save_cpu.bench_text and the paired-format cpu_bench) or on the
generated guest program; without --routine r1 none of it runs and the bench,
routine and guest files are the ones the runner made before.

What R1 touches and what answers it in this bench:
  memory control 0x1F801000-0x1F801020, RAM size 0x1F801060
        real RTL: the project's memctrl (memctrl_lean), in the bench before R1
  cache control 0xFFFE0130
        real RTL: the project's CPU
  JOY_MODE / JOY_CTRL / JOY_BAUD (0x1F801048, 4A, 4E)
        real RTL: the project's joypad (joypad_card1.vhd with its joypad_pad
        and joypad_mem), no pad and no card connected, in place of the
        constant-0 pad bus
  MDEC 0x1F801820 / 0x1F801824
        MODEL (mdec.vhd needs the SystemVerilog IDCT MAC, which GHDL cannot
        read): status, control, command words and the input FIFO after
        build/ss/cm/mdec-beetle/mdec.vhd 385, 400-406, 478-484, 486-609,
        1416-1426, 1437-1448. The output FIFO is always empty and a decode
        accepts only end-of-block codes (anything else stops the run).
  EXP1 DISC (0x180B) and DISC_SET (0x1823)
        MODEL of psx_save_exp1 + core_top + psx_disc_menu + psx_disc_swap as
        scripts/prepare_save_disc_set.py and rtl/pocket/psx_disc_menu.sv
        describe them (see DISC_MODEL below).

Guest witness: the bootstrap plants non-boot values in the R1 registers and an
epilogue of the game reads them back into RAM 0x2400-0x2434, inside the dumped
RAM. With R1_SCRUB the bench resets memctrl, the joypad and the MDEC model at
the load request, so a load that does not write them back changes the dump.
Cache control cannot be reset from the bench (it is inside the CPU).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DISC_POLLS_BENCH = 0x00010000   # the smallest count the routine's "lui $t2, DISC_POLLS >> 16" can hold


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'R1 bench anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


# ---------------------------------------------------------------- routine

# Planted routine faults (never a fitted input). Each is an exact-anchor replacement on the R1 text.
ROUTINE_MUTATIONS = {
    # the put-back after a failed remount is skipped
    'no-put-back': ('''\
        jal     disc_mount              # not mounted (wrong disc or no answer in time): the menu's
        move    $a0, $s5                # disc again, so DISC names the disc in the drive, and no load
''', '''\
        nop                             # MUTATION no-put-back
        nop
'''),
    # the remount's result is ignored: the load goes on
    'ignore-result': ('''\
        move    $a0, $s6
        beqz    $v0, 72f
''', '''\
        move    $a0, $s6
        b       72f                     # MUTATION ignore-result
'''),
    # the poll looks at settled only, not at DISC
    'settled-only': ('''\
        and     $t0, $t0, $t1           # ... and settled
''', '''\
        move    $t0, $t1                # MUTATION settled-only
'''),
    # the last DISC_SET write leaves the silent bit on
    'silent-left-on': ('''\
79:     jr      $ra
        sb      $a0, DISC_SET($k0)      # silent off (the same disc: the menu setting stays)
''', '''\
79:     ori     $t0, $a0, 8             # MUTATION silent-left-on
        jr      $ra
        sb      $t0, DISC_SET($k0)
'''),
    # memory control is not written back on a load
    'no-memctrl-restore': ('''\
        sw      $t0, 0($t7)             # (memctrl_lean takes whole words at aligned addresses only)
''', '''\
        nop                             # MUTATION no-memctrl-restore
'''),
    # JOY_MODE is written as a word (reaches JOY_MODE only; here: the CTRL write is dropped)
    'no-joy-ctrl-restore': ('''\
        sh      $t2, 0x104E($t9)
        sh      $t1, 0x104A($t9)
''', '''\
        sh      $t2, 0x104E($t9)
        nop                             # MUTATION no-joy-ctrl-restore
'''),
    # the MDEC enables are not written back
    'no-mdec-enable-restore': ('''\
        or      $t0, $t0, $t1
        sw      $t0, 0x1824($t9)
        jal     load_start              # page bitmap into the scratchpad, drive paused and set up
''', '''\
        or      $t0, $t0, $t1
        nop                             # MUTATION no-mdec-enable-restore
        jal     load_start              # page bitmap into the scratchpad, drive paused and set up
'''),
}


def routine_text(pxs6_text, disc_polls=None, mutation=None):
    """The R1 routine for the bench. disc_polls: BENCH-ONLY override of the DISC_POLLS equate
    (the timeout scenario); nothing else of the text changes."""
    from prepare_save_r1 import firmware_text
    text = firmware_text(pxs6_text)
    if disc_polls is not None:
        if disc_polls < 0x10000 or disc_polls & 0xffff:
            raise ValueError('DISC_POLLS is loaded by lui: a multiple of 0x10000, at least 0x10000')
        text = once(text, '        .equ    DISC_POLLS, 0x00800000  #',
                    f'        .equ    DISC_POLLS, 0x{disc_polls:08X}  # BENCH-ONLY OVERRIDE (save_r1_bench.py; the routine has 0x00800000) #')
    if mutation:
        text = once(text, *ROUTINE_MUTATIONS[mutation])
    return text


def magic_r1(pxs6_text):
    found = re.findall(r'^        \.equ    MAGIC, (0x[0-9A-Fa-f]+)', pxs6_text, flags=re.M)
    if len(found) != 1:
        raise ValueError('MAGIC equate not found once')
    return int(found[0], 16) + 0x20


# ---------------------------------------------------------------- guest

# (offset from 0x1F800000, planted word): not the reset values of memctrl_lean.vhd 70-72.
# EXP1 base and the EXP1 and BIOS delays keep their reset values (the routine and the
# bootstrap run through them); SPU keeps bit 12 (16-bit bus).
PLANT_MEM = [(0x1004, 0x1F802040), (0x100C, 0x00003123), (0x1014, 0x220931E2), (0x1018, 0x00020844),
             (0x101C, 0x00070776), (0x1020, 0x00031236), (0x1060, 0x00000B8C)]
PLANT_CACHE = 0x0001E988
PLANT_MDEC_CONTROL = 0x60000000         # both request enables
PLANT_MDEC_COMMAND = 0x3C000001         # decode, depth 3, signed, 1 parameter word
PLANT_JOY = [(0x1048, 0x010D), (0x104E, 0x0088), (0x104A, 0x1405)]   # MODE, BAUD, CTRL (no select)
READBACK = 0x2400                       # RAM words the epilogue fills (inside the dumped 16 KiB)
EPILOGUE = 0x0A00
READ_NAMES = ['mc0', 'mc1', 'mc2', 'mc3', 'mc4', 'mc5', 'mc6', 'mc7', 'mc8', 'ramsize', 'cache', 'mdec', 'joy', 'baud']
# What a correct run reads back (memctrl_lean masks; joypad_card1 244-275, 288-291; the MDEC model)
READ_EXPECT = [0x1F000000, 0x1F802040, 0x0013243F, 0x00003123, 0x0013243F, 0x220931E2, 0x00020844, 0x00070776,
               0x00031236, 0x00000B8C, PLANT_CACHE, 0x9704FFFF, 0x1405010D, 0x00000088]


def guest(directory):
    """Bootstrap prefix that plants the R1 registers; game epilogue that reads them back."""
    directory = Path(directory)
    T0, T1, T2, V0, T8, T9 = 8, 9, 10, 2, 24, 25

    def li(reg, value):
        out = []
        if value >> 16:
            out.append(0x3c000000 | reg << 16 | value >> 16)
            if value & 0xffff:
                out.append(0x34000000 | reg << 21 | reg << 16 | value & 0xffff)
        else:
            out.append(0x34000000 | reg << 16 | value & 0xffff)
        return out

    def store(op, base, reg, offset):
        return op << 26 | base << 21 | reg << 16 | offset & 0xffff

    prefix = [0x3c000000 | T1 << 16 | 0x1f80]
    for offset, value in PLANT_MEM:
        prefix += li(T0, value) + [store(0x2b, T1, T0, offset)]
    prefix += [0x3c000000 | T0 << 16 | 0xfffe] + li(T2, PLANT_CACHE) + [store(0x2b, T0, T2, 0x130)]
    prefix += li(T0, PLANT_MDEC_CONTROL) + [store(0x2b, T1, T0, 0x1824)]
    prefix += li(T0, PLANT_MDEC_COMMAND) + [store(0x2b, T1, T0, 0x1820)]
    prefix += li(T0, 0xFE00FE00) + [store(0x2b, T1, T0, 0x1820)]
    for offset, value in PLANT_JOY:
        prefix += li(T0, value) + [store(0x29, T1, T0, offset)]
    rom = directory / 'rom.hex'
    old = rom.read_text()
    if len(prefix) + len(old.split()) > 128:
        raise ValueError('bootstrap too long for the bench ROM')
    rom.write_text(''.join(f'{w:08x}\n' for w in prefix) + old)

    ram = directory / 'ram.hex'
    words = {int(a, 16): int(w, 16) for a, w in (line.split() for line in ram.read_text().splitlines())}
    marker = [a for a in words if words[a] == 0x3c19600d and words.get(a + 4) == 0x3739f00d]
    if len(marker) != 1:
        raise ValueError('finish-marker load not found once in the game')
    at = marker[0]
    code = [0x3c000000 | T9 << 16 | 0x1f80, 0x3c000000 | T8 << 16 | 0x8000]
    k = 0

    def keep(read):
        nonlocal k, code
        code += read + [0, store(0x2b, T8, V0, READBACK + 4 * k)]
        k += 1

    for i in range(9):
        keep([store(0x23, T9, V0, 0x1000 + 4 * i)])
    keep([store(0x23, T9, V0, 0x1060)])
    keep([0x3c000000 | V0 << 16 | 0xfffe, store(0x23, V0, V0, 0x130)])
    keep([store(0x23, T9, V0, 0x1824)])
    keep([store(0x23, T9, V0, 0x1048)])
    keep([store(0x25, T9, V0, 0x104E)])
    assert k == len(READ_NAMES)
    code += [0x3c19600d, 0x3739f00d, 0x08000000 | (at + 8) // 4, 0]
    span = range(EPILOGUE, EPILOGUE + 4 * len(code), 4)
    if any(a in words for a in span) or any(READBACK <= a < READBACK + 4 * k for a in words):
        raise ValueError('R1 epilogue or readback area is in use')
    words[at], words[at + 4] = 0x08000000 | EPILOGUE // 4, 0
    for a, w in zip(span, code):
        words[a] = w
    ram.write_text(''.join(f'{a:08x} {w:08x}\n' for a, w in sorted(words.items())))


def readback(image):
    """The epilogue's words from a dump.hex text: {name: value}."""
    rows = image.split()
    return {name: int(rows[READBACK // 4 + i], 16) for i, name in enumerate(READ_NAMES)}


# ---------------------------------------------------------------- bench

GENERICS = '''\
      -- R1 bench (scripts/save_r1_bench.py)
      SAVE_DISC  : natural  := 0;     -- the menu's disc (0x180B) until the load request
      DISC_SYNC  : natural  := 8;     -- cycles a DISC_SET write takes into the menu (DISC and settled still as before for half of it)
      DISC_START : natural  := 40;    -- cycles from the menu taking a new disc to the swap's start (DISC changes there)
      DISC_DELAY : natural  := 600;   -- cycles a swap takes (settled 0 throughout)
      DISC_WAKE  : natural  := 0;     -- > 0: at the load request the first mount has not arrived; it does this many cycles later
      DISC_WRONG : boolean  := false; -- cue reader: a disc other than the load-time one cannot be opened (settled with the wrong-disc bit)
      DISC_NEVER : boolean  := false; -- no cue reader: a disc other than the load-time one is never taken (settled stays 0)
      DISC_EARLY : boolean  := false; -- PLANTED MODEL FAULT: settled reads 1 while a swap runs
      LOAD_PLANT : integer  := -1;    -- >= 0: no save needed; the load finds the R1 magic word + this disc in page 0 (nothing else)
      R1_SCRUB   : boolean  := false; -- at the load request memctrl, the joypad and the MDEC model are reset
'''

SIGNALS = '''\
   -- R1 bench (scripts/save_r1_bench.py)
   signal r1_scrub_s, r1_dev_reset : std_logic := '0';
   signal r1_joy_q : std_logic_vector(31 downto 0);
   signal r1_pad_window : std_logic := '0';
   signal r1_nopad : joypad_t := (Analog1X => (others => '0'), Analog1Y => (others => '0'), Analog2X => (others => '0'),
                                  Analog2Y => (others => '0'), others => '0');
   signal disc_set_writes, disc_set_last, disc_now, disc_status_now, load_guest_writes, disc_looks : natural := 0;
   signal guest_write_any : std_logic;
'''

# DISC_MODEL. Hardware (prepare_save_disc_set.py; psx_disc_menu.sv):
#   DISC_SET write: disc_setting takes [2:0] after the clk_74a synchroniser (a write of the value it
#   has changes nothing); silent = [3]. psx_disc_menu: wanted follows the setting when the disc is
#   takeable (always with the cue reader; DISC_NEVER models a disc the game file does not have in a
#   build without it); selection_stable is 0 while the pipeline or wanted differ from the setting;
#   swap_request = wanted /= selected; selected (= DISC) takes wanted when the swap starts.
#   settled = first mount arrived and no swap running and no swap requested and selection stable.
#   wrong = the cue reader could not open the disc the menu asks for (it keeps the disc in the drive);
#   DISC still names the disc the menu selected.
DISC_VARS = '''\
      -- R1 disc model (save_r1_bench.py DISC_MODEL)
      variable d_setting, d_wanted, d_selected, d_drive : natural range 0 to 7 := SAVE_DISC;
      variable d_sync, d_start, d_swap, d_wake : natural := 0;
      variable d_unstable, d_wrong, d_swapping, d_settled : boolean := false;
      variable d_mounted : boolean := true;
      variable d_status : std_logic_vector(1 downto 0);
      variable d_writes, d_guest, d_looks : natural := 0;
      variable d_read_was : std_logic := '0';
'''

DISC_STEP = '''\
         -- R1 disc model: one step a CPU cycle
         if at_cpu_edge then
            if r1_scrub_s = '1' then r1_scrub_s <= '0'; end if;
            if d_wake > 0 then
               d_wake := d_wake - 1;
               if d_wake = 0 then d_mounted := true; end if;
            end if;
            if d_sync > 0 then
               d_sync := d_sync - 1;
               if d_sync <= DISC_SYNC / 2 then d_unstable := true; end if;
               if d_sync = 0 then
                  if DISC_NEVER and d_setting /= d_drive then null;   -- not takeable: wanted stays, never stable
                  else d_wanted := d_setting; d_unstable := false; end if;
               end if;
            end if;
            if d_swapping then
               d_swap := d_swap - 1;
               if d_swap = 0 then
                  d_swapping := false;
                  if DISC_WRONG and d_selected /= d_drive then d_wrong := true;
                  else d_wrong := false; d_drive := d_selected; end if;
               end if;
            elsif d_wanted /= d_selected and d_mounted then
               if d_start = 0 then d_start := DISC_START + 1; end if;
               d_start := d_start - 1;
               if d_start = 0 then d_selected := d_wanted; d_swapping := true; d_swap := DISC_DELAY + 1; end if;
            end if;
         end if;
         d_settled := d_mounted and not d_swapping and d_wanted = d_selected and not d_unstable;
         d_status := "00";
         if d_settled or (DISC_EARLY and d_swapping) then d_status(0) := '1'; end if;
         if d_wrong then d_status(1) := '1'; end if;
         disc_now <= d_selected; disc_status_now <= to_integer(unsigned(d_status));
         -- reads of 0x1823 (one a poll look; two more around a remount): rising read strobes
         if at_cpu_edge then
            if mux_bus_exp1_read = '1' and d_read_was = '0' and a = 16#1823# then d_looks := d_looks + 1; disc_looks <= d_looks; end if;
            d_read_was := mux_bus_exp1_read;
         end if;
         -- guest state written during a load (ACK to FINISH): every device bus but EXP1 and the routine's
         -- own EXP1 delay word (0x1F801008), and main RAM. The first one needs the state's disc mounted.
         if at_cpu_edge and ce = '1' and want_load = '1' and in_handler = '1' and
            (guest_write_any = '1' or (mux_ram_ena = '1' and mux_ram_rnw = '0' and mux_ram_Adr(24 downto 23) = "00" and
                                       not (save_mode_s = '1' and mux_ram_Adr(22 downto 21) /= "00"))) then
            if d_guest < 8 then
               report "R1 load: guest write " & integer'image(d_guest) & " at cycle " & integer'image(cycle_count) &
                  " ram_ena=" & std_logic'image(mux_ram_ena) & " ram_adr=" & to_hstring(mux_ram_Adr) severity note;
            end if;
            assert d_guest > 0 or (d_settled and not d_wrong and d_selected = to_integer(unsigned(buf(0)(2 downto 0))))
               report "R1 load went on without the state's disc mounted and settled (DISC " & integer'image(d_selected) &
                  ", status " & integer'image(to_integer(unsigned(d_status))) & ")" severity failure;
            d_guest := d_guest + 1; load_guest_writes <= d_guest;
         end if;
'''

DISC_WRITE = '''\
            elsif a = 16#1823# then
               -- R1 DISC_SET: [2:0] into the menu setting, [3] silent
               d_writes := d_writes + 1; disc_set_writes <= d_writes;
               disc_set_last <= to_integer(unsigned(mux_bus_exp1_dataWrite));
               report "R1 DISC_SET write " & to_hstring(mux_bus_exp1_dataWrite) & " at cycle " & integer'image(cycle_count) &
                  " (DISC " & integer'image(d_selected) & ", status " & integer'image(to_integer(unsigned(d_status))) & ")" severity note;
               if to_integer(unsigned(mux_bus_exp1_dataWrite(2 downto 0))) /= d_setting then
                  d_setting := to_integer(unsigned(mux_bus_exp1_dataWrite(2 downto 0)));
                  d_sync := DISC_SYNC + 1;
               end if;
'''

DISC_LOAD = '''\
               -- R1: the menu's disc at the load, a wake's first mount, a planted magic word, the device scrub
               d_setting := LOAD_DISC; d_wanted := LOAD_DISC; d_selected := LOAD_DISC; d_drive := LOAD_DISC;
               d_wrong := false; d_unstable := false; d_swapping := false; d_sync := 0; d_start := 0;
               if DISC_WAKE > 0 then d_mounted := false; d_wake := DISC_WAKE; end if;
               if LOAD_PLANT >= 0 then buf(0) := std_logic_vector(to_unsigned(MAGIC_R1_VALUE + LOAD_PLANT, 32)); end if;
               if R1_SCRUB then r1_scrub_s <= '1'; end if;
'''

MDEC_MODEL = '''\
   -- ---------------- R1: MDEC model (save_r1_bench.py; after mdec-beetle/mdec.vhd) ----------------
   -- Status, control, command words and the input FIFO. The output FIFO is always empty.
   r1_dev_reset <= reset or r1_scrub_s;
   mdec_model : process (clk1x)
      type fifo_t is array(0 to 511) of std_logic_vector(31 downto 0);
      variable fifo : fifo_t;
      variable n, rd, count : natural := 0;
      variable control : std_logic_vector(2 downto 0) := "000";
      variable fmt : std_logic_vector(3 downto 0) := "0000";         -- status 26:23
      variable state : natural range 0 to 3 := 0;                    -- idle, block, quant table, scale table
      variable words : unsigned(15 downto 0) := (others => '0');
      variable second, colour, clear, nearfull, pop : boolean := false;
      variable d, stat : std_logic_vector(31 downto 0);
      variable rl : std_logic_vector(15 downto 0);
   begin
      if rising_edge(clk1x) then
         if r1_dev_reset = '1' then
            n := 0; rd := 0; control := "000"; fmt := "0000"; state := 0; words := (others => '0'); second := false; clear := false;
         elsif ce = '1' then
            nearfull := n >= 512 - 223;
            stat := (others => '0');
            stat(15 downto 0) := std_logic_vector(words - 1);
            stat(18 downto 16) := "100";
            stat(26 downto 23) := fmt;
            if control(1) = '1' and not nearfull then stat(28) := '1'; end if;
            if state /= 0 then stat(29) := '1'; end if;
            if nearfull then stat(30) := '1'; end if;
            stat(31) := '1';
            bus_mdec_dataRead <= (others => '0');
            if mux_bus_mdec_read = '1' then
               if mux_bus_mdec_addr(3 downto 2) = "01" then bus_mdec_dataRead <= stat;
               else bus_mdec_dataRead <= x"FFFFFFFF"; end if;
            end if;
            pop := false;
            if clear then
               n := 0; rd := 0; state := 0; words := (others => '0'); second := false; fmt := "0000"; clear := false;
            elsif n > 0 then
               d := fifo(rd);
               case state is
                  when 0 =>
                     fmt := d(28 downto 25);
                     case d(31 downto 29) is
                        when "001" => state := 1; words := unsigned(d(15 downto 0)); pop := true;
                        when "010" => state := 2; colour := d(0) = '1'; count := 0; pop := true;
                           if colour then words := to_unsigned(32, 16); else words := to_unsigned(16, 16); end if;
                        when "011" => state := 3; count := 0; words := to_unsigned(32, 16); pop := true;
                        when others => if words > 0 then words := words - 1; pop := true; end if;
                     end case;
                  when 1 =>
                     if second then rl := d(31 downto 16); else rl := d(15 downto 0); end if;
                     assert rl = x"FE00" report "MDEC model: decode data " & to_hstring(rl) & " is not modelled" severity failure;
                     if not second then second := true;
                     else
                        second := false; pop := true;
                        if words = 1 then state := 0; end if;
                        words := words - 1;
                     end if;
                  when 2 =>
                     pop := true; count := count + 1;
                     if (colour and count = 32) or (not colour and count = 16) then state := 0; words := words - 16; end if;
                  when 3 =>
                     pop := true; count := count + 1;
                     if count = 32 then state := 0; words := words - 16; end if;
               end case;
               if pop then rd := (rd + 1) mod 512; n := n - 1; end if;
            end if;
            if mux_bus_mdec_write = '1' and mux_bus_mdec_addr = x"4" then
               control := mux_bus_mdec_dataWrite(31 downto 29);
               if mux_bus_mdec_dataWrite(31) = '1' then clear := true; end if;
            end if;
            if mux_bus_mdec_write = '1' and mux_bus_mdec_addr = x"0" then
               assert n < 512 report "MDEC model: input FIFO overflow" severity failure;
               fifo((rd + n) mod 512) := mux_bus_mdec_dataWrite; n := n + 1;
            end if;
         end if;
      end if;
   end process;

   -- ---------------- R1: the project's joypad (real RTL), nothing connected ----------------
   -- The PAD_SELECT window of the bench before R1 still answers with its constant.
   pad_model : process(clk1x)
   begin
      if rising_edge(clk1x) then
         r1_pad_window <= '0';
         if PAD_SELECT > 0 and in_handler = '1' and cycle_count >= SAVE_AT and cycle_count < SAVE_AT + PAD_SELECT then
            r1_pad_window <= '1';
         end if;
      end if;
   end process;
   bus_pad_dataRead <= x"00020002" when r1_pad_window = '1' else r1_joy_q;
'''

PAD_OLD = '''\
   pad_model : process(clk1x)
   begin
      if rising_edge(clk1x) then
         bus_pad_dataRead <= (others => '0');
         if PAD_SELECT > 0 and in_handler = '1' and cycle_count >= SAVE_AT and cycle_count < SAVE_AT + PAD_SELECT then
            bus_pad_dataRead <= x"00020002";
         end if;
      end if;
   end process;
'''


def joypad_instance(source, ports, zero):
    rows = []
    for name, direction, kind in ports(source, 'joypad'):
        if name in ('clk1x', 'clk2x', 'clk2xIndex', 'ce'):
            value = name
        elif name == 'reset':
            value = 'r1_dev_reset'
        elif name == 'bus_dataRead':
            value = 'r1_joy_q'
        elif name.startswith('bus_'):
            value = 'mux_bus_pad_' + name[4:]
        elif direction == 'out':
            value = 'open'
        elif kind.lower() == 'joypad_t':
            value = 'r1_nopad'
        else:
            value = zero(kind)
        rows.append(f'{name} => {value}')
    return '   ijoypad : entity work.joypad port map (\n      ' + ',\n      '.join(rows) + '\n   );\n'


def bench_text(text, joypad_source, ports, zero, magic):
    """The generated round-trip bench -> the R1 bench."""
    text = once(text, 'use work.pexport.all;\n', 'use work.pexport.all;\nuse work.pJoypad.all;   -- R1 bench\n')
    # (the load-first bench, 6459909e, has generics after ZERO_ROW: "4;" instead of "4 ")
    zero_row = '      ZERO_ROW   : natural  := 4;' if '      ZERO_ROW   : natural  := 4;' in text else '      ZERO_ROW   : natural  := 4 '
    text = once(text, zero_row, GENERICS + zero_row)
    writes = sorted(set(re.findall(r'signal (mux_bus_\w+_write) : std_logic;', text)) - {'mux_bus_exp1_write'})
    if not {'mux_bus_memc_write', 'mux_bus_pad_write', 'mux_bus_mdec_write', 'mux_bus_spu_write', 'mux_bus_gpu_write',
            'mux_bus_cd_write', 'mux_bus_memc2_write'} <= set(writes):
        raise ValueError('device write strobes not found in the bench')
    any_write = ' or '.join(w if w != 'mux_bus_memc_write' else "(mux_bus_memc_write and not (mux_bus_memc_addr ?= to_unsigned(8, 6)))"
                            for w in writes)
    text = once(text, 'begin\n\n   clk1x <= not clk1x after 15 ns;\n',
                SIGNALS + f'   constant MAGIC_R1_VALUE : natural := 16#{magic:08X}#;\n'
                'begin\n\n   clk1x <= not clk1x after 15 ns;\n'
                f'   guest_write_any <= {any_write};\n')
    # the pad bus: the real joypad; the MDEC bus: the model
    text = once(text, "         bus_mdec_dataRead <= (others => '0');\n", '')
    text = once(text, PAD_OLD, MDEC_MODEL)
    # memctrl takes the scrub reset
    start = text.index('   imemctrl : entity work.memctrl port map (')
    end = text.index(');', start)
    text = text[:start] + once(text[start:end], 'reset => reset', 'reset => r1_dev_reset') + text[end:]
    text = once(text, '   igte : entity work.gte port map (',
                joypad_instance(joypad_source, ports, zero) + '   igte : entity work.gte port map (')
    # EXP1: DISC and DISC_SET
    text = once(text, '''\
         elsif a = 16#180B# then
            if want_load = '1' then exp1_q <= std_logic_vector(to_unsigned(LOAD_DISC, 8)); else exp1_q <= x"00"; end if;
''', '''\
         elsif a = 16#180B# then
            exp1_q <= std_logic_vector(to_unsigned(d_selected, 8));
         elsif a = 16#1823# then
            exp1_q <= "000000" & d_status;
''')
    text = once(text, '      variable load_armed : boolean := LOAD_DELAY > 0;\n',
                '      variable load_armed : boolean := LOAD_DELAY > 0;\n' + DISC_VARS)
    text = once(text, '         if retry_left > 0 and at_cpu_edge then\n', DISC_STEP + '         if retry_left > 0 and at_cpu_edge then\n')
    text = once(text, '            elsif a = 16#1820# then\n', DISC_WRITE + '            elsif a = 16#1820# then\n')
    text = once(text, "               want_load <= '1'; trap <= '1';\n", DISC_LOAD + "               want_load <= '1'; trap <= '1';\n")
    text = once(text, '" spu_rekeys=" & integer\'image(spu_rekeys);',
                '" spu_rekeys=" & integer\'image(spu_rekeys) & " finish=" & integer\'image(finish_code) &\n'
                '                     " disc=" & integer\'image(disc_now) & " disc_status=" & integer\'image(disc_status_now) &\n'
                '                     " disc_set_writes=" & integer\'image(disc_set_writes) & " disc_set_last=" & integer\'image(disc_set_last) &\n'
                '                     " load_guest_writes=" & integer\'image(load_guest_writes) & " disc_status_reads=" & integer\'image(disc_looks);')
    return text


# ---------------------------------------------------------------- remount scenarios (item 3)

def remount_scenarios(m, directory, reference, ref_image, names):
    """Short remount cases. Returns (rows, failures). reference/ref_image: the run without save or load.

    Success cases: a warm save, then the load. Failure cases: no save; the load finds only the
    R1 magic word (LOAD_PLANT), so a load that went on would write garbage and stop the game.
    """
    cycles = int(reference['cycles'])
    at = cycles // 3
    cases = {
        # menu on disc 2 at the load, the state is disc 1's: silent swap, wait, load. DISC_SYNC 64: the
        # routine's first looks fall in the time DISC and settled still show the state before the write
        'other disc': (dict(SAVE_AT=at, STAGE2_WARM='true', LOAD_DELAY=cycles // 3, LOAD_DISC=1, DISC_SYNC=64, R1_SCRUB='true'),
                       dict(saves='1', loads='1', loads_failed='0', finish='1', disc='0', disc_status='1',
                            disc_set_writes='2', disc_set_last='0'), True),
        # a wake: same disc, the first mount arrives later
        'wake': (dict(SAVE_AT=at, STAGE2_WARM='true', LOAD_DELAY=cycles // 3, DISC_WAKE=5000, R1_SCRUB='true'),
                 dict(saves='1', loads='1', loads_failed='0', finish='1', disc='0', disc_status='1',
                      disc_set_writes='2', disc_set_last='0'), True),
        # cue reader cannot open the state's disc: error, menu back on its disc
        'wrong disc': (dict(LOAD_AT=at, STAGE2_WARM='true', LOAD_PLANT=0, LOAD_DISC=1, DISC_WRONG='true'),
                       dict(saves='0', loads='0', loads_failed='1', finish='2', disc='1', disc_status='1',
                            disc_set_writes='4', disc_set_last='1', load_guest_writes='0'), False),
        # the state's disc is never taken: timeout (bench DISC_POLLS), error, menu back on its disc
        'timeout': (dict(LOAD_AT=at, STAGE2_WARM='true', LOAD_PLANT=0, LOAD_DISC=1, DISC_NEVER='true'),
                    dict(saves='0', loads='0', loads_failed='1', finish='2', disc='1', disc_status='1',
                         disc_set_writes='4', disc_set_last='1', load_guest_writes='0'), False),
    }
    rows, failures = [], []
    for name in names:
        generics, expect, loads = cases[name]
        generics = dict(generics, MAX_CYCLES=6000000)
        ok, values, image, why = m.simulate(directory, generics, 'r1-' + name.replace(' ', '-'))
        problem = None
        if not ok:
            problem = why
        elif image != ref_image:
            problem = 'final dump differs from the run without save or load'
        elif any(values.get(k) != v for k, v in expect.items()):
            problem = f'counters differ from {expect}'
        elif loads != (int(values['spu_writes']) > int(reference['spu_writes'])):
            problem = f'guest SPU writes {values["spu_writes"]} vs reference {reference["spu_writes"]}'
        rows.append(dict(case=name, generics=generics, statistics=values, problem=problem))
        print(f'  r1 {name}: {values}' + (f'  <- {problem}' if problem else ''), flush=True)
        if problem:
            failures.append(f'r1 {name}: {problem}')
    return rows, failures
