"""Bench scenarios for the routine steps stage2-mark and load-check
(scripts/prepare_save_stage2_mark.py, scripts/prepare_save_load_check.py).

Used by run_save_project_cpu.py --stage2-bench. Run directly, it calls this checkout's
run_save_project_cpu.py with the same arguments and the fixture's scripts first on the
module path (a worktree without references/ cannot import its own copies of them):
  run_save_stage2_mark.py PROJECT --fixture-root ROOT --output DIR --staged-full-context \\
      --staged-scalar-ring [--routine r1] --routine-step stage2-mark [--routine-step load-check] \\
      --stage2-bench --scenario NAME ...
The fixture's bench (sim/reference/tb_staged_save_cpu.vhd, after the paired-format and R1
transforms) gets, only with --routine-step or --stage2-bench:
  STAGE2_FILE  the image STAGE2_WARM plants at STAGE2 (default "stage2.hex"),
  PAGE_MARK    1: staging page word 508 holds MARK_OK from the start, as after an
               earlier request since configuration (the page is zero otherwise),
  STATE_FAULT  at the load request: 1 = page 0 word 38 one less, 2 = bitmap bit 2 flipped,
               (4 = a stream word, 5 = the card slot's overflow, 6 = a page 1 word, 7 = a page 0 word: save_checksum_bench.py),
  a compare of every instruction retired at 0x80200000-0x80201FFF with stage2.hex,
  and a log of every write to staging page word 508: "MARK cpu" (a bus write of the
  routine, one line per byte), "MARK copy" (the device's copy engine: buffer or SPU RAM
  into the page), "MARK entry" (the word when the routine acknowledges a request or a
  retry), "MARK final" (the word and its last writer at the end of the run).
Scenarios (after the reference run, in the order asked for). "marked" = stage 2 planted
and the marker set, as in a later request of one session (no unpack: minutes, not an hour):
  save                    cold: nothing at STAGE2, page zero
  save warm               stage 2 planted, page zero (as after a restart with SDRAM kept)
  save warm marked        stage 2 planted, marker set
  save damaged stage 2    damaged stage 2 planted (stamp intact, 1e-4 of the other bits
                          flipped and one bit of stage 2's first instruction), page zero:
                          the defect
  save damaged 1e-3       the same with 1e-3 of the bits flipped
  save damaged single     the same with the one bit of the first instruction alone
  save+load damaged RATE  damaged stage 2 planted, page zero; a save, then a load
                          (RATE: 1e-4, 1e-3 or single; "save damaged RATE" likewise)
  load first cold         a restarted core: the plain save's state planted in the buffer, the
                          only request is a load; nothing at STAGE2, page zero
  load first warm         the same with stage 2 planted intact, page zero
  load first damaged RATE the same with a damaged stage 2 planted
  bad load damaged        damaged stage 2 (1e-4) planted, page zero; the first request is
                          a load with no state in the buffer (refused by the magic word)
  save damaged marked     damaged stage 2 planted, marker set: what the step still trusts
                          (expected to fail; the marker is the only guard)
The load-first scenarios use the buffer plant of the load-first branch (commit 6459909e:
bench generics DUMP_BUF / PLANT_BUF): they need that branch's checkout as --fixture-root and
the plain 'save' earlier in the same run (it writes savebuf.hex).
Every run also logs "DAMAGED exec": an instruction retired from stage 2's SDRAM whose
word differs from the intact stage2.hex (the first 8, and the total at the end).
  save GPU busy           stage 2 planted, page zero, the GPU busy past the unpack: retries
  save GPU busy marked    marked, the GPU busy: retries
  save never quiet marked marked, never a quiet point: the request gives up
  bad load marked         marked, a load with no state in the buffer: refused (`bad`)
  save+load               cold save, then a load in the same run (the second request)
  save+load marked        marked save, then a load
  load short count        marked save, then a load of the state with word 38 one less
  load absurd count       the same with the CD FIFO count 0x7FFFFFFF: refused (waits fifo-check)
  load absurd count clamped  the same for a routine with fifo-clamp only: the load ends
  save dead copy / save dead copy later / save dead port   (waits; --bench-wait-bound) the copy
                          engine busy for ever from its first / sixth command, the SPU port busy
                          for ever: the request ends with an error
  save retries above limit   the retry counter starts at 1000 and no quiet point comes
  load bitmap flipped     marked save, then a load of the state with a bitmap bit flipped
"""
import hashlib
import json
import os
import random

MARK_WORD = 508
MARK_OK = 0x53324F4B
DAMAGE_SEED = 20261111
RATES = {'1e-4': 1e-4, '1e-3': 1e-3, 'single': 0}   # flipped bits per bit of stage 2 (stamp word left out);
                                                    # single: FORCED_FLIP alone
# word 1 of stage 2 is quiet:'s first instruction, lui $t1, 0x1F80 (0x3C091F80), which every
# request executes first in stage 2. Bit 26 makes it xori $t1, $zero, 0x1F80.
FORCED_FLIP = (1, 26)


def bench_text(text):
    """The generated bench with STAGE2_FILE, PAGE_MARK, STATE_FAULT and the marker log (exact anchors)."""
    def once(old, new):
        nonlocal text
        if text.count(old) != 1:
            raise ValueError('stage2-mark bench anchor changed: ' + old[:70])
        text = text.replace(old, new)
    word = f'page({MARK_WORD})'
    at = '" cycle=" & integer\'image(cycle_count) severity note;\n'
    once('      STAGE2_WARM : boolean := false; -- stage 2 already unpacked at STAGE2 (stage2.hex), as after an earlier trap\n',
         '      STAGE2_WARM : boolean := false; -- stage 2 already unpacked at STAGE2 (stage2.hex), as after an earlier trap\n'
         '      STAGE2_FILE : string := "stage2.hex";  -- stage2-mark bench: the image STAGE2_WARM plants\n'
         '      PAGE_MARK  : natural  := 0;     -- stage2-mark bench: 1 = staging page word 508 holds MARK_OK from the start\n'
         '      STATE_FAULT : natural := 0;     -- load-check bench: at the load request 1 = word 38 one less, 2 = bitmap bit 2 flipped\n'
         '      QUIET_RULE : boolean := false;  -- check spu-quiet-poll: every SPU RAM page command follows a quiet read of SPU_HOLD\n'
         '      DEAD_COPY  : natural  := 0;     -- waits bench: from this page-copy command on (1 = the first) the copy engine stays busy\n'
         '      DEAD_SS    : boolean  := false; -- waits bench: the SPU port stays busy after its first command\n'
         '      PAGE_RETRIES : natural := 0;    -- waits bench: staging page word 511 (RETRIES) at the start\n'
         '      PLANT_WRONG : boolean := false; -- load-first bench: word 0 of every planted page from 2 on is inverted\n')
    # a dead copy engine: the device ignores a command while busy
    if text.count('               assert cp_busy = \'0\' report "COMMAND while a copy runs" severity failure;\n') != 2:
        raise ValueError('stage2-mark bench anchor changed: COMMAND while a copy runs')
    text = text.replace('               assert cp_busy = \'0\' report "COMMAND while a copy runs" severity failure;\n',
                        '               assert cp_busy = \'0\' or DEAD_COPY > 0 report "COMMAND while a copy runs" severity failure;\n')
    once("                     if mux_bus_exp1_dataWrite(1) = '1' then page(k) := buf(p * 512 + k);\n",
         "                     if cp_busy = '1' then null;   -- (dead copy engine: ignored)\n"
         "                     elsif mux_bus_exp1_dataWrite(1) = '1' then page(k) := buf(p * 512 + k);\n")
    dead_ss = "ss_busy <= '1'; ss_left := 3 + to_integer(lfsr(2 downto 0));"
    if dead_ss not in text:
        raise ValueError('stage2-mark bench anchor changed: ss_left')
    text = text.replace(dead_ss, dead_ss + " if DEAD_SS then ss_left := 0; end if;")
    if 'PLANT_BUF' in text:
        once('            report "planted save buffer: " & integer\'image(sbn) & " pages" severity note;\n',
             '            if PLANT_WRONG then for q in 2 to sbn - 1 loop buf(q * 512) := not buf(q * 512); end loop; end if;\n'
             '            report "planted save buffer: " & integer\'image(sbn) & " pages" severity note;\n')
    once('         elsif a = 16#180C# then\n            exp1_q <= "0000000" & spu_quiet;\n',
         '         elsif a = 16#180C# then\n            exp1_q <= "0000000" & spu_quiet;\n'
         "            if spu_quiet = '1' and mux_bus_exp1_write = '0' then quiet_polled := true; end if;\n")
    once('               assert spu_hold = \'1\' and spu_quiet = \'1\' report "SPU RAM copy while the SPU runs" severity failure;\n',
         '               assert spu_hold = \'1\' and spu_quiet = \'1\' report "SPU RAM copy while the SPU runs" severity failure;\n'
         '               assert quiet_polled or not QUIET_RULE\n'
         '                  report "spu-quiet-poll: SPU RAM page command without a quiet read of SPU_HOLD since the command before" severity failure;\n'
         '               quiet_polled := false;\n')
    once('            high(0 to 2047) := load("stage2.hex", 2048, false);\n',
         '            high(0 to 2047) := load(STAGE2_FILE, 2048, false);\n')
    once('   constant exp1_rom : words(0 to 2047) := load("rom_exp1.hex", 2048, false);\n',
         '   constant exp1_rom : words(0 to 2047) := load("rom_exp1.hex", 2048, false);\n'
         '   constant stage2_ref : words(0 to 2047) := load("stage2.hex", 2048, false);   -- stage2-mark bench: the intact stage 2\n'
         '   signal damaged_execs : natural := 0;\n')
    once('         if reset = \'0\' and cpu_mem_request = \'1\' and cpu_mem_isData = \'0\' and\n'
         '            cpu_mem_addressInstr(31 downto 12) = x"BF000" then\n',
         '         if reset = \'0\' and ce = \'1\' and cpu_cpu_done = \'1\' and cpu_cpu_export.pc(31 downto 13) = x"8020" & "000" and\n'
         '            std_logic_vector(cpu_cpu_export.opcode) /= stage2_ref(to_integer(cpu_cpu_export.pc(12 downto 2))) then\n'
         '            damaged_execs <= damaged_execs + 1;\n'
         '            if damaged_execs < 8 then\n'
         '               report "DAMAGED exec pc=" & to_hstring(cpu_cpu_export.pc) & " opcode=" & to_hstring(cpu_cpu_export.opcode) &\n'
         '                  " intact=" & to_hstring(stage2_ref(to_integer(cpu_cpu_export.pc(12 downto 2)))) &\n'
         f'                  {at}'
         '            end if;\n'
         '         end if;\n'
         '         if reset = \'0\' and cpu_mem_request = \'1\' and cpu_mem_isData = \'0\' and\n'
         '            cpu_mem_addressInstr(31 downto 12) = x"BF000" then\n')
    once("   signal save_mode_s : std_logic := '0';",
         "   signal mark_s : std_logic_vector(31 downto 0) := (others => '0');   -- stage2-mark bench\n"
         "   signal mark_writer_s : natural := 0;                                -- 0 none, 1 routine (bus), 2 copy engine\n"
         "   signal save_mode_s : std_logic := '0';")
    once("      variable page : words(0 to 511) := (others => (others => '0'));\n",
         "      impure function page_start return words is\n"
         "         variable r : words(0 to 511) := (others => (others => '0'));\n"
         "      begin\n"
         f"         if PAGE_MARK /= 0 then r({MARK_WORD}) := x\"{MARK_OK:08X}\"; end if;\n"
         "         r(511) := std_logic_vector(to_unsigned(PAGE_RETRIES, 32));\n"
         "         return r;\n"
         "      end function;\n"
         "      variable page : words(0 to 511) := page_start;\n"
         "      variable mark_last : natural := 0;\n"
         "      variable quiet_polled : boolean := false;\n"
         "      variable copies_n : natural := 0;\n")
    # the routine's bus writes (8-bit bus: one byte each)
    once('               page((a - 16#1000#) / 4)(8 * (a mod 4) + 7 downto 8 * (a mod 4)) := mux_bus_exp1_dataWrite;\n',
         '               page((a - 16#1000#) / 4)(8 * (a mod 4) + 7 downto 8 * (a mod 4)) := mux_bus_exp1_dataWrite;\n'
         f'               if (a - 16#1000#) / 4 = {MARK_WORD} then\n'
         '                  mark_last := 1;\n'
         f'                  report "MARK cpu byte=" & integer\'image(a mod 4) & " word=" & to_hstring({word}) &\n'
         f'                     {at}'
         '               end if;\n')
    # the copy engine: SPU RAM -> page, buffer -> page
    once("               cp_busy <= '1'; busy_left := 200 + to_integer(lfsr(7 downto 0));\n",
         "               if mux_bus_exp1_dataWrite(1) = '1' then\n"
         "                  mark_last := 2;\n"
         f"                  report \"MARK copy from=spuram page=\" & integer'image(page_lo) & \" word=\" & to_hstring({word}) &\n"
         f"                     {at}"
         "               end if;\n"
         "               cp_busy <= '1'; busy_left := 200 + to_integer(lfsr(7 downto 0));\n")
    once("                  cp_busy <= '1'; busy_left := 20 + to_integer(lfsr(5 downto 0));\n",
         "                  if mux_bus_exp1_dataWrite(1) = '1' then\n"
         "                     mark_last := 2;\n"
         f"                     report \"MARK copy from=buffer page=\" & integer'image(page_hi * 256 + page_lo) & \" word=\" & to_hstring({word}) &\n"
         f"                        {at}"
         "                  end if;\n"
         "                  cp_busy <= '1'; busy_left := 20 + to_integer(lfsr(5 downto 0));\n"
         "                  copies_n := copies_n + 1;\n"
         "                  if DEAD_COPY > 0 and copies_n >= DEAD_COPY then busy_left := 0; end if;\n")
    once("            elsif a = 16#1804# then\n               trap <= '0'; in_handler <= '1';\n",
         "            elsif a = 16#1804# then\n               trap <= '0'; in_handler <= '1';\n"
         f"               report \"MARK entry word=\" & to_hstring({word}) & \" load=\" & std_logic'image(want_load) &\n"
         f"                  {at}")
    once('         spu_regs_s <= regs; spu_ss_s <= ss; spu_ram_s <= spuram; spu_samples_s <= decoded;\n',
         '         spu_regs_s <= regs; spu_ss_s <= ss; spu_ram_s <= spuram; spu_samples_s <= decoded;\n'
         f'         mark_s <= {word}; mark_writer_s <= mark_last;\n')
    once('                  report "PASS save trap run";\n',
         '                  report "DAMAGED total=" & integer\'image(damaged_execs) & " cycle=" & integer\'image(cycle_count) severity note;\n'
         '                  report "MARK final word=" & to_hstring(mark_s) & " writer=" & integer\'image(mark_writer_s) &\n'
         f'                     {at}'
         '                  report "PASS save trap run";\n')
    once("               want_load <= '1'; trap <= '1';\n",
         "               if STATE_FAULT = 1 then buf(38) := std_logic_vector(unsigned(buf(38)) - 1); end if;\n"
         "               if STATE_FAULT = 2 then buf(64)(2) := not buf(64)(2); end if;\n"
         "               if STATE_FAULT = 3 then buf((to_integer(unsigned(buf(38))) - 1) * 512) := x\"7FFFFFFF\"; end if;\n"
         "               want_load <= '1'; trap <= '1';\n")
    return text


def marker_log(text):
    """The MARK lines of one run -> a summary dict."""
    import re
    entries, cpu, copies, final, damaged, damaged_total = [], [], [], None, [], None
    for line in text.splitlines():
        if 'DAMAGED ' in line:
            fields = dict(re.findall(r'(\w+)=(\w+)', line[line.index('DAMAGED '):]))
            if 'DAMAGED exec' in line:
                damaged.append((int(fields['cycle']), fields['pc'], fields['opcode'], fields['intact']))
            else:
                damaged_total = int(fields['total'])
            continue
        if 'MARK ' not in line:
            continue
        fields = dict(re.findall(r'(\w+)=([\w\']+)', line[line.index('MARK '):]))
        cycle = int(fields.get('cycle', 0))
        if 'MARK entry' in line:
            entries.append((cycle, fields['word'], 'load' if "'1'" in fields.get('load', '') else 'save'))
        elif 'MARK cpu' in line:
            if fields['byte'] == '3':                    # the word's last byte: a whole word write
                cpu.append((cycle, fields['word']))
        elif 'MARK copy' in line:
            copies.append((cycle, fields['from'], int(fields['page']), fields['word']))
        elif 'MARK final' in line:
            final = (fields['word'], {'0': 'none', '1': 'routine', '2': 'copy engine'}[fields['writer']])
    return dict(entries=entries, cpu_writes=cpu, copies=copies, final=final, damaged=damaged,
                damaged_total=damaged_total if damaged_total is not None else len(damaged))


def damage(directory, rate_name):
    """stage2-damaged-RATE.hex: stage2.hex with the stamp word kept, FORCED_FLIP, and
    round(rate * bits) seeded single-bit flips in the other words. Returns the record."""
    words = [int(w, 16) for w in (directory / 'stage2.hex').read_text().split()]
    rng = random.Random(f'{DAMAGE_SEED} {rate_name}')
    count = round(RATES[rate_name] * 32 * (len(words) - 1))
    chosen = [FORCED_FLIP]
    while len(chosen) < count + 1:
        flip = (rng.randrange(1, len(words)), rng.randrange(32))
        if flip not in chosen:
            chosen.append(flip)
    stamp = words[0]
    for word, bit in chosen:
        words[word] ^= 1 << bit
    assert words[0] == stamp
    path = directory / f'stage2-damaged-{rate_name}.hex'
    path.write_text(''.join(f'{w:08x}\n' for w in words))
    record = dict(file=path.name, seed=f'{DAMAGE_SEED} {rate_name}', rate=rate_name, stage2_words=len(words), stamp=f'{stamp:08x}',
                  flips=[dict(word=w, bit=b, address=f'{0x80200000 + 4 * w:08X}') for w, b in chosen],
                  sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  intact_sha256=hashlib.sha256((directory / 'stage2.hex').read_bytes()).hexdigest())
    (directory / f'stage2-damage-{rate_name}.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def scenarios(m, directory, names, marker, check=False, quiet=False, waits=False):
    """Run the named scenarios with the runner module m. marker: the routine has stage2-mark
    (its fetch-count and marker-log expectations apply); check: it has load-check.
    Returns a list of failure strings."""
    failures = []
    log = directory / 'checks.log'
    ok, ref, ref_image, why = m.simulate(directory, {}, 'reference')
    if not ok:
        return [f'reference run failed: {why}']
    cycles = int(ref['cycles'])
    print(f'  reference: {ref}', flush=True)
    save_at = cycles // 3
    for rate_name in RATES:
        record = damage(directory, rate_name)
        print(f"  damaged stage 2 ({rate_name} per bit): {len(record['flips'])} bits flipped in {record['stage2_words'] - 1} words, "
              f"stamp {record['stamp']} kept; first: "
              + ', '.join(f"{f['address']}.{f['bit']}" for f in record['flips'][:6]), flush=True)
    save_cycles = int(os.environ['SAVE_CYCLES_HINT']) if os.environ.get('SAVE_CYCLES_HINT') else None
    save_fetches = int(os.environ['SAVE_FETCHES_HINT']) if os.environ.get('SAVE_FETCHES_HINT') else None
    warm = dict(SAVE_AT=save_at, STAGE2_WARM='true')
    marked = dict(warm, PAGE_MARK=1)
    plain = dict(saves='1', saves_failed='0', loads='0')
    loaded = dict(saves='1', loads='1', loads_failed='0', spu_rekeys='0')
    refused = dict(saves='1', loads='0', loads_failed='1')
    first = dict(saves='0', loads='1', loads_failed='0', spu_rekeys='0')
    plant = 'PLANT_BUF' in (m.ROOT / 'sim/reference/tb_staged_save_cpu.vhd').read_text()
    load_first = dict(LOAD_AT=save_at + cycles // 3, PLANT_BUF='true')
    warm_cycles = int(os.environ.get('WARM_CYCLES_HINT', '174331'))    # a save with stage 2 reused (cycles of the whole run)
    table = {
        'save': (dict(SAVE_AT=save_at, **(dict(DUMP_BUF='true') if plant else {})), plain, False),
        'load first cold': (load_first, first, True),
        'load first warm': (dict(load_first, STAGE2_WARM='true'), first, True),
        'save warm': (warm, plain, False),
        'save warm marked': (marked, plain, False),
        'save damaged stage 2': (dict(warm, STAGE2_FILE='stage2-damaged-1e-4.hex'), plain, False),
        'save damaged 1e-3': (dict(warm, STAGE2_FILE='stage2-damaged-1e-3.hex'), plain, False),
        'save damaged single': (dict(warm, STAGE2_FILE='stage2-damaged-single.hex'), plain, False),
        'save damaged marked': (dict(marked, STAGE2_FILE='stage2-damaged-1e-4.hex'), plain, False),
        'save+load damaged': (dict(warm, STAGE2_FILE='stage2-damaged-1e-4.hex', LOAD_DELAY=cycles // 3), loaded, True),
        'bad load damaged': (dict(LOAD_AT=save_at, STAGE2_WARM='true', STAGE2_FILE='stage2-damaged-1e-4.hex'),
                             dict(saves='0', loads='0', loads_failed='1'), False),
        'save GPU busy marked': (dict(marked, GPU_HOLD=m.GPU_BUSY), plain, False),
        'save never quiet marked': (dict(marked, GPU_HOLD=m.GPU_NEVER),
                                    dict(saves='0', saves_failed='1', loads='0', retries=str(m.TEST_RETRY_MAX)), False),
        'bad load marked': (dict(LOAD_AT=save_at, STAGE2_WARM='true', PAGE_MARK=1),
                            dict(saves='0', loads='0', loads_failed='1'), False),
        'save+load': (dict(SAVE_AT=save_at, LOAD_DELAY=cycles // 3), loaded, True),
        'save+load marked': (dict(marked, LOAD_DELAY=cycles // 3), loaded, True),
        'load short count': (dict(marked, LOAD_DELAY=cycles // 3, STATE_FAULT=1), refused, False),
        'load bitmap flipped': (dict(marked, LOAD_DELAY=cycles // 3, STATE_FAULT=2), refused, False),
        # checksum (save_checksum_bench.py): a damaged stream word; the card slot's overflow at the load
        'load stream word': (dict(marked, LOAD_DELAY=cycles // 3, STATE_FAULT=4), refused, False),
        'load overflow': (dict(marked, LOAD_DELAY=cycles // 3, STATE_FAULT=5), refused, False),
        'load page1 word': (dict(marked, LOAD_DELAY=cycles // 3, STATE_FAULT=6), refused, False),
        'load page0 word': (dict(marked, LOAD_DELAY=cycles // 3, STATE_FAULT=7), refused, False),
    }
    gave_up = dict(saves='0', saves_failed='1', loads='0')
    table.update({
        # waits: a dead device ends the request with an error (the bench bound makes it minutes)
        'save dead copy': (dict(marked, DEAD_COPY=1), gave_up, False),
        'save dead copy later': (dict(marked, DEAD_COPY=6), gave_up, False),
        'save dead port': (dict(marked, DEAD_SS='true'), gave_up, False),
        # RETRIES above the limit and never a quiet point: gives up at the first retry
        'save retries above limit': (dict(marked, PAGE_RETRIES=1000, GPU_HOLD=m.GPU_NEVER), dict(gave_up, retries='0'), False),
        'load absurd count': (dict(marked, LOAD_DELAY=cycles // 3, STATE_FAULT=3), refused, False),
        # the waits step's fifo-clamp part without its fifo-check part: the absurd count is cut to 4,095
        # bytes, the load ends (a state with a wrong CD FIFO content, so the final dump is not compared);
        # without the clamp the bench's CD model stops the run ("CD data FIFO overflow")
        'load absurd count clamped': (dict(marked, LOAD_DELAY=cycles // 3, STATE_FAULT=3), loaded, True),
        # the planted fault of the load-first scenarios: this one must FAIL
        'load first wrong page': (dict(load_first, PLANT_WRONG='true'), first, True),
    })
    for rate_name in RATES:
        image = f'stage2-damaged-{rate_name}.hex'
        table[f'save damaged {rate_name}'] = (dict(warm, STAGE2_FILE=image), plain, False)
        table[f'save+load damaged {rate_name}'] = (dict(warm, STAGE2_FILE=image, LOAD_DELAY=cycles // 3), loaded, True)
        table[f'load first damaged {rate_name}'] = (dict(load_first, STAGE2_WARM='true', STAGE2_FILE=image), first, True)
    if os.environ.get('SAVEBUF_FROM') and plant:
        # the plain save's buffer of an earlier run of the SAME routine and bench (the state does not
        # depend on the generics added since); otherwise the 'save' of this run writes it
        import shutil
        shutil.copy(os.environ['SAVEBUF_FROM'], directory / 'savebuf.hex')
        print('  savebuf.hex from ' + os.environ['SAVEBUF_FROM'] + ' sha256 '
              + hashlib.sha256((directory / 'savebuf.hex').read_bytes()).hexdigest()[:16], flush=True)
    for tag in names:
        if tag.startswith('load first') and not (plant and (directory / 'savebuf.hex').exists()):
            failures.append(f'{tag}: needs the load-first fixture and the save of this run (savebuf.hex)')
            continue
        if tag == 'save GPU busy':
            if save_cycles is None:
                failures.append(f'{tag}: needs the save run or SAVE_CYCLES_HINT')
                continue
            # busy until after the unpack of a cold start (the cold save's cycles less a reusing save's),
            # then m.GPU_BUSY more: the retry follows the unpack
            generics, expect, replays = dict(warm, GPU_HOLD=(save_cycles - warm_cycles) + m.GPU_BUSY), plain, False
        elif tag in table:
            generics, expect, replays = table[tag]
        else:
            raise ValueError('unknown stage2-mark scenario: ' + tag)
        generics = dict(generics, MAX_CYCLES=cycles * 20, **(dict(QUIET_RULE='true') if quiet else {}))
        if expect is refused and 'disc' in ref:
            # R1 bench: its runner resets memctrl, the joypad and the MDEC model at a load request
            # (R1_SCRUB, a wake) unless one of its failure-case generics is present. A refused load
            # must leave the devices as they are, so the scrub is kept out of these runs.
            generics = dict(generics, DISC_WRONG='false')
        before = log.stat().st_size
        ok, values, image, why = m.simulate(directory, generics, tag.replace(' ', '-').replace('+', '-'))
        with log.open() as handle:
            handle.seek(before)
            marks = marker_log(handle.read())
        fetches = int(values.get('handler_fetches', 0))
        error = None
        if not ok:
            error = why
        elif image != ref_image and tag != 'load absurd count clamped':
            error = 'final RAM differs from the reference'
        elif any(values.get(k) != v for k, v in expect.items()):
            error = f'counters, expected {expect}'
        elif replays != (int(values['spu_writes']) > int(ref['spu_writes'])):
            error = f'guest SPU writes {values["spu_writes"]} vs reference {ref["spu_writes"]}'
        elif expect is refused and values.get('load_guest_writes', '0') != '0':
            error = f'the refused load wrote {values["load_guest_writes"]} device registers'
        elif fetches == 0:
            error = 'the handler never ran'
        elif 'GPU busy' in tag and int(values.get('retries', 0)) == 0:
            error = 'no retry'
        elif error is None and tag == 'save dead copy later' and False:
            pass
        elif marker and marks['damaged_total']:
            error = f"{marks['damaged_total']} flipped instructions executed"
        elif marker and 'dead' not in tag and marks['final'] != (f'{MARK_OK:08X}', 'routine'):
            error = f'the marker at the end is {marks["final"]}, expected MARK_OK written by the routine'
        elif marker and any(word != f'{MARK_OK:08X}' for _, word, _ in marks['entries'][1:]):
            error = 'a later request or retry found no marker: ' + str(marks['entries'])
        elif marker and save_fetches is not None:
            # the step's fetch counts: an unpack costs about the whole of a cold save's fetches
            if (tag == 'save warm' or tag.startswith('save damaged')) and tag != 'save damaged marked' and abs(fetches - save_fetches) > save_fetches // 10:
                error = f'{fetches} handler fetches: no unpack (the cold save has {save_fetches})'
            elif tag == 'save warm marked' and fetches > save_fetches // 2:
                error = f'{fetches} handler fetches: unpacked with the marker set (the cold save has {save_fetches})'
            elif tag == 'save GPU busy' and fetches > save_fetches + save_fetches // 2:
                error = f'{fetches} handler fetches: more than one unpack (the cold save has {save_fetches})'
            elif tag.startswith('save+load') and 'marked' not in tag and not 0 <= fetches - save_fetches <= save_fetches // 2:
                error = f'{fetches} handler fetches: not one unpack and a reusing second request (the cold save has {save_fetches})'
            elif tag.startswith('load first') and fetches < save_fetches // 2:
                error = f'{fetches} handler fetches: the first request did not unpack (the cold save has {save_fetches})'
            elif False:
                error = f'{fetches} handler fetches: the second request unpacked again (the cold save has {save_fetches})'
        if ok and tag == 'save':
            save_cycles, save_fetches = int(values['cycles']), fetches
        digest = hashlib.sha256(image.encode()).hexdigest()[:16] if image else None
        if tag == 'load first wrong page':
            error = None if error else 'the wrong page was not noticed (the run passed)'
        if error:
            failures.append(f'{tag}: {error}')
        print(f'  {tag}: {values} dump_sha256={digest}' + (f'  <- {tag}: {error}' if error else ''), flush=True)
        copies = marks['copies']
        print(f"    marker word 508: at each entry {[(c, w, k) for c, w, k in marks['entries']]}; "
              f"routine writes {marks['cpu_writes']}; copy-engine writes {len(copies)}"
              + (f" (first {copies[0]}, last {copies[-1]})" if copies else '')
              + f"; at the end {marks['final']}", flush=True)
        if 'damaged' in tag or marks['damaged_total']:
            print(f"    flipped instructions executed: {marks['damaged_total']}"
                  + (f"; first (cycle, pc, word, intact word): {marks['damaged'][:3]}" if marks['damaged'] else ''), flush=True)
    return failures


if __name__ == '__main__':
    import importlib.util
    import sys
    from pathlib import Path
    here = Path(__file__).resolve().parent
    if '--fixture-root' not in sys.argv:
        raise SystemExit(__doc__)
    fixture = Path(sys.argv[sys.argv.index('--fixture-root') + 1]).resolve()
    sys.path[:0] = [str(fixture / 'scripts'), str(here)]
    spec = importlib.util.spec_from_file_location('run_save_project_cpu', here / 'run_save_project_cpu.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    sys.argv[0] = str(here / 'run_save_project_cpu.py')
    sys.exit(runner.main())
