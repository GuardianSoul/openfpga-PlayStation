#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bench side of the MDEC table tap routine step (scripts/prepare_save_mdec_tap.py) for
run_save_project_cpu.py --routine r1 --routine-step ... --routine-step mdec-tap.

Text transforms on the generated CPU round-trip bench (tb_staged_save_cpu.vhd after the R1 and
stage2-mark transforms); without the step none of it runs.

What answers the step in this bench (the CPU is the project's RTL; these are MODELS, like the
bench's device and R1's MDEC):
  MDEC model (save_r1_bench.MDEC_MODEL)  gets the two tables: commands 2 and 3 store their
        words (quantisation bytes in upload order, luminance then chrominance; scale halfwords in
        upload order, low half first). A reset keeps them, as mdec.vhd's RAMs do.
  save device model   SPU_HOLD (0x180C) bit 1 = table tap; 0x1824 reads quantisation byte
        SS_ADR[6:0], 0x1825 byte SS_ADR[0] of scale word SS_ADR[6:1]; both read 0 with the tap off.
Generics:
  MDEC_TABLES  true (default): the MDEC starts with the tables "the game sent earlier" (a
               pattern, not zero: byte i = 37 i + 11, halfword j = 2053 j + 977). false: zero
               tables, as in a core no guest has written them to (a load-first run).
  MDEC_WIPE    true (default): at a load request with R1_SCRUB the tables are zeroed with the
               other device state (a core that lost them), so only the routine's commands 2 and
               3 can bring them back.
  MDEC_EXPECT  true (default): at the finish both tables must equal the pattern.
  CD_SLACK     0 (default): the bench's rule "the drive reads on at most one sector between the
               save's quiet point and CD_FREEZE" as it was. The step's table read (mdec_quiet's
               1,024 looks and 129 tap passes) sits between cd_quiet and CD_FREEZE, and the
               bench's drive delivers a sector every 4,000 clk1x (a real 2x drive: 225,792), so
               with the step the rule fails at 0 (warm round trip: read on from 1006 to 1010).
               The runner takes CD_SLACK from the environment variable MDEC_TAP_CD_SLACK for
               runs with a save; every such run prints it.
Rules (a miss stops the run): no copy command and no MDEC command word while the tap is on; the
tap is off at the finish.
Runner side (simulate wrapper): a run that plants the save buffer (PLANT_BUF, the load-first
scenarios) gets MDEC_TABLES=false; after a run that dumped the save buffer (DUMP_BUF, the plain
'save') the state must carry the tables: page 0 word 59 bit 19 set, words 128-191 = the pattern.
The real MDEC is not in this bench (GHDL cannot read its SystemVerilog IDCT MAC here); the decode
after the routine's writes is run_save_mdec_tap.py's module bench (cold_start_equals_model).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_save_mdec_tap as step

# Planted faults for this bench (never a fitted input), besides the step's own
BENCH_FAULTS = {
    # the load never sends the tables again
    'load-no-resend': ('        bgez    $t0, 63f\n        lui     $t0, 0x4000\n',
                       '        b       63f                     # FAULT load-no-resend\n        lui     $t0, 0x4000\n'),
    # the tap stays on after the tables are read: not switched off, and the SPU hold write keeps bit 1
    'save-tap-left-on': ('        sb      $zero, SPU_HOLD($k0)    # tap off\n        li      $t0, 1\n        sb      $t0, MT_FLAG($k0)\n62:\n'
                         '        jal     cd_quiet\n        nop\n        bnez    $v0, retry\n        nop\n1:\n'
                         '        # the SPU held between samples (no voice processing, DMA FIFO empty)\n        li      $t0, 1\n',
                         '        nop                             # FAULT save-tap-left-on\n        li      $t0, 1\n        sb      $t0, MT_FLAG($k0)\n62:\n'
                         '        jal     cd_quiet\n        nop\n        bnez    $v0, retry\n        nop\n1:\n'
                         '        # the SPU held between samples (no voice processing, DMA FIFO empty)\n        li      $t0, 3\n'),
}
FAULTS = {**step.FAULTS, **BENCH_FAULTS}
firmware_text = step.firmware_text


def fault_text(text, name):
    old, new = FAULTS[name]
    return step.once(text, old, new)


def quant_byte(i):
    return (37 * i + 11) % 256


def scale_half(j):
    return (2053 * j + 977) % 65536


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'mdec-tap bench anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


GENERIC_OLD = '      STAGE2_WARM : boolean := false; -- stage 2 already unpacked at STAGE2 (stage2.hex), as after an earlier trap\n'
GENERICS = '''\
      MDEC_TABLES : boolean := true;  -- mdec-tap: the MDEC starts with the game's tables (false: zero)
      MDEC_WIPE   : boolean := true;  -- mdec-tap: R1_SCRUB at a load request zeroes the tables too
      MDEC_EXPECT : boolean := true;  -- mdec-tap: at the finish the tables are the game's
      CD_SLACK    : natural := 0;     -- mdec-tap: sectors more than 1 the drive may read on between the quiet point and CD_FREEZE
'''
SIGNAL_OLD = "   signal r1_scrub_s, r1_dev_reset : std_logic := '0';\n"
SIGNALS = '''\
   -- mdec-tap (save_mdec_tap_bench.py): the MDEC's tables and the save device's tap bit
   type mt_bytes is array(0 to 127) of std_logic_vector(7 downto 0);
   type mt_halves is array(0 to 63) of std_logic_vector(15 downto 0);
   function mt_quant_game(present : boolean) return mt_bytes is
      variable t : mt_bytes := (others => x"00");
   begin
      if present then for i in t'range loop t(i) := std_logic_vector(to_unsigned((37 * i + 11) mod 256, 8)); end loop; end if;
      return t;
   end function;
   function mt_scale_game(present : boolean) return mt_halves is
      variable t : mt_halves := (others => x"0000");
   begin
      if present then for j in t'range loop t(j) := std_logic_vector(to_unsigned((2053 * j + 977) mod 65536, 16)); end loop; end if;
      return t;
   end function;
   signal mt_quant : mt_bytes := mt_quant_game(MDEC_TABLES);
   signal mt_scale : mt_halves := mt_scale_game(MDEC_TABLES);
   signal mdec_tap : std_logic := '0';
'''
RESET_OLD = '''\
         if r1_dev_reset = '1' then
            n := 0; rd := 0; control := "000"; fmt := "0000"; state := 0; words := (others => '0'); second := false; clear := false;
'''
RESET_NEW = RESET_OLD + '''\
            if r1_scrub_s = '1' and MDEC_WIPE then mt_quant <= mt_quant_game(false); mt_scale <= mt_scale_game(false); end if;   -- mdec-tap
'''
QUANT_OLD = '''\
                  when 2 =>
                     pop := true; count := count + 1;
'''
QUANT_NEW = '''\
                  when 2 =>
                     for k in 0 to 3 loop mt_quant(4 * count + k) <= d(8 * k + 7 downto 8 * k); end loop;   -- mdec-tap
                     pop := true; count := count + 1;
'''
SCALE_OLD = '''\
                  when 3 =>
                     pop := true; count := count + 1;
'''
SCALE_NEW = '''\
                  when 3 =>
                     mt_scale(2 * count) <= d(15 downto 0); mt_scale(2 * count + 1) <= d(31 downto 16);   -- mdec-tap
                     pop := true; count := count + 1;
'''
COMMAND_OLD = '''\
            if mux_bus_mdec_write = '1' and mux_bus_mdec_addr = x"0" then
'''
COMMAND_NEW = COMMAND_OLD + '''\
               assert mdec_tap = '0' report "MDEC command word with the table tap on" severity failure;   -- mdec-tap
'''
READ_OLD = '''\
         elsif a = 16#180C# then
            exp1_q <= "0000000" & spu_quiet;
'''
READ_NEW = '''\
         elsif a = 16#1824# then   -- mdec-tap: quantisation byte SS_ADR[6:0]
            if mdec_tap = '1' then exp1_q <= mt_quant(ss_adr mod 128); else exp1_q <= x"00"; end if;
         elsif a = 16#1825# then   -- mdec-tap: byte SS_ADR[0] of scale word SS_ADR[6:1]
            if mdec_tap = '0' then exp1_q <= x"00";
            elsif ss_adr mod 2 = 0 then exp1_q <= mt_scale((ss_adr mod 128) / 2)(7 downto 0);
            else exp1_q <= mt_scale((ss_adr mod 128) / 2)(15 downto 8); end if;
''' + READ_OLD
HOLD_OLD = "               spu_hold <= mux_bus_exp1_dataWrite(0);\n"
HOLD_NEW = HOLD_OLD + "               mdec_tap <= mux_bus_exp1_dataWrite(1);   -- mdec-tap\n"
COPY_OLD = '''               assert cp_busy = '0' report "COMMAND while a copy runs" severity failure;\n'''
COPY_NEW = COPY_OLD + '''               assert mdec_tap = '0' report "copy command with the MDEC table tap on" severity failure;   -- mdec-tap\n'''
DRIVE_OLD = '            assert CD_LEFT > 0 or lba <= cd_capt + 1\n'
DRIVE_NEW = '            assert CD_LEFT > 0 or lba <= cd_capt + 1 + CD_SLACK   -- mdec-tap: CD_SLACK\n'
FINISH_OLD = '''                  assert spu_hold = '0' report "SPU still held at the finish" severity failure;\n'''
FINISH_NEW = FINISH_OLD + '''\
                  assert mdec_tap = '0' report "MDEC table tap still on at the finish" severity failure;   -- mdec-tap
                  if MDEC_EXPECT then
                     for i in mt_quant'range loop
                        assert mt_quant(i) = mt_quant_game(true)(i) report "MDEC quantisation byte " & integer'image(i) & " at the finish is " &
                           to_hstring(mt_quant(i)) & ", not the game's" severity failure;
                     end loop;
                     for j in mt_scale'range loop
                        assert mt_scale(j) = mt_scale_game(true)(j) report "MDEC scale word " & integer'image(j) & " at the finish is " &
                           to_hstring(mt_scale(j)) & ", not the game's" severity failure;
                     end loop;
                     report "MDEC tables at the finish: the game's (128 bytes, 64 halfwords)" severity note;
                  end if;
'''


def bench_text(text):
    """The R1 / stage2-mark bench with the table tap in the device model and tables in the MDEC model."""
    if 'mdec-tap' in text:
        raise ValueError('mdec-tap bench: already applied')
    for old, new in ((GENERIC_OLD, GENERIC_OLD + GENERICS), (SIGNAL_OLD, SIGNAL_OLD + SIGNALS), (RESET_OLD, RESET_NEW),
                     (QUANT_OLD, QUANT_NEW), (SCALE_OLD, SCALE_NEW), (COMMAND_OLD, COMMAND_NEW), (READ_OLD, READ_NEW),
                     (HOLD_OLD, HOLD_NEW), (DRIVE_OLD, DRIVE_NEW), (FINISH_OLD, FINISH_NEW)):
        text = once(text, old, new)
    waits = COPY_OLD.replace("cp_busy = '0' report", "cp_busy = '0' or DEAD_COPY > 0 report")   # the stage2-mark bench with the waits generics
    old = COPY_OLD if COPY_OLD in text else waits
    if old not in text:
        raise ValueError('mdec-tap bench anchor: copy command')
    return text.replace(old, old + COPY_NEW[len(COPY_OLD):])


def state_problems(savebuf):
    """savebuf.hex of a save (DUMP_BUF): what is wrong with the tables in the state (empty: nothing)."""
    lines = savebuf.read_text().split()
    words = [int(w, 16) for w in lines[2:2 + 512]]
    found = []
    if not words[59] >> 19 & 1:
        found.append(f'page 0 word 59 is {words[59]:08X}: bit 19 (tables in the state) is not set')
    for w in range(32):
        want = sum(quant_byte(4 * w + k) << 8 * k for k in range(4))
        if words[128 + w] != want:
            found.append(f'page 0 word {128 + w} is {words[128 + w]:08X}, not quantisation {want:08X}')
            break
    for w in range(32):
        want = scale_half(2 * w) | scale_half(2 * w + 1) << 16
        if words[160 + w] != want:
            found.append(f'page 0 word {160 + w} is {words[160 + w]:08X}, not scale {want:08X}')
            break
    return found


def wrap_simulate(simulate):
    def wrapped(directory, generics, tag):
        generics = dict(generics)
        if str(generics.get('PLANT_BUF', '')).lower() == 'true':
            generics['MDEC_TABLES'] = 'false'
        slack = os.environ.get('MDEC_TAP_CD_SLACK')
        if slack and 'SAVE_AT' in generics:
            generics['CD_SLACK'] = slack
            print(f"  mdec-tap: '{tag}' runs with CD_SLACK={slack} (the drive may read on {1 + int(slack)} sectors before CD_FREEZE)", flush=True)
        savebuf = directory / 'savebuf.hex'
        dumped = str(generics.get('DUMP_BUF', '')).lower() == 'true'
        if dumped and savebuf.exists():
            savebuf.unlink()
        ok, values, image, why = simulate(directory, generics, tag)
        if ok and dumped:
            found = state_problems(savebuf)
            print(f"  mdec-tap: state of '{tag}': " + ('tables stored, bit 19 set' if not found else '; '.join(found)), flush=True)
            if found:
                return False, values, image, 'mdec-tap state: ' + found[0]
        if ok:
            values = dict(values, mdec_tables='game' if generics.get('MDEC_EXPECT', 'true') == 'true' else 'unchecked',
                          mdec_start='zero' if generics.get('MDEC_TABLES') == 'false' else 'game')
        return ok, values, image, why
    return wrapped
