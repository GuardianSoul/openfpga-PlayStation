#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Bench side of the early-load routine step (scripts/prepare_save_early_load.py) for
run_save_project_cpu.py ... --early-load-bench --scenario NAME ...

Text transforms on the generated CPU round-trip bench (after R1, stage2-mark and mdec-tap);
the CPU, memorymux, cache and memctrl are the project's RTL, the devices are MODELS.
Generics added:
  EARLY_ANSWER       the first FINISH 1 of a load is the routine's early answer: the request stays
                     open in the bench (its load checks run at the routine's exit, the SAVE_MODE 0
                     write, which then counts as the load's end)
  DISC_NEEDS_ANSWER  the disc model's remount makes no progress until the load is answered (the
                     Pocket serves no data slot while a Memory load is unanswered)
  CD_STOPPED         at the load request the drive is stopped: motor off, not reading; only GetID
                     (0x1A) starts the motor (INT3, INT2)
  CD_NODISC          the same without a disc: GetID answers INT5; after the refused load CD
                     IRQENA and index must be the game's
  RAM_ID             boot name planted in RAM at 0x8000B8B0: 0 none, 1 SCUS_941.63, 2 SLUS_008.92,
                     3 a non-serial name, 4 SCUS_941.64
  STATE_ID           at the load request the state's name words (page 0 words 193-197) become:
                     0 as saved, 1-4 as RAM_ID, 9 zero (a state from before the step)
  STATE_DID          disc identity (prepare_save_disc_id.py): at the load request the state's word 198
                     becomes: 0 as saved, 1 another disc's (0xFEDCBA), 9 zero (a state from before the step)
EARLY_LOAD_SAVEBUF=FILE in the environment (the savebuf.hex of an 'id stored' run of the SAME routine and
bench): the scenarios that save and then load take that state planted instead of saving first (the
bench's PLANT_BUF: a restarted core with an uploaded state; the load is the only request, stage 2 warm).
A save with the compress step is 1,050,000 clk1x in this bench, about 80 minutes; the load decisions
tested here do not depend on it. 'load GPU busy', 'save+load', 'id stored' and 'wake' are never changed.
GetID is added to the CD model in every run with this bench, and the disc identity tap: 0x1828-0x182A
read DISC_ID_BASE + 0x011111 * (the disc in the model's drive), 0x182B reads 0.
"""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_save_early_load as step

NAMES = {1: 'cdrom:\\SCUS_941.63;1', 2: 'cdrom:\\SLUS_008.92;1', 3: 'cdrom:\\MAIN.EXE;1', 4: 'cdrom:\\SCUS_941.64;1'}


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'early-load bench anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


def name_words(name):
    data = name.encode().ljust(20, b'\0')[:20]
    return [int.from_bytes(data[i:i + 4], 'little') for i in range(0, 20, 4)]


GENERIC_OLD = '      STAGE2_WARM : boolean := false; -- stage 2 already unpacked at STAGE2 (stage2.hex), as after an earlier trap\n'
GENERICS = '''\
      EARLY_ANSWER : boolean := false;      -- early-load: the first FINISH 1 of a load is an early answer
      DISC_NEEDS_ANSWER : boolean := false; -- early-load: a remount waits for the load's answer
      CD_STOPPED : boolean := false;        -- early-load: the drive is stopped at the load request (GetID starts it)
      CD_NODISC  : boolean := false;        -- early-load: stopped and no disc (GetID: INT5)
      RAM_ID     : natural := 0;            -- early-load: boot name in RAM (0 none)
      STATE_ID   : natural := 0;            -- early-load: the state's name at the load request (0 as saved, 9 zero)
      STATE_DID  : natural := 0;            -- disc-id: the state's identity word at the load request (0 as saved, 1 another, 9 zero)
      DEAD_QUIET : boolean := false;        -- waits: after the first read of SPU_HOLD that shows quiet, it reads 0 for ever
      GPU_AT     : natural := 0;            -- early-load: start of the GPU_HOLD window (0: SAVE_AT), so a load can retry
'''
SIGNAL_OLD = "   signal r1_scrub_s, r1_dev_reset : std_logic := '0';\n"


def signals():
    rows = ''.join(f"      if id = {k} then t := ({', '.join(f'x\"{w:08X}\"' for w in name_words(v))}); end if;\n" for k, v in NAMES.items())
    return f'''\
   -- early-load (save_early_load_bench.py)
   type el_words is array(0 to 4) of std_logic_vector(31 downto 0);
   function el_name(id : natural) return el_words is
      variable t : el_words := (others => (others => '0'));
   begin
{rows}      return t;
   end function;
   function el_plant(m : words; id : natural) return words is
      variable r : words(m'range) := m;
   begin
      if id > 0 then for i in 0 to 4 loop r({step.NAME_ADDRESS & 0x1FFFFF} / 4 + i) := el_name(id)(i); end loop; end if;
      return r;
   end function;
   signal el_answered_s : std_logic := '0';
   function el_pick(a, b : natural) return natural is begin if a > 0 then return a; else return b; end if; end function;
   constant el_gpu_from : natural := el_pick(GPU_AT, SAVE_AT);
'''


MEM_OLD = '      variable mem : words(0 to 524287) := load("ram.hex", 524288, true);\n'
MEM_NEW = '      variable mem : words(0 to 524287) := el_plant(load("ram.hex", 524288, true), RAM_ID);   -- early-load\n'
FINISH_OLD = "            elsif a = 16#1803# then\n               if want_save = '1' and mux_bus_exp1_dataWrite(0) = '1' then\n"
FINISH_NEW = '''\
            elsif a = 16#1803# and EARLY_ANSWER and want_load = '1' and el_answered_s = '0' and mux_bus_exp1_dataWrite(1 downto 0) = "01" then
               el_answered_s <= '1';   -- early-load: answered, the load goes on
               report "early-load: early answer at cycle " & integer'image(cycle_count) severity note;
            elsif a = 16#1803# or (a = 16#180E# and el_answered_s = '1' and want_load = '1' and mux_bus_exp1_dataWrite(0) = '0') then
               assert not (a = 16#1803# and el_answered_s = '1') report "early-load: a second answer after the early one" severity failure;
               if want_save = '1' and mux_bus_exp1_dataWrite(0) = '1' then
'''
DONE_OLD = "                  if mux_bus_exp1_dataWrite(0) = '1' then loads_done <= loads_done + 1;\n"
DONE_NEW = "                  if mux_bus_exp1_dataWrite(0) = '1' or a = 16#180E# then loads_done <= loads_done + 1;   -- early-load\n"
CODE_OLD = '               finish_code <= to_integer(unsigned(mux_bus_exp1_dataWrite));\n'
CODE_NEW = '''\
               if a = 16#180E# then finish_code <= 1; el_answered_s <= '0';   -- early-load: the answered load's end
               else finish_code <= to_integer(unsigned(mux_bus_exp1_dataWrite)); end if;
'''
SWAP_OLD = '               d_swap := d_swap - 1;\n               if d_swap = 0 then\n'
SWAP_NEW = '''\
               if (not DISC_NEEDS_ANSWER) or el_answered_s = '1' then d_swap := d_swap - 1; end if;   -- early-load
               if d_swap = 0 then
'''
STATE_OLD = "               if STATE_FAULT = 1 then buf(38) := std_logic_vector(unsigned(buf(38)) - 1); end if;\n"
DISC_ID_BASE = 0x139023
QUIET_OLD = '            exp1_q <= "0000000" & spu_quiet;\n'
QUIET_NEW = '''\
            -- waits bench (DEAD_QUIET): the SPU's own model stays quiet (its checks hold); only the read is dead
            if DEAD_QUIET and el_quiet_seen then exp1_q <= x"00"; else exp1_q <= "0000000" & spu_quiet; end if;
            if spu_quiet = '1' and mux_bus_exp1_read = '1' then el_quiet_pending := true; end if;
'''
QUIET_STEP_OLD = '         -- R1 disc model: one step a CPU cycle\n'
QUIET_STEP_NEW = '''\
         if el_quiet_pending and a /= 16#180C# then el_quiet_seen := true; end if;   -- waits bench: that read is over
''' + QUIET_STEP_OLD
QUIET_VAR_OLD = "      variable d_read_was : std_logic := '0';\n"
QUIET_VAR_NEW = QUIET_VAR_OLD + '      variable el_quiet_pending, el_quiet_seen : boolean := false;   -- waits bench\n'
TAP_OLD = '''\
         elsif a = 16#1823# then
            exp1_q <= "000000" & d_status;
'''
TAP_NEW = TAP_OLD + f'''\
         elsif a >= 16#1828# and a <= 16#182B# then   -- disc-id: the identity of the disc in the drive
            w := std_logic_vector(to_unsigned({DISC_ID_BASE} + 16#011111# * d_drive, 32));
            exp1_q <= w(8 * (a mod 4) + 7 downto 8 * (a mod 4));
'''
STATE_NEW = f'''\
               if STATE_DID = 9 then buf(198) := (others => '0');   -- disc-id
               elsif STATE_DID = 1 then buf(198) := x"00FEDCBA"; end if;
               if STATE_ID = 9 then for i in 0 to 4 loop buf({step.NAME_WORD} + i) := (others => '0'); end loop;   -- early-load
               elsif STATE_ID > 0 then for i in 0 to 4 loop buf({step.NAME_WORD} + i) := el_name(STATE_ID)(i); end loop; end if;
''' + STATE_OLD
FRESH_OLD = "         if CD_FRESH and want_load = '1' and not loading then\n"
FRESH_NEW = '''\
         if (CD_STOPPED or CD_NODISC) and want_load = '1' and not loading then   -- early-load: the BIOS stopped the drive
            motor := false; reading := false; playing := false;
         end if;
         if CD_NODISC and loads_failed /= el_failed then
            el_failed := loads_failed;
            assert irqena = "11111" and idx = 1 report "early-load: CD IRQENA / index not the game's after the refused load" severity failure;
            report "early-load: refused load, CD IRQENA and index as the game had them" severity note;
         end if;
''' + FRESH_OLD
GETID_OLD = '               when x"0C" => demuted := true; push(stat);\n'
GETID_NEW = GETID_OLD + '''\
               when x"1A" =>   -- early-load: GetID
                  el_getids := el_getids + 1;
                  report "early-load: GetID at cycle " & integer'image(cycle_count) severity note;
                  if CD_NODISC then flag := "00101"; push(x"11"); push(x"80");
                  else motor := true; push(stat); int2_left := 3000; end if;
'''
VAR_OLD = '      variable last_loads : natural := 0;\n'
VAR_NEW = VAR_OLD + '      variable el_failed, el_getids : natural := 0;   -- early-load\n'


def bench_text(text):
    if 'early-load' in text:
        raise ValueError('early-load bench: already applied')
    for old, new in ((GENERIC_OLD, GENERIC_OLD + GENERICS), (SIGNAL_OLD, SIGNAL_OLD + signals()), (MEM_OLD, MEM_NEW),
                     (FINISH_OLD, FINISH_NEW), (DONE_OLD, DONE_NEW), (CODE_OLD, CODE_NEW), (SWAP_OLD, SWAP_NEW), (STATE_OLD, STATE_NEW),
                     (FRESH_OLD, FRESH_NEW), (GETID_OLD, GETID_NEW), (VAR_OLD, VAR_NEW), (TAP_OLD, TAP_NEW), (QUIET_OLD, QUIET_NEW), (QUIET_STEP_OLD, QUIET_STEP_NEW), (QUIET_VAR_OLD, QUIET_VAR_NEW),
                     ('cycle_count >= SAVE_AT and cycle_count < SAVE_AT + GPU_HOLD', 'cycle_count >= el_gpu_from and cycle_count < el_gpu_from + GPU_HOLD')):
        text = once(text, old, new)
    return text


def scenarios(m, directory, names, disc_id=False):
    """The early-load scenarios; each against the run without save or load that has the same RAM_ID."""
    references = {}

    def reference(ram_id):
        if ram_id not in references:
            ok, ref, image, why = m.simulate(directory, dict(RAM_ID=ram_id), f'reference-id{ram_id}')
            if not ok:
                raise ValueError('reference run failed: ' + why)
            print(f'  reference RAM_ID={ram_id}: cycles {ref["cycles"]}', flush=True)
            references[ram_id] = (ref, image)
        return references[ram_id]
    cycles = int(reference(1)[0]['cycles'])
    at = cycles // 3
    marked = dict(SAVE_AT=at, STAGE2_WARM='true', PAGE_MARK=1, LOAD_DELAY=cycles // 3, QUIET_RULE='true')
    loaded = dict(saves='1', loads='1', loads_failed='0', finish='1')
    refused = dict(saves='1', loads='0', loads_failed='1', finish='2', load_guest_writes='0', disc_set_writes='0')
    switch = dict(LOAD_DISC=1, DISC_SYNC=64, DISC_NEEDS_ANSWER='true')
    table = {
        'id same': (1, dict(marked), loaded, True),
        'id foreign': (1, dict(marked, STATE_ID=2), refused, False),
        # the same letters, another serial (SCUS_941.64 against .63), the same disc number: refused by the
        # branch the fault id-not-refused removes ('id foreign' differs in the letters and is refused earlier)
        'id other serial': (1, dict(marked, STATE_ID=4), refused, False),
        # a save alone, its state dumped: page 0 words 193-197 must be the name in RAM
        'id stored': (1, dict(SAVE_AT=at, STAGE2_WARM='true', PAGE_MARK=1, QUIET_RULE='true', DUMP_BUF='true'), dict(saves='1', loads='0'), False),
        'id old state': (1, dict(marked, STATE_ID=9), loaded, True),
        'id state no serial': (1, dict(marked, STATE_ID=3), loaded, True),
        'id ram no serial': (3, dict(marked, STATE_ID=1), loaded, True),
        'id other disc': (4, dict(marked, STATE_ID=1, EARLY_ANSWER='true', **switch), dict(loaded, disc='0', disc_set_writes='2'), True),
        'id foreign other disc': (2, dict(marked, STATE_ID=1, **switch), dict(refused), False),
        # the disc switch alone (no names): the routine with the step answers first; today's times out
        'other disc': (0, dict(marked, EARLY_ANSWER='true', **switch), dict(loaded, disc='0', disc_set_writes='2'), True),
        'other disc today': (0, dict(marked, **switch, MAX_CYCLES=12000000),
                             dict(saves='1', loads='0', loads_failed='1', finish='2', disc='1', load_guest_writes='0'), False),
        'stopped drive': (1, dict(marked, CD_STOPPED='true'), loaded, True),
        'no disc': (1, dict(marked, CD_NODISC='true'), dict(saves='1', loads='0', loads_failed='1', finish='2', disc_set_writes='1'), False),
        'save+load': (1, dict(marked), loaded, True),
        # a load whose first attempts find the GPU busy (the load request of the marked run is at about 172,600)
        'load GPU busy': (1, dict(marked, GPU_AT=172000, GPU_HOLD=40000, MAX_CYCLES=1500000), dict(loaded), True),
        'load GPU busy long': (1, dict(marked, GPU_AT=172000, GPU_HOLD=120000, MAX_CYCLES=2000000), dict(loaded), True),
    }
    # disc identity (the routine with prepare_save_disc_id.py): RAM_ID 0 = no boot name in the running
    # machine, as during the BIOS screens and at a wake's trap
    refused_id = dict(saves='1', loads='0', loads_failed='1', finish='2', load_guest_writes='0', disc_set_writes='2')
    table.update({
        'bios same': (0, dict(marked, STATE_ID=1), loaded, True),
        'bios foreign': (0, dict(marked, STATE_ID=2, STATE_DID=1), refused_id, False),
        'bios old state': (0, dict(marked, STATE_ID=9, STATE_DID=9), refused_id, False),
        'id old state no identity': (1, dict(marked, STATE_ID=9, STATE_DID=9), loaded, True),
        'id same other identity': (1, dict(marked, STATE_DID=1), loaded, True),
        'bios other disc': (0, dict(marked, STATE_ID=1, EARLY_ANSWER='true', **switch), dict(loaded, disc='0', disc_set_writes='2'), True),
        # after the switch the disc is not the state's: the menu's disc comes back, nothing is loaded (the
        # bench counts the routine's exit after an early answer as the load's end)
        'bios other disc foreign': (0, dict(marked, STATE_ID=1, STATE_DID=1, EARLY_ANSWER='true', **switch),
                                    dict(saves='1', disc='1', load_guest_writes='0'), False),
        # ready-remount (audit-059 #4): the state's disc mounts, then the drive never gets ready: the
        # menu's disc comes back and nothing is loaded (routine g left the state's disc in the drive)
        'other disc not ready': (4, dict(marked, STATE_ID=1, EARLY_ANSWER='true', CD_NODISC='true', **switch),
                                 dict(saves='1', disc='1', load_guest_writes='0'), False),
        # switch-check: a game runs (a name of the same letters), the state is another game's (its identity
        # is not the other disc's): the switch is made after the early answer, then the menu's disc comes
        # back and nothing is loaded (routine g loaded the state onto the other disc)
        'id cross game other disc': (4, dict(marked, STATE_ID=1, STATE_DID=1, EARLY_ANSWER='true', **switch),
                                     dict(saves='1', disc='1', load_guest_writes='0'), False),
        # a wake: the state of this run's 'id stored' planted, the only request is a load held from the
        # reset, stage 2 not unpacked, the first mount still on its way, no boot name in RAM
        'wake': (0, dict(LOAD_AT=at + cycles // 3, PLANT_BUF='true', DISC_WAKE=3000, QUIET_RULE='true'),
                 dict(saves='0', loads='1', loads_failed='0', finish='1'), True),
    })
    planted = os.environ.get('EARLY_LOAD_SAVEBUF')
    if planted:
        shutil.copy(planted, directory / 'savebuf.hex')
        print(f'  savebuf.hex from {planted}', flush=True)
        for name, (ram_id, generics, expect, replays) in list(table.items()):
            if 'LOAD_DELAY' in generics and 'SAVE_AT' in generics and 'GPU_AT' not in generics and name != 'save+load':
                generics = {k: v for k, v in generics.items() if k not in ('SAVE_AT', 'LOAD_DELAY')}
                generics.update(LOAD_AT=at + cycles // 3, PLANT_BUF='true')
                table[name] = (ram_id, generics, dict(expect, saves='0'), replays)
    # waits: the quiet poll before an SPU RAM page command never sees quiet (run with --bench-wait-bound)
    table['save dead quiet'] = (1, dict(SAVE_AT=at, STAGE2_WARM='true', PAGE_MARK=1, QUIET_RULE='true', DEAD_QUIET='true'),
                                dict(saves='0', saves_failed='1', loads='0', finish='2'), False)
    failures = []
    for name in names:
        if name == 'wake' and not (directory / 'savebuf.hex').exists():
            failures.append("early-load wake: needs 'id stored' earlier in the same run (savebuf.hex)")
            continue
        if name not in table:
            raise ValueError('unknown early-load scenario: ' + name)
        ram_id, generics, expect, replays = table[name]
        ref, ref_image = reference(ram_id)
        generics = dict(dict(MAX_CYCLES=cycles * 20), **generics, RAM_ID=ram_id)
        if expect.get('loads') == '0':
            # the R1 bench resets memctrl, the joypad and the MDEC model at a load request (R1_SCRUB, a wake)
            # unless one of its failure-case generics is present; a refused load must find and leave the
            # devices as they are (as run_save_stage2_mark.py does for its refused loads)
            generics = dict(generics, DISC_WRONG='false')
        ok, values, image, why = m.simulate(directory, generics, 'el-' + name.replace(' ', '-').replace('+', '-'))
        error = None
        if not ok:
            error = why
        elif image != ref_image:
            error = 'final dump differs from the run without save or load'
        elif any(values.get(k) != v for k, v in expect.items()):
            error = f'counters, expected {expect}'
        elif replays != (int(values['spu_writes']) > int(ref['spu_writes'])):
            error = f'guest SPU writes {values["spu_writes"]} vs reference {ref["spu_writes"]}'
        if not error and name == 'id stored':
            words = [int(w, 16) for w in (directory / 'savebuf.hex').read_text().split()[2:2 + 512]]
            got = words[step.NAME_WORD:step.NAME_WORD + 5]
            if got != name_words(NAMES[ram_id]):
                error = 'state words 193-197 are ' + ' '.join(f'{w:08X}' for w in got) + ', not the name in RAM'
            else:
                print('  early-load id stored: state words 193-197 = ' + ' '.join(f'{w:08X}' for w in got), flush=True)
            if not error and disc_id:
                if words[198] != DISC_ID_BASE:
                    error = f'state word 198 is {words[198]:08X}, not the identity of the disc in the drive ({DISC_ID_BASE:08X})'
                else:
                    print(f'  disc-id id stored: state word 198 = {words[198]:08X}', flush=True)
        print(f'  early-load {name}: {values}' + (f'  <- {error}' if error else ''), flush=True)
        if error:
            failures.append(f'early-load {name}: {error}')
    return failures
