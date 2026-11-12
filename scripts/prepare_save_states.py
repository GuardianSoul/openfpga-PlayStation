#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Source transforms for CPU-assisted Pocket save states (notes/save-states-design.md, design B).

cpu.vhd: a `trapRequest` input taken like an interrupt (only while SR.IEc=1,
same blocking and EPC/SR handling) that vectors to 0xBF000000 (the EXP1
window, uncached) instead of 0x80000080. Tied low, nothing changes.

memorymux.vhd: the EXP1 stub becomes a real 8-bit device port. Writes and
the bus address go out (bus_exp1_write/addr/dataWrite, as on EXP2), and
instruction fetches from EXP1 go through the external bus like data reads
(uncached: the tag valid bits are left as they were, since the cache data
RAM is only written by SDRAM line fills).

Both are text transforms anchored on upstream lines; an anchor that moved is
an error, never a silent skip.
"""
import re


def _once(text, old, new, what):
    if text.count(old) != 1:
        raise ValueError(f'save states: {what} anchor found {text.count(old)} times')
    return text.replace(old, new, 1)


def _sub_once(text, pattern, repl, what):
    result, count = re.subn(pattern, repl, text, count=0, flags=re.M)
    if count != 1:
        raise ValueError(f'save states: {what} anchor found {count} times')
    return result


TRAP_VECTOR = 0xBF000000


def exp1_byte_bus_text(text):
    """The internal save device has one byte lane, including its first fetch.

    Keep programmable delays and register readback. The upstream external
    EXP1 stub allowed width/increment settings our internal ROM cannot use;
    firmware cannot repair these after fetching an invalid first opcode.
    """
    return _once(text, "ex1_memctrl when (ext_select_ex1 = '1') else",
                 "(\"10\" & ex1_memctrl(11 downto 0)) when (ext_select_ex1 = '1') else",
                 'internal EXP1 byte bus')


def cpu_trap_text(text):
    if 'trapRequest' in text:
        raise ValueError('save states: CPU already has trapRequest')
    text = _sub_once(text, r'^([ \t]*)irqRequest([ \t]*): in  std_logic;\n',
                     lambda m: m.group(0) + f'{m.group(1)}trapRequest{" " * max(1, len(m.group(2)) - 1)}: in  std_logic := \'0\';\n',
                     'irqRequest port')
    text = _once(text, '   signal exceptionBreakpoint          : std_logic;\n',
                 '   signal exceptionBreakpoint          : std_logic;\n'
                 "   signal exceptionTrap                : std_logic := '0';\n", 'exceptionBreakpoint signal')
    text = _sub_once(text, r'^([ \t]*FetchAddr[ \t]*<= x"80000040" when \(exceptionBreakpoint = \'1\'\) else\n)',
                     lambda m: m.group(1) + f'                      x"{TRAP_VECTOR:08X}" when (exception > 0 and exceptionTrap = \'1\') else\n',
                     'FetchAddr vector')
    text = _sub_once(text,
                     r"if \(exception = 0 and blockirq = '0' and cop0_SR\(0\) = '1' and "
                     r"\(cop0_SR\(10 downto 8\) and cop0_CAUSE\(10 downto 8\)\) /= \"000\"\) then",
                     "if (exception = 0 and blockirq = '0' and cop0_SR(0) = '1' and "
                     "(((cop0_SR(10 downto 8) and cop0_CAUSE(10 downto 8)) /= \"000\") or trapRequest = '1')) then",
                     'interrupt condition')
    text = _once(text, '   process (blockirq, cop0_SR, cop0_CAUSE, exception, stall,',
                 '   process (trapRequest, blockirq, cop0_SR, cop0_CAUSE, exception, stall,', 'fetch process sensitivity')
    # The trap reuses the interrupt path (exceptionNew5); it wins over a
    # pending game IRQ, which stays in CAUSE and is taken after the handler's rfe.
    text = _sub_once(text, r'^([ \t]*)exception           <= exceptionNew;\n',
                     lambda m: m.group(0) + f"{m.group(1)}exceptionTrap       <= exceptionNew5 and trapRequest and not exceptionNew1;\n",
                     'exception register')
    # The release accuracy step raises IBE outside RAM/BIOS/KSEG2. The trap
    # handler executes from EXP1, which memorymux_exp1_text serves below.
    # Keep this decode matched with the reverse preparation order as well.
    from prepare_cpu_exceptions import TAG, BAD_FETCH, SAVE_BAD_FETCH
    if TAG in text:
        text = _once(text, BAD_FETCH, SAVE_BAD_FETCH, 'CPU save-handler fetch error decode')
    return text


def memorymux_exp1_text(text):
    if 'bus_exp1_write' in text.replace('--bus_exp1_write', ''):
        raise ValueError('save states: memorymux already has an EXP1 write port')
    text = _once(text, '      --bus_exp1_addr        : out unsigned(22 downto 0); \n'
                       '      --bus_exp1_dataWrite   : out std_logic_vector(7 downto 0);\n'
                       '      bus_exp1_read        : out std_logic;\n'
                       '      --bus_exp1_write       : out std_logic;\n',
                 '      bus_exp1_addr        : out unsigned(12 downto 0);\n'
                 '      bus_exp1_dataWrite   : out std_logic_vector(7 downto 0);\n'
                 '      bus_exp1_read        : out std_logic;\n'
                 '      bus_exp1_write       : out std_logic;\n', 'EXP1 ports')
    old = "   bus_exp1_read     <= '1' when (ext_state = EXT_READ_NEXT and ext_select_ex1_saved = '1') else '0';\n"
    text = _once(text, old, old +
                 '   bus_exp1_addr      <= ext_bus_addr;\n'
                 "   bus_exp1_write     <= '1' when (ext_write_ena = '1' and ext_select_ex1_saved = '1') else '0';\n"
                 '   bus_exp1_dataWrite <= ext_dataWrite(7 downto 0);\n', 'EXP1 read strobe')
    # Instruction fetch from EXP1 (the save-state handler ROM): an external
    # 32-bit read of mem_addressInstr; mem_done/mem_dataRead come from ext_done.
    # Only address bits 12:0 take the fetch address: an external read uses
    # no others (ext_bus_addr is addressData_buf(12 downto 0), the device is
    # ext_select_*), and the upper bits keep IDLE's mem_addressData, so the
    # fetch adds a 13-bit address mux instead of a 32-bit one.
    old = ('                        else\n'
           '                           report "should never happen" severity failure; \n')
    new = ('                        elsif (mem_addressInstr(28 downto 23) = "111110") then -- EXP1 0x1F000000-0x1F7FFFFF (save states)\n'
           "                           ext_select_spu  <= '0';\n"
           "                           ext_select_cd   <= '0';\n"
           "                           ext_select_ex1  <= '1';\n"
           "                           ext_select_ex2  <= '0';\n"
           "                           ext_select_ex3  <= '0';\n"
           '                           addressData_buf(12 downto 0) <= mem_addressInstr(12 downto 0);\n'
           '                           reqsize_buf     <= "10";\n'
           '                           mem_tagvalids   <= mem_oldtagvalids;\n'
           '                           state           <= BUSREADEXTERNAL;\n' + old)
    text = _once(text, old, new, 'instruction fetch decode')
    return exp1_byte_bus_text(text)


TAP_PORT = '      bus_dataRead         : out std_logic_vector(31 downto 0);\n'


# The set tap: psx_save_exp1's last 0x1805-0x180A write (save_set_sel 1-6)
# and its byte, held until its next register write.
SET_PORTS = ("      save_set_sel         : in  std_logic_vector(2 downto 0) := (others => '0');\n"
             "      save_set_data        : in  std_logic_vector(7 downto 0) := (others => '0');\n")


def irq_set_text(text):
    """irq.vhd: I_STAT bits the save routine sets (no game can set them):
    bits 7:0 while save_set_sel is 1, bits 10:8 while it is 2."""
    if 'save_set_sel' in text:
        raise ValueError('save states: irq already has the set tap')
    text = _once(text, TAP_PORT, TAP_PORT + SET_PORTS, 'irq ports')
    old = '            I_STATUSNew := I_STATUSNew or (irqIn and (not irqIn_1));\n'
    return _once(text, old, old +
                 "            if (save_set_sel = \"001\") then I_STATUSNew := I_STATUSNew or resize(unsigned(save_set_data), 11); end if;\n"
                 "            if (save_set_sel = \"010\") then I_STATUSNew := I_STATUSNew or shift_left(resize(unsigned(save_set_data(2 downto 0)), 11), 8); end if;\n",
                 'irq status update')


def dma_set_text(text):
    """dma.vhd: DICR flag bits (24-30) the save routine sets while save_set_sel is 3."""
    if 'save_set_sel' in text:
        raise ValueError('save states: dma already has the set tap')
    text = _once(text, TAP_PORT, TAP_PORT + SET_PORTS, 'dma ports')
    return _sub_once(text, r'^([ \t]*)end if; -- ce\n',
                     lambda m: f"{m.group(1)}   if (save_set_sel = \"011\") then DICR_IRQs <= DICR_IRQs or unsigned(save_set_data(6 downto 0)); end if;\n"
                               + m.group(0), 'dma ce end')


def timer_tap_text(text):
    """timer.vhd: the hidden flags {irqDone, T_MODE(12:10)} per timer read out
    without side effects (a T_MODE read clears 11-12) and forced from
    save_set_data(3:0) while save_set_sel is 4 + the timer."""
    if 'save_set_sel' in text:
        raise ValueError('save states: timer already has the tap')
    text = _once(text, TAP_PORT, TAP_PORT + SET_PORTS +
                 "      save_timer_flags     : out std_logic_vector(11 downto 0);\n", 'timer ports')
    text = _sub_once(text, r'^begin[ \t]*\n', lambda m: m.group(0) + ''.join(
        f"\n   save_timer_flags({4 * i + 3} downto {4 * i}) <= timerArray({i}).irqDone & std_logic_vector(timerArray({i}).T_MODE(12 downto 10));"
        for i in range(3)) + '\n', 'timer architecture begin')
    return _sub_once(text, r'^([ \t]*)-- bus interface\n',
                     lambda m: f"{m.group(1)}for i in 0 to 2 loop\n"
                               f"{m.group(1)}   if (to_integer(unsigned(save_set_sel)) = 4 + i) then\n"
                               f"{m.group(1)}      timerArray(i).T_MODE(12 downto 10) <= unsigned(save_set_data(2 downto 0));\n"
                               f"{m.group(1)}      timerArray(i).irqDone <= save_set_data(3);\n"
                               f"{m.group(1)}   end if;\n"
                               f"{m.group(1)}end loop;\n\n" + m.group(0), 'timer bus interface')


def gpu_tap_text(text):
    """gpu.vhd: the GP1 05/06/07 display ranges, which no register reads back."""
    if 'save_gpu_ranges' in text:
        raise ValueError('save states: gpu already has the tap')
    text = _once(text, TAP_PORT, TAP_PORT + '      save_gpu_ranges      : out std_logic_vector(62 downto 0);\n', 'gpu ports')
    return _sub_once(text, r'^begin[ \t]*\n', lambda m: m.group(0) +
                     '\n   save_gpu_ranges <= std_logic_vector(vDisplayRange & hDisplayRange & vramRange);\n',
                     'gpu architecture begin')


CD_TAP_PORT = '      bus_dataRead         : out std_logic_vector(7 downto 0) := (others => \'0\');\n'


def cd_tap_text(text):
    """cd_top.vhd: the state the save routine needs to restart the drive on a
    load, as levels (no register gives it back, and a command from the
    routine at save time could land between the game's own commands and
    sector interrupts): stat byte, mode, XA filter, the last sector read and
    a busy bit (a command, a second response or a drive interrupt on its
    way, or a sector being copied into the data FIFO; with it 0 and IRQFLAG
    0 the game has every sector up to currentLBA).

    The data FIFO's unread bytes are part of a state (a game may leave the
    rest of a sector there for seconds): the routine reads them out at
    1F801802 and puts them back through the set tap, select 7 (psx_save_exp1
    CD_PUT): one FIFO write each time it turns 7."""
    if 'save_cd_state' in text:
        raise ValueError('save states: cd_top already has the tap')
    text = _once(text, CD_TAP_PORT, CD_TAP_PORT + '      save_cd_state        : out std_logic_vector(63 downto 0);\n' + SET_PORTS,
                 'cd_top ports')
    signals = ("   signal save_copy_busy : std_logic;\n"
               "   signal save_put_d     : std_logic := '0';\n")
    tap = ("\n   save_copy_busy <= '0' when (copyState = COPY_IDLE) else '1';\n"
           '   save_cd_state <= "0000000" & (working or cmd_busy or cmdPending or save_copy_busy or (pendingDriveIRQ(0) or pendingDriveIRQ(1) or\n'
           '                    pendingDriveIRQ(2) or pendingDriveIRQ(3) or pendingDriveIRQ(4))) &\n'
           '                    std_logic_vector(to_unsigned(currentLBA, 24)) & XaFilterChannel & XaFilterFile & modeReg & internalStatus;\n')
    text = _sub_once(text, r'^begin[ \t]*\n', lambda m: signals + m.group(0) + tap, 'cd_top architecture begin')
    anchor = '            -- if data fifo is reset while copy is still ongoing, stop copy immidiatly so fifo stays empty\n'
    return _once(text, anchor,
                 '            -- save states: a byte the save routine puts back into the data fifo\n'
                 '            save_put_d <= \'0\';\n'
                 '            if (save_set_sel = "111") then save_put_d <= \'1\'; end if;\n'
                 '            if (save_set_sel = "111" and save_put_d = \'0\') then\n'
                 '               FifoData_Wr  <= \'1\';\n'
                 '               FifoData_Din <= save_set_data;\n'
                 '            end if;\n\n' + anchor, 'cd_top data fifo copy')


SPU_KEEP_WRITES = {2, 3, 4, 5, 6, 37, 43}   # plus the IRQ9 line of 40
SPU_KEEP_READS = {2, 3, 4, 5, 6}          # ENDX and STAT come from guest reads


def spu_tap_text(text):
    """spu.vhd: a quiet point and the upstream savestate port, trimmed.

    save_spu_hold stops the sample counter at 512 (the value a MiSTer load
    sets) once the write FIFO is empty, so no new sample starts; save_spu_quiet reports the voice loop
    idle with nothing stashed and the write FIFO empty. The upstream SS_*
    port then reads and writes the state no register gives back: capture
    and transfer positions, the reverb pointer, noise, ENDX, IRQ9, the
    current main volume, current voice volumes, envelope counters and voice
    records. Everything a guest write restores (voice registers, globals,
    reverb registers, which also feed the reverb table) is left to the save
    routine's bus writes, so the other SS register writes and readbacks are
    removed to keep the area small. While held, voice register writes do
    not flag an ADSR reset or a repeat-address write (the restored records
    carry that state).
    While quiet, the voice-record readback uses the engine's bank (the
    voice loop is idle), so the guest copy of the records stays unused.

    The build ties the savestate register port off (psx_top_spu_text): the
    save routine only reads and writes guest registers (owner, 2026-11-04:
    a brief sound glitch on load is fine). For its re-key on load, a held
    SPU reads 0x1F801DBC/E as the voices in ADSR release (save_released,
    set with each voice record); unheld they read all ones as before. The
    benches still use the port to check the restore.
    """
    if 'save_spu_hold' in text:
        raise ValueError('save states: spu already has the tap')
    port = '      SS_idle              : out std_logic;\n'
    text = _once(text, port, port + "      save_spu_hold        : in  std_logic := '0';\n"
                 "      save_spu_quiet       : out std_logic := '0';\n", 'spu ports')
    # The write FIFO (CPU or DMA data for SPU RAM) only drains inside the
    # voice loop, so the counter stops only with it empty: otherwise the next
    # sample runs and drains it (Ape Escape EU never got quiet, 0.5.5-test18).
    text = _once(text, '            if (sampleticks < 767) then\n',
                 "            if (save_spu_hold = '1' and sampleticks = 512 and FifoIn_Empty = '1') then\n"
                 '               null;   -- save states: no new sample\n'
                 '            elsif (sampleticks < 767) then\n', 'spu sample counter')
    text = _once(text, "         SS_idle <= '0';\n",
                 "         SS_idle <= '0';\n"
                 "         save_spu_quiet <= '0';\n"
                 "         if (save_spu_hold = '1' and sampleticks = 512 and state = IDLE and stashedSamples = 0 and FifoIn_Empty = '1') then\n"
                 "            save_spu_quiet <= '1';\n"
                 '         end if;\n', 'spu idle process')
    text = _once(text, '   RamVoiceRecord_addrB(0)  <= to_unsigned(index, 5);\n',
                 "   RamVoiceRecord_addrB(0)  <= SS_Adr(6 downto 2) when (save_spu_quiet = '1') else to_unsigned(index, 5);\n",
                 'spu record engine address')
    # voice registers: restored by guest writes, which must not flag
    text = _once(text, "   RamVoice_addrB(1) <= (SS_Adr(7 downto 0) - 64)         when (SS_rden = '1')            else \n",
                 '   RamVoice_addrB(1) <= ', 'spu voice register SS read address')
    text = _sub_once(text, r'\n[ \t]*if \(SS_Adr >= 64 and SS_Adr < 256\) then\n(?:[^\n]*\n){3}[ \t]*end if;\n', lambda m: '\n',
                     'spu voice register SS write')
    text = _sub_once(text, r'\n[ \t]*elsif \(SS_Adr >= 64 and SS_Adr < 256\) then\n[ \t]*SS_DataRead <= x"0000" & RamVoice_dataB\(1\);[^\n]*\n',
                     lambda m: '\n', 'spu voice register SS readback')
    for flag in ('voice_adsrWrite', 'voice_repeatWrite'):
        text = _sub_once(text, r'(=> )(' + flag + r'\(to_integer\(bus_addr\(8 downto 4\)\)\)) *<= \'1\';',
                         lambda m: m.group(1) + f"if (save_spu_hold = '0') then {m.group(2)} <= '1'; end if;", 'spu ' + flag)
    # the voices in ADSR release, for the save routine's re-key on load
    text = _once(text, "   signal voice_repeatWrite : std_logic_vector(23 downto 0) := (others => '0');\n",
                 "   signal voice_repeatWrite : std_logic_vector(23 downto 0) := (others => '0');\n"
                 "   signal save_released     : std_logic_vector(23 downto 0) := (others => '0');   -- save states: ADSR release\n",
                 'spu release mask signal')
    text = _once(text, '                  RamVoiceRecord_dataA( 76 downto  74) <= std_logic_vector(voice.adsrphase);\n',
                 '                  RamVoiceRecord_dataA( 76 downto  74) <= std_logic_vector(voice.adsrphase);\n'
                 '                  save_released(index) <= voice.adsrphase(2);   -- save states: RELEASE = "100"\n',
                 'spu release mask')
    text = _once(text, '                     when 16#1BA# => bus_dataRead <= CURVOL_R;\n',
                 '                     when 16#1BA# => bus_dataRead <= CURVOL_R;\n'
                 "                     when 16#1BC# => if (save_spu_hold = '1') then bus_dataRead <= save_released(15 downto 0); end if;\n"
                 "                     when 16#1BE# => if (save_spu_hold = '1') then bus_dataRead <= x\"00\" & save_released(23 downto 16); end if;\n",
                 'spu release mask read')
    text, n = re.subn(r'(SS_DataRead <= [^;]*?)RamVoiceRecord_dataB\(1\)', r'\1RamVoiceRecord_dataB(0)', text)
    if n != 4:
        raise ValueError(f'save states: spu record readback found {n} times')

    def write_line(m):
        number, name = int(m.group(2)), m.group(3)
        return m.group(0) if number in SPU_KEEP_WRITES or (number == 40 and name == 'IRQ9') else ''
    text, n = re.subn(r'^([ \t]*)if \(to_integer\(SS_Adr\) = *(\d+)\) then (\w+) +<= [^\n]*end if;[ \t]*\n', write_line, text, flags=re.M)
    if n != 60:
        raise ValueError(f'save states: spu SS register writes found {n} times')

    def read_line(m):
        number = int(m.group(1))
        return m.group(0) if number in SPU_KEEP_READS else ''
    text, n = re.subn(r'^[ \t]*ss_out\((\d+)\)(\([^)]*\))? *<= [^\n]*;[^\n]*\n', read_line, text, flags=re.M)
    if n != 61:
        raise ValueError(f'save states: spu ss_out lines found {n} times')
    # Simulation-only guards of the area variants (savestate port unused).
    for guard in (r"   -- synthesis translate_off\n   assert SS_rden = '0'\n[^\n]*\n   -- synthesis translate_on\n",
                  r"   -- synthesis translate_off\n   process \(clk1x\)\n   begin\n      if rising_edge\(clk1x\) then\n"
                  r"         assert SS_wren = '0' and SS_rden = '0' and loading_savestate = '0'\n[^\n]*\n"
                  r"      end if;\n   end process;\n   -- synthesis translate_on\n"):
        text = re.sub(guard, '', text)
    if re.search(r"assert SS_(?:wren|rden) = '0'", text):
        raise ValueError('save states: spu keeps a savestate guard')
    return text


# The build's SPU port is off: the record engine address needs no port select.
SPU_RECORD_ADDR = ("   RamVoiceRecord_addrB(0)  <= SS_Adr(6 downto 2) when (save_spu_quiet = '1') else to_unsigned(index, 5);\n",
                   '   RamVoiceRecord_addrB(0)  <= to_unsigned(index, 5);\n')


def spu_build_text(text):
    """The build's SPU: the tap with the savestate port off (no record
    engine address select, and held voice register writes flag ADSR and
    repeat writes as unheld ones do: nothing restores the records, and
    the load's key-on clears both flags)."""
    text = _once(spu_tap_text(text), *SPU_RECORD_ADDR, 'spu record engine address (port off)')
    for flag in ('voice_adsrWrite', 'voice_repeatWrite'):
        text = _sub_once(text, r"if \(save_spu_hold = '0'\) then (" + flag + r"\(to_integer\(bus_addr\(8 downto 4\)\)\) <= '1';) end if;",
                         lambda m: m.group(1), 'spu build ' + flag)
    return text


SAVE_PORTS_VHDL = ('save_trap : in std_logic := \'0\';\n'
                   '      save_mode : in std_logic := \'0\';\n'
                   '      save_cd_freeze : in std_logic := \'0\';\n'
                   '      save_exp1_addr : out unsigned(12 downto 0);\n'
                   '      save_exp1_write : out std_logic;\n'
                   '      save_exp1_dataWrite : out std_logic_vector(7 downto 0);\n'
                   '      save_exp1_dataRead : in std_logic_vector(7 downto 0) := (others => \'0\');\n'
                   '      save_set_sel : in std_logic_vector(2 downto 0) := (others => \'0\');\n'
                   '      save_set_data : in std_logic_vector(7 downto 0) := (others => \'0\');\n'
                   '      save_timer_flags : out std_logic_vector(11 downto 0);\n'
                   '      save_gpu_ranges : out std_logic_vector(62 downto 0);\n'
                   '      save_cd_state : out std_logic_vector(63 downto 0);\n'
                   '      save_spu_hold : in std_logic := \'0\';\n'
                   '      save_spu_quiet : out std_logic;\n'
                   '      save_spu_adr : in unsigned(8 downto 0) := (others => \'0\');\n'
                   '      save_spu_wdata : in std_logic_vector(31 downto 0) := (others => \'0\');\n'
                   '      save_spu_rden : in std_logic := \'0\';\n'
                   '      save_spu_wren : in std_logic := \'0\';\n'
                   '      save_spu_rdata : out std_logic_vector(31 downto 0);\n'
                   '      save_spuram_req : in std_logic := \'0\';\n'
                   '      save_spuram_rnw : in std_logic := \'1\';\n'
                   '      save_spuram_adr : in std_logic_vector(18 downto 0) := (others => \'0\');\n'
                   '      save_spuram_wdata : in std_logic_vector(15 downto 0) := (others => \'0\');\n'
                   '      save_spuram_rdata : out std_logic_vector(15 downto 0);\n'
                   '      save_spuram_done : out std_logic')

SPU_TOP_SIGNALS = '''   -- save states: the SPU savestate and RAM ports from the save routine while the SPU is held
   signal save_spu_ss_adr    : unsigned(8 downto 0);
   signal save_spu_ss_wdata  : std_logic_vector(31 downto 0);
   signal save_spu_ss_wren   : std_logic;
   signal save_spu_ss_rden   : std_logic;
   signal save_spu_ram_wdata : std_logic_vector(15 downto 0);
   signal save_spu_ram_adr   : std_logic_vector(18 downto 0);
   signal save_spu_ram_req   : std_logic;
   signal save_spu_ram_rnw   : std_logic;
'''
SPU_TOP_LOGIC = '''
   -- (the reset-only sequencer never strobes the SPU's register port; the
   -- save routine restores the SPU by guest writes only, so it is off)
   save_spu_ss_adr    <= (others => '0');
   save_spu_ss_wdata  <= (others => '0');
   save_spu_ss_wren   <= '0';
   save_spu_ss_rden   <= '0';
   save_spu_ram_wdata <= save_spuram_wdata  when (save_spu_hold = '1') else SS_SPURAM_dataWrite;
   save_spu_ram_adr   <= save_spuram_adr    when (save_spu_hold = '1') else SS_SPURAM_Adr;
   save_spu_ram_req   <= save_spuram_req    when (save_spu_hold = '1') else SS_SPURAM_request;
   save_spu_ram_rnw   <= save_spuram_rnw    when (save_spu_hold = '1') else SS_SPURAM_rnw;
   save_spu_rdata     <= (others => '0');
   save_spuram_rdata  <= SS_SPURAM_dataRead;
   save_spuram_done   <= SS_SPURAM_done;
'''


def psx_top_spu_text(text):
    """psx_top: the SPU instance takes its savestate register port from the
    save routine (the reset-only sequencer only strobes the scratchpad and
    main RAM; its SPU guard asserts so), and its RAM port from the save
    routine while save_spu_hold is 1 (the reset sequencer clears SPU RAM
    through it otherwise)."""
    at = text.index('ispu : entity work.spu')
    end = text.index(');', at)
    block = text[at:end]
    for port, old, new in (('SS_DataWrite', 'SS_DataWrite', 'save_spu_ss_wdata'), ('SS_Adr', r'SS_Adr\(8 downto 0\)', 'save_spu_ss_adr'),
                           ('SS_wren', r'SS_wren\(9\)', 'save_spu_ss_wren'), ('SS_rden', r'SS_rden\(9\)', 'save_spu_ss_rden'),
                           ('SS_RAM_dataWrite', 'SS_SPURAM_dataWrite', 'save_spu_ram_wdata'), ('SS_RAM_Adr', 'SS_SPURAM_Adr', 'save_spu_ram_adr'),
                           ('SS_RAM_request', 'SS_SPURAM_request', 'save_spu_ram_req'), ('SS_RAM_rnw', 'SS_SPURAM_rnw', 'save_spu_ram_rnw')):
        block = _sub_once(block, r'^([ \t]*' + port + r'[ \t]*=> )' + old + r'(?=[,\s])', lambda m: m.group(1) + new, 'spu map ' + port)
    block = _sub_once(block, r'^([ \t]*)SS_idle([ \t]*)=> SS_idle_spu,[ \t]*\n',
                      lambda m: m.group(0) + f'{m.group(1)}save_spu_hold        => save_spu_hold,\n'
                                             f'{m.group(1)}save_spu_quiet       => save_spu_quiet,\n', 'spu map SS_idle')
    text = text[:at] + block + text[end:]
    return _sub_once(text, r'^begin[ \t]*\n', lambda m: SPU_TOP_SIGNALS + m.group(0) + SPU_TOP_LOGIC, 'psx_top architecture begin')


def psx_top_text(text):
    """psx_top: the trap input to the CPU and EXP1 bus ports out of the top."""
    if 'save_trap' in text:
        raise ValueError('save states: psx_top already has save_trap')
    text = _sub_once(text, r'(\S)([ \t]*(?:--[^\n]*)?\n[ \t]*\);\nend entity;)',
                     lambda m: m.group(1) + ';\n      ' + SAVE_PORTS_VHDL + m.group(2), 'psx_top entity end')
    text = _sub_once(text, r'^([ \t]*)--bus_exp1_addr([ \t]*)=> bus_exp1_addr,[ \t]*\n'
                           r'[ \t]*--bus_exp1_dataWrite[ \t]*=> bus_exp1_dataWrite,[ \t]*\n'
                           r'([ \t]*bus_exp1_read[ \t]*=> bus_exp1_read,[ \t]*\n)'
                           r'[ \t]*--bus_exp1_write[ \t]*=> bus_exp1_write,[ \t]*\n'
                           r'[ \t]*bus_exp1_dataRead[ \t]*=> bus_exp1_dataRead,',
                     lambda m: (f'{m.group(1)}bus_exp1_addr        => save_exp1_addr,\n'
                                f'{m.group(1)}bus_exp1_dataWrite   => save_exp1_dataWrite,\n' + m.group(3) +
                                f'{m.group(1)}bus_exp1_write       => save_exp1_write,\n'
                                f'{m.group(1)}bus_exp1_dataRead    => save_exp1_dataRead,'),
                     'memorymux EXP1 map')
    text = _sub_once(text, r'(icpu : entity work\.cpu\s*port map\s*\([^;]*?\n([ \t]*)irqRequest[ \t]*=> irqRequest,)',
                     lambda m: m.group(1) + f'\n{m.group(2)}trapRequest       => save_trap,', 'CPU irqRequest map')
    for unit, maps in (('irq', ('save_set_sel', 'save_set_data')), ('dma', ('save_set_sel', 'save_set_data')),
                       ('tmr', ('save_set_sel', 'save_set_data', 'save_timer_flags')), ('gpu', ('save_gpu_ranges',)),
                       ('cd', ('save_cd_state', 'save_set_sel', 'save_set_data'))):
        text = _sub_once(text, r'^([ \t]*)bus_dataRead([ \t]*)=> bus_' + unit + r'_dataRead,[ \t]*\n',
                         lambda m: m.group(0) + ''.join(f'{m.group(1)}{name} => {name},\n' for name in maps),
                         unit + ' bus map')
    # save mode (psx_save_exp1 SAVE_MODE): the CPU's main RAM bits 22:21 reach
    # the SDRAM above main RAM, where the save routine's stage 2 lives (mirrors
    # of main RAM otherwise; DMA addresses are unchanged)
    text = _once(text, '                    ram_cpu_Adr(24 downto 23) & "00" & ram_cpu_Adr(20 downto 0);\n',
                 '                    ram_cpu_Adr(24 downto 23) & (ram_cpu_Adr(22 downto 21) and (save_mode & save_mode)) & ram_cpu_Adr(20 downto 0);\n',
                 'psx_top main RAM address')
    # (save_mode is a clk1x register of psx_save_exp1: these bits stay clk1x-timed)
    # CD freeze (psx_save_exp1 CD_FREEZE): cd_top's clock enable off while the
    # save routine copies, so no sector arrives that nobody reads
    text = _sub_once(text, r'(icd_top : entity work\.cd_top\s*port map\s*\(\s*clk1x\s*=> clk1x,\s*\n\s*ce\s*=> )ce,',
                     lambda m: m.group(1) + 'save_cd_ce,', 'cd_top ce map')
    text = _sub_once(text, r'^begin[ \t]*\n', lambda m: '   signal save_cd_ce : std_logic;\n' + m.group(0) +
                     "\n   save_cd_ce <= ce and not save_cd_freeze;\n", 'psx_top architecture begin (cd ce)')
    return psx_top_spu_text(text)


def resource_wrapper_text(text):
    """psx_mister_psx_top_resource (the generated psx_top wrapper): pass the ports through."""
    if 'save_trap' in text:
        raise ValueError('save states: wrapper already has save_trap')
    ports = ('    save_trap : in std_logic;\n    save_mode : in std_logic;\n    save_cd_freeze : in std_logic;\n    save_exp1_addr : out unsigned(12 downto 0);\n'
             '    save_exp1_write : out std_logic;\n    save_exp1_dataWrite : out std_logic_vector(7 downto 0);\n'
             '    save_exp1_dataRead : in std_logic_vector(7 downto 0);\n    save_set_sel : in std_logic_vector(2 downto 0);\n'
             '    save_set_data : in std_logic_vector(7 downto 0);\n    save_timer_flags : out std_logic_vector(11 downto 0);\n'
             '    save_gpu_ranges : out std_logic_vector(62 downto 0);\n'
             '    save_cd_state : out std_logic_vector(63 downto 0);\n'
             '    save_spu_hold : in std_logic;\n    save_spu_quiet : out std_logic;\n'
             '    save_spu_adr : in unsigned(8 downto 0);\n    save_spu_wdata : in std_logic_vector(31 downto 0);\n'
             '    save_spu_rden : in std_logic;\n    save_spu_wren : in std_logic;\n'
             '    save_spu_rdata : out std_logic_vector(31 downto 0);\n    save_spuram_req : in std_logic;\n'
             '    save_spuram_rnw : in std_logic;\n    save_spuram_adr : in std_logic_vector(18 downto 0);\n'
             '    save_spuram_wdata : in std_logic_vector(15 downto 0);\n    save_spuram_rdata : out std_logic_vector(15 downto 0);\n'
             '    save_spuram_done : out std_logic')
    text = _sub_once(text, r'(\S)(\n\);\nend entity;)', lambda m: m.group(1) + ';\n' + ports + m.group(2), 'wrapper entity end')
    maps = ('        save_trap => save_trap,\n        save_mode => save_mode,\n        save_cd_freeze => save_cd_freeze,\n        save_exp1_addr => save_exp1_addr,\n'
            '        save_exp1_write => save_exp1_write,\n        save_exp1_dataWrite => save_exp1_dataWrite,\n'
            '        save_exp1_dataRead => save_exp1_dataRead,\n        save_set_sel => save_set_sel,\n'
            '        save_set_data => save_set_data,\n        save_timer_flags => save_timer_flags,\n'
            '        save_gpu_ranges => save_gpu_ranges,\n        save_cd_state => save_cd_state,\n' +
            ',\n'.join(f'        {n} => {n}' for n in SPU_SIGNALS))
    text = _sub_once(text, r'(\S)(\n    \);\nend architecture;)', lambda m: m.group(1) + ',\n' + maps + m.group(2), 'wrapper port map end')
    return text


SPU_SIGNALS = ('save_spu_hold', 'save_spu_quiet', 'save_spu_adr', 'save_spu_wdata', 'save_spu_rden', 'save_spu_wren',
               'save_spu_rdata', 'save_spuram_req', 'save_spuram_rnw', 'save_spuram_adr', 'save_spuram_wdata',
               'save_spuram_rdata', 'save_spuram_done')

SAVE_BLOCK = '''    // Save states (prepare_save_states.py): EXP1 device + APF save/load handshake.
    // The copy engine sits in front of the card slot on psx_vram_psram's aux port.
    wire save_trap, save_mode, save_cd_freeze, save_exp1_write, ss_aux_req, ss_aux_we, ss_aux_done;
    wire [1:0] save_disc;   // the disc in the drive - 1, assigned at the end of the module
    wire [12:0] save_exp1_addr; wire [7:0] save_exp1_dataWrite, save_exp1_dataRead;
    wire [2:0] save_set_sel; wire [7:0] save_set_data; wire [11:0] save_timer_flags;
    wire [62:0] save_gpu_ranges; wire [63:0] save_cd_state;
    wire save_spu_hold, save_spu_quiet, save_spu_rden, save_spu_wren, save_spuram_req, save_spuram_rnw, save_spuram_done;
    wire [8:0] save_spu_adr; wire [31:0] save_spu_wdata, save_spu_rdata;
    wire [18:0] save_spuram_adr; wire [15:0] save_spuram_wdata, save_spuram_rdata;
    wire [20:0] ss_aux_addr; wire [31:0] ss_aux_din, ss_aux_dout;
    wire ss_supported, ss_start_ack, ss_start_busy, ss_start_ok, ss_start_err;
    wire ss_load_ack, ss_load_busy, ss_load_ok, ss_load_err, ss_start, ss_load;
    wire [31:0] ss_addr, ss_size, ss_maxloadsize;
    psx_save_exp1 #(.ROM_HEX("{rom}"), .SAVE_BASE(32'h3000_0000), .SAVE_SIZE(32'h0040_0000)) save_exp1 (
        .clk2x(clk2x), .reset_2x(mc_reset2x[1]),
        .bus_addr(save_exp1_addr), .bus_write(save_exp1_write), .bus_dataWrite(save_exp1_dataWrite),
        .bus_dataRead(save_exp1_dataRead), .trap(save_trap), .save_mode(save_mode), .cd_freeze(save_cd_freeze),
        .set_sel(save_set_sel), .set_data(save_set_data),
        .timer_flags(save_timer_flags), .gpu_ranges(save_gpu_ranges),
        .clk1x(clk1x), .spu_hold(save_spu_hold), .spu_quiet(save_spu_quiet), .spu_ss_adr(save_spu_adr),
        .spu_ss_wdata(save_spu_wdata), .spu_ss_rden(save_spu_rden), .spu_ss_wren(save_spu_wren), .spu_ss_rdata(save_spu_rdata),
        .spuram_req(save_spuram_req), .spuram_rnw(save_spuram_rnw), .spuram_adr(save_spuram_adr),
        .spuram_wdata(save_spuram_wdata), .spuram_rdata(save_spuram_rdata), .spuram_done(save_spuram_done),
        .aux_req(ss_aux_req), .aux_we(ss_aux_we), .aux_addr(ss_aux_addr), .aux_din(ss_aux_din),
        .aux_dout(ss_aux_dout), .aux_done(ss_aux_done),
        .up_req(mc_aux_req), .up_we(mc_aux_we), .up_addr(mc_aux_addr), .up_din(mc_aux_din),
        .up_dout(mc_aux_dout), .up_done(mc_aux_done),
        .clk_74a(clk_74a), .savestate_supported(ss_supported), .savestate_addr(ss_addr),
        .savestate_size(ss_size), .savestate_maxloadsize(ss_maxloadsize),
        .savestate_start(ss_start), .savestate_start_ack(ss_start_ack), .savestate_start_busy(ss_start_busy),
        .savestate_start_ok(ss_start_ok), .savestate_start_err(ss_start_err),
        .savestate_load(ss_load), .savestate_load_ack(ss_load_ack), .savestate_load_busy(ss_load_busy),
        .savestate_load_ok(ss_load_ok), .savestate_load_err(ss_load_err), .disc(save_disc), .core_held(!reset_n),
        .cd_state(save_cd_state));
'''

CMD_MAP = {'savestate_supported': 'ss_supported', 'savestate_addr': 'ss_addr', 'savestate_size': 'ss_size',
           'savestate_maxloadsize': 'ss_maxloadsize', 'savestate_start': 'ss_start', 'savestate_start_ack': 'ss_start_ack',
           'savestate_start_busy': 'ss_start_busy', 'savestate_start_ok': 'ss_start_ok',
           'savestate_start_err': 'ss_start_err', 'savestate_load': 'ss_load', 'savestate_load_ack': 'ss_load_ack',
           'savestate_load_busy': 'ss_load_busy', 'savestate_load_ok': 'ss_load_ok', 'savestate_load_err': 'ss_load_err'}


def rom2_hex(rom_hex):
    """The second ROM's hex next to the first (build_save_fw.py writes both)."""
    from pathlib import Path
    path = Path(rom_hex)
    return path.with_name(path.stem + '2' + path.suffix)


def core_top_text(text, rom_hex, save_size=0x400000):
    if 'psx_save_exp1' in text:
        raise ValueError('save states: core_top already has psx_save_exp1')
    # --vram-full-rate gives the instance parameters: SAVE_AUX goes first
    found = re.findall(r'^    psx_vram_psram (?:#\((.*)\) )?vram \(', text, re.M)
    if len(found) != 1:
        raise ValueError('save states: VRAM instance anchor found %d times' % len(found))
    params = found[0]
    head = f'    psx_vram_psram #({params}) vram (' if params else '    psx_vram_psram vram ('
    at = text.index(head)
    vram = text[at:text.index(');', at)]
    x3 = '    psx_vram_x3 vram_x3 ('
    if x3 in text:
        # --vram-x3: the card slot's aux port reaches the controller through the
        # clk2x side of psx_vram_x3; aux_dout comes straight from the controller
        vram = _once(vram, '.aux_dout(mc_aux_dout),', '.aux_dout(ss_aux_dout),', 'VRAM aux_dout')
        b = text.index(x3); line = text[b:text.index(');', b)]
        new_line = line
        for port in ('aux_req', 'aux_we', 'aux_addr', 'aux_din', 'aux_done'):
            new_line = _once(new_line, f'.{port}(mc_{port}),', f'.{port}(ss_{port}),', f'VRAM x3 bridge {port}')
    else:
        vram = _once(vram, '.aux_req(mc_aux_req),.aux_we(mc_aux_we),.aux_addr(mc_aux_addr),.aux_din(mc_aux_din),.aux_dout(mc_aux_dout),\n'
                           '        .aux_done(mc_aux_done),',
                     '.aux_req(ss_aux_req),.aux_we(ss_aux_we),.aux_addr(ss_aux_addr),.aux_din(ss_aux_din),.aux_dout(ss_aux_dout),\n'
                     '        .aux_done(ss_aux_done),', 'VRAM aux port')
    text = text[:at] + vram + text[at + len(vram):]
    if x3 in text:
        text = _once(text, line, new_line, 'VRAM x3 bridge')
    block = SAVE_BLOCK.replace('{rom}', str(rom_hex))
    if save_size != 0x400000:
        block = block.replace(".SAVE_SIZE(32'h0040_0000)", f".SAVE_SIZE(32'h{save_size:08X})")
    text = _once(text, head, f'    psx_vram_psram #(.SAVE_AUX(1){", " + params if params else ""}) vram (', 'VRAM instance')
    # the save block's nets are declared before their first use (the x3 bridge comes first)
    first = '    // --vram-x3: controller on clk3x' if x3 in text else f'    psx_vram_psram #(.SAVE_AUX(1)'
    text = _once(text, first, block + first, 'save block position')
    # the disc in the drive: psx_disc_menu/psx_disc_select's first slot 101/201/301/401
    disc = '{swap_slot_id[8], swap_slot_id[7]}' if 'wire [15:0] swap_slot_id;' in text else "2'd0"
    if text.count('\nendmodule') < 1:
        raise ValueError('save states: core_top endmodule not found')
    at = text.rindex('\nendmodule')
    text = text[:at] + f'\n    assign save_disc = {disc};   // prepare_save_states.py' + text[at:]
    text = _once(text, '    psx_memcard_slot memcard_slot (', '    psx_memcard_slot #(.SAVE_BASE(32\'h3000_0000)) memcard_slot (',
                 'card slot instance')
    mux = "        if (bridge_addr[31:17]==15'h1000) bridge_rd_data=memcard_rd_data;"
    text = _once(text, mux, mux + "\n        if (bridge_addr[31:22]==10'h0C0) bridge_rd_data=memcard_rd_data;   // save states 0x30000000",
                 'bridge read mux')
    text = _sub_once(text, r'(psx_mister_psx_top_resource\s+psx\s*\()',
                     lambda m: m.group(1) + '.save_trap(save_trap),.save_mode(save_mode),.save_cd_freeze(save_cd_freeze),.save_exp1_addr(save_exp1_addr),.save_exp1_write(save_exp1_write),'
                                           '.save_exp1_dataWrite(save_exp1_dataWrite),.save_exp1_dataRead(save_exp1_dataRead),'
                                           '.save_set_sel(save_set_sel),.save_set_data(save_set_data),'
                                           '.save_timer_flags(save_timer_flags),.save_gpu_ranges(save_gpu_ranges),'
                                           '.save_cd_state(save_cd_state),'
                                           + ''.join(f'.{n}({n}),' for n in SPU_SIGNALS),
                     'psx instance')
    start = text.index('core_bridge_cmd')
    end = text.index(');', start)
    block = text[start:end]
    for port, signal in CMD_MAP.items():
        block, count = re.subn(r'\.' + port + r'\s*\([^()]*\)', f'.{port}({signal})', block)
        if count != 1:
            raise ValueError(f'save states: core_bridge_cmd .{port} binding found {count} times')
    return text[:start] + block + text[end:]


SDC = '''
# psx_save_exp1 (prepare_save_states.py): toggle synchroniser first stages,
# and the request kind / result bit, held from before their toggle until the
# far side has seen it.
set_false_path -to [get_registers {*|psx_save_exp1:save_exp1|s74_fin[0] *|psx_save_exp1:save_exp1|s2x_req[0]}]
set_max_delay 13 -from [get_registers {*|psx_save_exp1:save_exp1|req_load_74 *|psx_save_exp1:save_exp1|fin_ok_2x *|psx_save_exp1:save_exp1|size_pages[*]}]
set_min_delay -13 -from [get_registers {*|psx_save_exp1:save_exp1|req_load_74 *|psx_save_exp1:save_exp1|fin_ok_2x *|psx_save_exp1:save_exp1|size_pages[*]}]
# The copy engine's aux word into psx_vram_x3, as the card slot's (pocket.sdc):
# aux_we/addr/din are set a clk2x or more before own_req rises (cp_word moves
# only with own_req low, the page word cp_q is valid from the edge own_req
# rises on) and held until aux_done; c_aux_* load only after the request has
# crossed into the memory clock. get_keepers: the page RAM's port registers.
set_max_delay 13 -from [get_keepers {*|psx_save_exp1:save_exp1|*}] -to [get_registers {*|psx_vram_x3:vram_x3|c_aux_we *|psx_vram_x3:vram_x3|c_aux_addr[*] *|psx_vram_x3:vram_x3|c_aux_din[*]}]
set_min_delay -13 -from [get_keepers {*|psx_save_exp1:save_exp1|*}] -to [get_registers {*|psx_vram_x3:vram_x3|c_aux_we *|psx_vram_x3:vram_x3|c_aux_addr[*] *|psx_vram_x3:vram_x3|c_aux_din[*]}]
'''


def prepare_project(directory, rom_hex, save_size=0x400000):
    """Apply every save-state transform to a prepared project (run after the
    DualShock fit helper, which checks the CPU source's hash)."""
    from pathlib import Path
    from prepare_pocket_config_cdc import assignments
    directory = Path(directory).resolve()
    if any((directory / name).exists() for name in ('db', 'incremental_db', 'output_files', 'save-states')):
        raise ValueError('save states must precede map/fit, once')
    qsf = directory / 'apf_top.qsf'
    qtext = qsf.read_text()
    selected = assignments(directory, qtext)

    def one(name):
        paths = {p for _, p in selected if p.name == name}
        if len(paths) != 1:
            raise ValueError('save states: expected one selected ' + name)
        return paths.pop()
    work = directory / 'save-states'
    work.mkdir()
    # both handler ROMs in psx_save_exp1's one 1024 x 40 ROM
    from build_save_fw import rom40_from_byte_hex
    (work / 'psx_save_rom40.hex').write_text(rom40_from_byte_hex(rom_hex, rom2_hex(rom_hex)))
    changes = {'cpu.vhd': cpu_trap_text, 'memorymux.vhd': memorymux_exp1_text, 'psx_top.vhd': psx_top_text,
               'irq.vhd': irq_set_text, 'dma.vhd': dma_set_text, 'timer.vhd': timer_tap_text, 'gpu.vhd': gpu_tap_text,
               'spu.vhd': spu_build_text,
               'cd_top.vhd': cd_tap_text,
               'psx_mister_psx_top_resource.vhd': resource_wrapper_text,
               'core_top.sv': lambda t: core_top_text(t, work / 'psx_save_rom40.hex', save_size)}
    for name, transform in changes.items():
        source = one(name)
        out = work / name
        out.write_text(transform(source.read_text()))
        replaced = False
        for spelled in ('"' + str(source) + '"', str(source), '"' + str(source.relative_to(directory)) + '"'
                        if source.is_relative_to(directory) else None,
                        str(source.relative_to(directory)) if source.is_relative_to(directory) else None):
            if spelled and re.search(r' ' + re.escape(spelled) + r'(\s|$)', qtext):
                qtext = re.sub(r' ' + re.escape(spelled) + r'(\s|$)', lambda m: ' "' + str(out) + '"' + m.group(1), qtext, count=1)
                replaced = True
                break
        if not replaced:
            raise ValueError('save states: qsf assignment not found for ' + name)
    root = Path(__file__).resolve().parents[1]
    qtext += f'set_global_assignment -name SYSTEMVERILOG_FILE "{root}/rtl/pocket/psx_save_exp1.sv" -library work\n'
    qsf.write_text(qtext)
    sdc = directory / 'pocket.sdc'
    sdc.write_text(sdc.read_text() + SDC)
    return {'files': sorted(changes), 'rom': str(rom_hex)}


if __name__ == '__main__':
    import argparse
    import json
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('project', type=Path, help='prepared project (after prepare_pocket_dualshock_fit.py)')
    parser.add_argument('--rom', type=Path, default=Path(__file__).resolve().parents[1] / 'rtl/pocket/psx_save_fw.hex')
    parser.add_argument('--save-size', type=lambda s: int(s, 0), default=0x400000,
                        help='diagnosis only: the state size the Pocket reads back (default 4 MiB; smaller states are incomplete)')
    args = parser.parse_args()
    print(json.dumps(prepare_project(args.project, args.rom.resolve(), args.save_size), indent=2))
