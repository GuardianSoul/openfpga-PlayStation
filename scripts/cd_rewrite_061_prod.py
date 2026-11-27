#!/usr/bin/env python3
"""cd-rewrite-061 product transform: cd_top's command, drive and motor processes replaced by cd_subcpu firmware.

transform(text, fw_hex=None) -> text.  Also writes the firmware port map (rtl/cd_fw/cd_ports.inc) when run as a script:
  cd_rewrite_061_prod.py IN_cd_top.vhd OUT_cd_top.vhd [--fw FW.hex] [--inc rtl/cd_fw/cd_ports.inc]

What stays hardware in the glue (guest-visible on today's clock; docs/cd-rewrite-061-port.md):
  - cmdPending / cmd_busy set on beginCommand (Reel Fishing exception included), the ACK age counter
  - parameter FIFO count and the Setloc BCD check per byte (fifoParamCount, setLocParamBad)
  - response pipelines: status byte after cmdAck / ackRead_valid (GetStat modifiers from fw registers), drive acks,
    the two-byte error response with errorResponseNext_new, ackPendingIRQ -> pendingDriveResponse,
    readSectorPointer <= writeSectorPointer on a published data IRQ
  - internalStatus_1 / newCD_1 delays, subdata <= nextSubdata on a copy strobe, driveDelay = DRIVE timer
Everything else in S is an OUT port the firmware writes.
"""
import math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cd_rewrite_061_gen as gen

SUBCPU = os.path.join(HERE, '..', 'rtl', 'cd', 'cd_subcpu.vhd')
FIFO_MLAB = os.path.join(HERE, '..', 'rtl', 'cd', 'cd_fifo_mlab.vhd')
PM_ABITS = 12

# commit strobe bits (cd_subcpu strobe vector); also writable at once through OUT 113
STROBES = ['cmdAck', 'cmdIRQ', 'driveAck', 'getIDAck', 'ackDrive', 'ackDriveEnd', 'ackRead', 'errCmd',
           'errDrive', 'softReset', 'startReading', 'startPlaying', 'processDataSector', 'processCDDASector',
           'readOnDisk', 'copySubdata']
# level flags (cd_subcpu flags vector)
FLAGS = ['cmd_busy', 'cmdPending', 'working']
# S signals the glue drives itself
GLUE = {'FifoResponse_Din', 'FifoResponse_Wr', 'FifoResponse_reset', 'errorResponseNext_new', 'ackPendingIRQNext',
        'internalStatus_1', 'newCD_1', 'subdata', 'driveDelay', 'readSectorPointer'}
# one-clock pulses written by OUT (auto-clear)
PULSE = re.compile(r'^(FifoParam_Rd|FifoParam_reset|ClearXACurrentSet|cmdResetXa|clearSectorBuffers|handleDrive|'
                   r'processSeekHeader|triggerUpdateSubchannel|seekOnDiskDrive|pausePositionLookup)$')
# live response byte sources (streamer source codes; 0 = literal)
SRC = ['0'] + [f'header({8*i+7} downto {8*i})' for i in range(4)] + [f'subheader({8*i+7} downto {8*i})' for i in range(4)] + \
      ['trackcountBCD', 'totalMinutesBCD', 'totalSecondsBCD', 'minutesBCD', 'secondsBCD',
       'std_logic_vector(peakvolumeR(7 downto 0))', "'1' & std_logic_vector(peakvolumeR(14 downto 8))",
       'std_logic_vector(peakvolumeL(7 downto 0))', "'0' & std_logic_vector(peakvolumeL(14 downto 8))"]
SRC_NAMES = ['LIT'] + [f'HDR{i}' for i in range(4)] + [f'SUBH{i}' for i in range(4)] + \
            ['TRACKCNT', 'TOTMIN', 'TOTSEC', 'TRKMIN', 'TRKSEC', 'PEAKR_LO', 'PEAKR_HI', 'PEAKL_LO', 'PEAKL_HI']
# firmware-owned values (status, mode, filter, subdata) go into responses as literals the firmware re-stages
# extra fw-readable words (after the polled inputs)
EXTRA_IN = [('fifoParamCount', 'integer range 0 to 16'), ('setLocParamBad', 'std_logic'), ('cdrw_params', 'std_logic_vector(23 downto 0)'),
            ('cdrw_lrs_fire', 'integer range 0 to 524287'), ('allow_speedhack', 'std_logic')]


def subq_cut(text):
    """CDRW_SUBQ: subchannel Q (BCD position, track/index, LibCrypt skip) moves to firmware.
    Removes the readSubchannelState FSM, its trigger block, the LibCrypt compares and the nextSubdata reset load
    from the data process; the firmware writes nextSubdata through OUT ports and sees readSubchannel as an event."""
    L = text.split('\n')
    def span(start_pat, end_pat, from_i=0):
        i = next(k for k in range(from_i, len(L)) if re.search(start_pat, L[k]))
        ind = len(L[i]) - len(L[i].lstrip())
        j = next(k for k in range(i + 1, len(L)) if L[k].startswith(' ' * ind + end_pat))
        return i, j
    cuts = []
    cuts.append(span(r'^\s*case \(readSubchannelState\) is', 'end case;'))
    cuts.append(span(r'^\s*if \(readSubchannel = .1. or triggerUpdateSubchannel = .1.\) then', 'end if;'))
    i = next(k for k in range(len(L)) if re.search(r"libcryptKey\(15\) = '1' and \(\(lastReadSector \+ 2\)", L[k]))
    i0 = i - 1
    assert L[i0].strip() == 'if (', L[i0]
    j = next(k for k in range(i, len(L)) if L[k].strip() == 'end if;')
    cuts.append((i0, j))
    i = next(k for k in range(len(L)) if re.search(r'nextSubdata\(i\) <= ss_in\(i \+ 64\)', L[k]))
    cuts.append((i - 1, i + 1))
    drop = set()
    for a, b in cuts:
        drop.update(range(a, b + 1))
    return '\n'.join(l for k, l in enumerate(L) if k not in drop)


def transform(text, fw_hex=None, report=None):
    subq = os.environ.get('CDRW_SUBQ') == '1'
    if subq:
        text = subq_cut(text)
    a = gen.transform(text, 'analyse')
    lines, S, inputs, D = a['lines'], a['S'], a['inputs'], a['D']
    Sn = [s for s, t in S]
    tx = '\n'.join(lines)
    for s in STROBES:
        if s not in ('errCmd', 'errDrive', 'copySubdata') and s not in Sn:
            raise ValueError(f'strobe {s} not in S')
    for s in FLAGS:
        assert s in Sn, s
    ports = [(s, t) for s, t in S if s not in GLUE and s not in STROBES and s not in FLAGS and not PULSE.match(s)]
    pulses = [s for s in Sn if PULSE.match(s)]
    # ------------------------------------------------------------- inputs: one IN port per field (trimmed)
    D2 = dict(D)
    D2.update({'fifoParamCount': 'integer range 0 to 16', 'setLocParamBad': 'std_logic',
               'cdrw_params': 'std_logic_vector(23 downto 0)', 'cdrw_lrs_fire': 'integer range 0 to 524287', 'allow_speedhack': 'std_logic'})
    DROP = {'header', 'subheader', 'peakvolumeL', 'peakvolumeR', 'totalMinutesBCD', 'totalSecondsBCD', 'minutesBCD',
            'secondsBCD', 'nextSubdata', 'libcryptKey', 'fifoParamCount', 'setLocParamBad', 'FifoParam_Din'}
    ins = [(s, t) for s, t in inputs if s not in DROP] + EXTRA_IN
    fields = []   # (name, vhdl expression, width)
    for s, t in ins:
        e, w = gen.to_bits(s, t, tx)
        if s == 'trackInfo_DataOutA':
            e, w = 'trackInfo_DataOutA(18 downto 0)', 19
        fields.append((s, e, w))
    if not subq:
        for k in (0, 1, 8, 9):
            fields.append((f'nextSubdata{k}', f'nextSubdata({k})', 8))
    else:
        fields.append(('trackNumberBCD', 'std_logic_vector(trackNumberBCD)', 8))
        fields.append(('libcryptKey', 'libcryptKey', 16))
    fields.append(('libcryptOn', "'1' when libcryptKey /= x\"0000\" else '0'", 1))
    # only the fields the firmware reads (each one is a glue mux input); CDRW_IN_ALL=1 keeps every field
    if os.environ.get('CDRW_IN_ALL') != '1':
        fwdir = os.path.join(HERE, '..', 'rtl', 'cd_fw')
        src = ''.join(open(os.path.join(fwdir, n)).read() for n in sorted(os.listdir(fwdir))
                      if n.endswith('.s'))
        fields = [x for x in fields if re.search(r'\bIN_' + re.escape(x[0]) + r'\b', src)]
    assert len(fields) <= 96
    inmap = [(n, i, w) for i, (n, e, w) in enumerate(fields)]
    nin = len(fields)
    # ------------------------------------------------------------- enum helpers
    types_needed = set()
    for s, t in list(S) + ins:
        w, k, x = gen.width_of(D2.get(s, t), tx)
        if k == 'enum': types_needed.add(x)
    decl = ['   -- cd-rewrite-061: command, drive and motor logic in cd_subcpu firmware (docs/cd-rewrite-061-port.md)']
    for tn, lits in sorted(types_needed):
        decl += gen.enum_funcs(tn, lits)
    decl += ['   signal cdrw_in_sel : unsigned(6 downto 0);', '   signal cdrw_in_data : std_logic_vector(31 downto 0);',
             '   signal cdrw_libcrypt_on : std_logic;',
             '   signal cdrw_ev : std_logic_vector(15 downto 0);',
             '   signal cdrw_out_we : std_logic;', '   signal cdrw_out_adr : unsigned(6 downto 0);',
             '   signal cdrw_out_d : std_logic_vector(26 downto 0);',
             '   signal cdrw_strobe : std_logic_vector(15 downto 0);', '   signal cdrw_flags : std_logic_vector(3 downto 0);',
             '   signal cdrw_tval2 : unsigned(26 downto 0);',
             '   signal cdrw_dfire : std_logic;',
             '   signal cdrw_lrs_fire : integer range 0 to 524287 := 0;   -- lastReadSector on the DRIVE fire clock (old currentLBA source)',
             f'   signal cdrw_src : std_logic_vector({len(SRC) * 8 - 1} downto 0);',
             '   signal cdrw_rwr, cdrw_rreset : std_logic;', '   signal cdrw_rdin : std_logic_vector(7 downto 0);',
             '   signal cdrw_irq_pending, cdrw_fetch_idle, cdrw_drive_ready : std_logic;',
             '   signal cdrw_fw_we : std_logic;', '   signal cdrw_dbg : std_logic_vector(19 downto 0);',
             '   signal cdrw_preset : std_logic;', '   signal cdrw_params : std_logic_vector(23 downto 0) := (others => \'0\');',
             '   signal cdrw_st_or, cdrw_st_clr : std_logic_vector(7 downto 0) := (others => \'0\');   -- GetStat byte modifiers',
             '   signal cdrw_err_cmd, cdrw_err_drv : std_logic_vector(7 downto 0) := (others => \'0\');',
             '   signal cdrw_rsn_cmd, cdrw_rsn_drv, cdrw_rsn_next : std_logic_vector(7 downto 0) := (others => \'0\');',
             '   signal cdrw_mask_seek, cdrw_errcmd_1, cdrw_errdrv_1, cdrw_ackst_1, cdrw_dack_1, cdrw_dend_1 : std_logic := \'0\';',
             '   signal fifoParamCount : integer range 0 to 16 := 0;',
             '   signal setLocParamBad : std_logic := \'0\';',
             '   signal cdrw_newcmd : std_logic_vector(7 downto 0) := (others => \'0\');   -- the accepted command byte',
             '   signal cdrw_ign : std_logic;   -- Setmode while Setloc is pending (Reel Fishing): ignored']
    # ------------------------------------------------------------- body
    b = []
    b.append("   cdrw_libcrypt_on <= '1' when libcryptKey /= x\"0000\" else '0';")
    b.append('   process (all) begin')
    b.append("      cdrw_in_data <= (others => '0');")
    b.append('      case to_integer(cdrw_in_sel) is')
    for n, i, w in inmap:
        e = fields[i][1]
        if n == 'libcryptOn':
            e = 'cdrw_libcrypt_on'
        if n == 'newCmd':
            e = 'cdrw_newcmd'
        if w == 1 and not e.startswith('(0 =>'):
            b.append(f'         when {i} => cdrw_in_data(0) <= {e};')
        else:
            b.append(f'         when {i} => cdrw_in_data({w - 1} downto 0) <= {e};')
    b.append('         when others => null;')
    b.append('      end case;')
    b.append('   end process;')
    b.append("   cdrw_irq_pending <= '1' when CDROM_IRQFLAG /= \"00000\" else '0';")
    b.append("   cdrw_fetch_idle  <= '1' when sectorFetchState = SFETCH_IDLE else '0';")
    b.append("   cdrw_drive_ready <= '1' when sectorFetchState = SFETCH_IDLE and readOnDisk = '0' and readOnDisk_buffer = '0' and "
             "sectorProcessState = SPROC_IDLE and copyState = COPY_IDLE else '0';")
    # a command byte before the firmware took the previous one replaces it, unless it is the ignored Setmode
    # cdrw_newcmd is the old nextCmd (taken on every applied command byte)
    b.append("   cdrw_ign <= '1' when cmdPending = '1' and newCmd = x\"0E\" and cdrw_newcmd = x\"02\" else '0';")
    evs = ['(beginCommand and not cdrw_ign)', 'ackRead_valid', 'ackPendingIRQ', 'UpdateSubchannelDone', 'XA_start', 'XA_reset', 'newCD',
           'LIDopen', 'readSubchannel' if subq else 'FifoParam_Wr', 'cdrw_irq_none', 'fetchErr', 'hasCD']
    b.append("   cdrw_ev <= '0' & '0' & '0' & '0' & " + ' & '.join(
        ("(not cdrw_irq_pending)" if e == 'cdrw_irq_none' else e) for e in reversed(evs)) + ';')
    b.append('   cdrw_src <= ' + ' & '.join(f'({s})' if s != '0' else 'x"00"' for s in reversed(SRC)) + ';')
    sim_split = 'true' if os.environ.get('CDRW_SIM') == '1' else 'false'
    pm_depth = int(os.environ.get('CDRW_PM_DEPTH', '1536'))
    fw_writable = 'true' if (os.environ.get('CDRW_FW_DEBUG') == '1' or fw_hex is None) else 'false'
    if os.environ.get('CDRW_FW_DEBUG') == '1' or fw_hex is None:
        # debug only (or the hardware-only measurement with no image): load through the track-info window.
        # Address 0x1FF is a real track-info slot (track 127, word 3), so never in a release build.
        b.append("   cdrw_fw_we <= '1' when trackinfo_write = '1' and trackinfo_addr = \"111111111\" else '0';   -- DEBUG firmware load")
    else:
        b.append("   cdrw_fw_we <= '0';   -- firmware is the M10K init image in the bitstream")
    b.append(f'''   icd_subcpu : entity work.cd_subcpu
      generic map (PM_ABITS => {PM_ABITS}, PM_DEPTH => {pm_depth}, FW_WRITABLE => {fw_writable}, SIM_SPLIT => {sim_split}, NSRC => {len(SRC)}, DW => 28)
      port map (clk => clk1x, ce => ce, reset => reset,
         fw_we => cdrw_fw_we, fw_addr => trackinfo_data(31 downto 20), fw_data => trackinfo_data(19 downto 0),
         in_sel => cdrw_in_sel, in_data => cdrw_in_data, ev_in => cdrw_ev, out_we => cdrw_out_we, out_adr => cdrw_out_adr, out_d => cdrw_out_d,
         irq_pending => cdrw_irq_pending, dma_read => dma_read, limit_speed => LIMITREADSPEED,
         fetch_idle => cdrw_fetch_idle, drive_ready => cdrw_drive_ready, drive_busy => driveBusy, tval_drive => cdrw_tval2, drive_fire => cdrw_dfire,
         strobe => cdrw_strobe, flags => cdrw_flags, src => cdrw_src,
         resp_wr => cdrw_rwr, resp_din => cdrw_rdin, resp_reset => cdrw_rreset, param_reset => cdrw_preset, dbg => cdrw_dbg);''')
    b.append('   driveDelay <= to_integer(cdrw_tval2);')
    # strobes from commit (registered in cd_subcpu): drive S strobes as OR of commit bit and OUT pulse register
    for i, s in enumerate(STROBES):
        decl.append(f"   signal cdrw_p_{s} : std_logic := '0';")
        if s in Sn:
            if s == 'ackRead':
                # a DRIVE commit's data-sector ackRead is decided here on the commit clock from the fetched sector's
                # header (old drive process at handleDrive); the firmware stages the commit while that fetch still runs
                b.append(f"   {s} <= cdrw_strobe({i}) or cdrw_p_{s} or (cdrw_strobe({STROBES.index('processDataSector')}) and cdrw_hdr_ok);")
            else:
                b.append(f'   {s} <= cdrw_strobe({i}) or cdrw_p_{s};')
    decl.append("   signal cdrw_hdr_ok : std_logic;")
    b.append("   cdrw_hdr_ok <= '1' when (modeReg(6) = '0' or headerIsData = '1') and (modeReg(5) = '1' or headerDataSector = '1') else '0';")
    for i, s in enumerate(FLAGS):
        pass
    # flags: cd_subcpu flags with the glue's beginCommand set (BUSYSTS on the next clock)
    decl += ["   signal cdrw_busy_set, cdrw_pend_set, cdrw_busy_clr : std_logic := '0';"]
    # OUT decode
    pm = []   # (signal, port, width)
    b.append('   process (clk1x)')
    b.append('   begin')
    b.append('      if rising_edge(clk1x) then')
    for s in pulses:
        b.append(f"         {s} <= '0';")
    for s in STROBES:
        b.append(f"         cdrw_p_{s} <= '0';")
    b.append("         cdrw_busy_clr <= '0';")
    b.append("         FifoResponse_Wr <= '0'; FifoResponse_reset <= '0'; errorResponseNext_new <= '0';")
    b.append("         cdrw_errcmd_1 <= '0'; cdrw_errdrv_1 <= '0'; cdrw_ackst_1 <= '0'; cdrw_dack_1 <= '0'; cdrw_dend_1 <= '0';")
    b.append("         if reset = '1' then")
    b.append("            fifoParamCount <= 0; setLocParamBad <= '0'; allow_speedhack <= '0';")
    b.append("         elsif ce = '1' then")
    b.append("            internalStatus_1 <= internalStatus;")
    b.append("            if cdrw_dfire = '1' then cdrw_lrs_fire <= lastReadSector; end if;")
    b.append("            newCD_1 <= newCD;")
    b.append("            ackPendingIRQNext <= ackPendingIRQ;")
    # OUT writes
    b.append("            if cdrw_out_we = '1' then")
    b.append('               case to_integer(cdrw_out_adr) is')
    port = 0
    for s, t in ports:
        w = gen.width_of(t, tx)[0]
        assert w <= 32, s
        b.append(f'                  when {port} => ' + gen.from_bits(s, t, tx, f'cdrw_out_d({w - 1} downto 0)'))
        pm.append((s, port, w)); port += 1
    for s in pulses:
        b.append(f"                  when {port} => {s} <= '1';")
        pm.append((s, port, 1)); port += 1
    for s in STROBES:
        b.append(f"                  when {port} => cdrw_p_{s} <= '1';")
        pm.append(('PULSE_' + s, port, 1)); port += 1
    b.append(f"                  when {port} => cdrw_busy_clr <= '1';")
    pm.append(('BUSY_CLR', port, 1)); port += 1
    extra_ports = [('cdrw_st_or', 8), ('cdrw_st_clr', 8), ('cdrw_err_cmd', 8), ('cdrw_rsn_cmd', 8), ('cdrw_err_drv', 8),
                   ('cdrw_rsn_drv', 8), ('cdrw_mask_seek', 1), ('readSectorPointer', 3)]
    for s, w in extra_ports:
        if w == 1:
            b.append(f'                  when {port} => {s} <= cdrw_out_d(0);')
        elif s == 'readSectorPointer':
            b.append(f'                  when {port} => {s} <= unsigned(cdrw_out_d(2 downto 0));')
        else:
            b.append(f'                  when {port} => {s} <= cdrw_out_d({w - 1} downto 0);')
        pm.append((s, port, w)); port += 1
    if subq:
        for k in range(12):
            b.append(f'                  when {port} => nextSubdata({k}) <= cdrw_out_d(7 downto 0);')
            pm.append((f'nextSubdata{k}', port, 8)); port += 1
    shell_port = port                     # GetStat's shell_close: the only firmware write of the shell-open bit
    b.append(f"                  when {port} => null;")
    pm.append(('SHELL_CLR', port, 1)); port += 1
    speed_port = port                     # the read start that allows the speed hack (old allow_speedhack <= '1')
    b.append(f"                  when {port} => null;")
    pm.append(('SPEEDHACK_SET', port, 1)); port += 1
    assert port <= 96, port
    b.append('                  when others => null;')
    b.append('               end case;')
    b.append('            end if;')
    # speed hack (FORCECDSPEED) allowed from a read start until the drive leaves reading, an XA sample is written or
    # the MDEC works (old drive process: set with startReading, cleared on every such clock)
    b.append(f"            if cdrw_out_we = '1' and to_integer(cdrw_out_adr) = {speed_port} and XA_write = '0' and mdec_idle = '1' then allow_speedhack <= '1';")
    b.append("            elsif (driveState /= DRIVE_READING and startReading = '0') or XA_write = '1' or mdec_idle = '0' then allow_speedhack <= '0'; end if;")
    # the shell-open bit is the glue's (old drive process): set while the lid is open and on a new disc, cleared by
    # GetStat's shell_close; status writes from the firmware (also ones staged before the lid moved) leave it alone
    b.append(f"            if cdrw_out_we = '1' and to_integer(cdrw_out_adr) = {shell_port} then internalStatus(4) <= '0';")
    b.append("            else internalStatus(4) <= internalStatus(4); end if;")
    b.append("            if LIDopen = '1' or (newCD = '1' and newCD_1 = '0') then internalStatus(4) <= '1'; end if;")
    # subdata copy
    b.append("            if cdrw_strobe(15) = '1' then")
    b.append('               for i in 0 to 11 loop subdata(i) <= nextSubdata(i); end loop;')
    b.append('            end if;')
    # published data IRQ: readSectorPointer follows writeSectorPointer (old drive process)
    b.append("            if ackPendingIRQNext = '1' and pendingDriveIRQ = \"00001\" then readSectorPointer <= writeSectorPointer; end if;")
    # the data sector's slot with its ackRead (old drive process: skip the notified slot; the copy is idle at handleDrive)
    b.append(f"            if cdrw_strobe({STROBES.index('processDataSector')}) = '1' and cdrw_hdr_ok = '1' then")
    b.append("               if writeSectorPointer + 1 = readSectorPointer then writeSectorPointer <= writeSectorPointer + 2;")
    b.append("               else writeSectorPointer <= writeSectorPointer + 1; end if;")
    b.append("            end if;")
    # response pipelines (old cmd process "responses" section, same clock relations)
    b.append("            if cmdAck = '1' or ackRead_valid = '1' then")
    b.append("               FifoResponse_Wr <= '1'; FifoResponse_Din <= internalStatus;")
    b.append("               if cmdAck = '1' then FifoResponse_Din <= (internalStatus and not cdrw_st_clr) or cdrw_st_or; end if;")
    b.append("            end if;")
    b.append("            if (driveAck = '1' or ackDrive = '1' or ackDriveEnd = '1') and CDROM_IRQFLAG = \"00000\" then")
    b.append("               FifoResponse_Wr <= '1';")
    b.append("               if ackDriveEnd = '1' then FifoResponse_Din <= internalStatus_1; else FifoResponse_Din <= internalStatus; end if;")
    b.append("            end if;")
    b.append("            if cdrw_strobe(7) = '1' or cdrw_p_errCmd = '1' then")
    b.append("               FifoResponse_Wr <= '1'; FifoResponse_Din <= internalStatus or cdrw_err_cmd;")
    b.append("               if cdrw_mask_seek = '1' then FifoResponse_Din(6) <= '0'; end if;")
    b.append("               errorResponseNext_new <= '1'; cdrw_rsn_next <= cdrw_rsn_cmd;")
    b.append("            end if;")
    b.append("            if cdrw_strobe(8) = '1' or cdrw_p_errDrive = '1' then")
    b.append("               FifoResponse_Wr <= '1'; FifoResponse_Din <= internalStatus or cdrw_err_drv;")
    b.append("               errorResponseNext_new <= '1'; cdrw_rsn_next <= cdrw_rsn_drv;")
    b.append("            end if;")
    b.append("            if errorResponseNext_new = '1' then FifoResponse_Wr <= '1'; FifoResponse_Din <= cdrw_rsn_next; end if;")
    b.append("            if ackPendingIRQ = '1' then FifoResponse_reset <= '1'; end if;")
    b.append("            if ackPendingIRQNext = '1' then FifoResponse_Wr <= '1'; FifoResponse_Din <= pendingDriveResponse; end if;")
    b.append("            if cdrw_rreset = '1' then FifoResponse_reset <= '1'; end if;")
    b.append("            if cdrw_rwr = '1' then FifoResponse_Wr <= '1'; FifoResponse_Din <= cdrw_rdin; end if;")
    b.append("            if softReset = '1' then FifoParam_reset <= '1'; end if;")
    b.append("            if cdrw_preset = '1' then FifoParam_reset <= '1'; end if;")
    b.append("         end if;")
    # parameter FIFO count and Setloc check (old cmd process, outside ce)
    b.append("""         if (FifoParam_reset = '1') then
            fifoParamCount <= 0;
            setLocParamBad <= '0';
         elsif (FifoParam_Wr = '1') then
            if (fifoParamCount < 3) then
               if (unsigned(FifoParam_Din(3 downto 0)) > 9 or unsigned(FifoParam_Din(7 downto 4)) > 9 or
                   (fifoParamCount = 1 and unsigned(FifoParam_Din) >= 16#60#) or
                   (fifoParamCount = 2 and unsigned(FifoParam_Din) >= 16#75#)) then
                  setLocParamBad <= '1';
               end if;
            end if;
            if (fifoParamCount < 16) then fifoParamCount <= fifoParamCount + 1; end if;
            -- the first three parameter bytes, for the firmware (the old process read them right after the ACK)
            if fifoParamCount = 0 then cdrw_params(7 downto 0) <= FifoParam_Din; end if;
            if fifoParamCount = 1 then cdrw_params(15 downto 8) <= FifoParam_Din; end if;
            if fifoParamCount = 2 then cdrw_params(23 downto 16) <= FifoParam_Din; end if;
         elsif (FifoParam_Rd = '1') then
            if fifoParamCount > 0 then fifoParamCount <= fifoParamCount - 1; end if;
         end if;""")
    b.append('      end if;')
    b.append('   end process;')
    # flags: cmd_busy / cmdPending set by the glue on an accepted command, else the firmware flags
    b.append("   process (clk1x) begin if rising_edge(clk1x) then")
    b.append("      if reset = '1' then cdrw_busy_set <= '0';")
    b.append("      elsif ce = '1' then")
    b.append("         if beginCommand = '1' and cdrw_ign = '0' then cdrw_busy_set <= '1'; cdrw_newcmd <= newCmd;")
    b.append("         elsif cdrw_busy_clr = '1' then cdrw_busy_set <= '0'; end if;")   # fw took over the flags
    b.append("      end if;")
    b.append("   end if; end process;")
    b.append("   cmd_busy   <= cdrw_flags(0) or cdrw_busy_set;")
    b.append("   cmdPending <= cdrw_flags(1) or cdrw_busy_set;")
    b.append("   working    <= cdrw_flags(2);")
    # ------------------------------------------------------------- assemble the text
    out = lines
    ab = next(i for i, l in enumerate(out) if l == 'begin')
    out[ab:ab] = decl
    ea = max(i for i, l in enumerate(out) if 'synthesis translate_off' in l)
    out[ea:ea] = b + ['']
    res = '\n'.join(out)
    if os.environ.get('CDRW_SIM') == '1':   # simulation: LBAdisplay carries the sub-CPU's events and pc
        old = '   LBAdisplay  <= to_unsigned(currentLBA, 20);'
        assert res.count(old) == 1
        res = res.replace(old, '   LBAdisplay  <= unsigned(cdrw_dbg);')
        res = res.replace("elsif (SS_rden = '1' and SS_Adr < 128) then\n            SS_DataRead <= ss_out(to_integer(SS_Adr));",
                          "elsif true then\n            SS_DataRead <= ss_out(to_integer(SS_Adr));")
        old = 'SS_DataRead <= ss_out(to_integer(SS_Adr));'
        assert res.count(old) == 1
        res = res.replace(old, "SS_DataRead <= FifoResponse_Wr & FifoResponse_reset & FifoParam_reset & errorResponseNext_new & FifoResponse_Din & cdrw_strobe & x\"0\";")
    # signals now driven by the glue from other declarations: ensure no duplicate declarations of fifoParamCount/setLocParamBad
    for s in ('fifoParamCount', 'setLocParamBad'):
        n = len(re.findall(r'signal\s+' + s + r'\b', res))
        if n > 1:
            res = re.sub(r'\n\s*signal\s+' + s + r'\b[^;]*;[^\n]*', '', res, count=1)
    # parameter and response FIFOs in MLAB (frees two M10K for the program)
    for inst in ('ififoParam', 'ififoResponse'):
        old = f'{inst}: entity mem.SyncFifoFallThrough'
        assert res.count(old) == 1, inst
        res = res.replace(old, f'{inst}: entity work.cd_fifo_mlab')
    res = open(FIFO_MLAB).read() + '\n\n' + res
    # prepend cd_subcpu
    sub = open(SUBCPU).read()
    if fw_hex:
        pm_depth = int(os.environ.get('CDRW_PM_DEPTH', '1536'))
        allw = [int(l, 16) for l in open(fw_hex).read().split()]
        # the IN map above comes from rtl/cd_fw next to this script: the image must be assembled from those same
        # sources (a staging copy with stale firmware sources once gave fw12's image fw11's IN map: Rc1 white screen)
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as td:
            subprocess.run([sys.executable, os.path.join(HERE, 'cd_fw_asm.py'), os.path.join(HERE, '..', 'rtl', 'cd_fw', 'cd_fw.s'),
                            '--hex', os.path.join(td, 'fw.hex'), '--depth', str(len(allw))], check=True, capture_output=True)
            assert [int(l, 16) for l in open(os.path.join(td, 'fw.hex')).read().split()] == allw, \
                f'{fw_hex} is not the image of {os.path.join(HERE, "..", "rtl", "cd_fw")} (stale firmware sources?)'
        assert not any(allw[pm_depth:]), 'firmware larger than CDRW_PM_DEPTH'
        words = allw[:pm_depth]
        agg = '(' + ', '.join(f'{i} => x"{w:05x}"' for i, w in enumerate(words) if w) + ', others => (others => \'0\'))'
        a1 = "   constant PM_INIT : t_pm := (others => (others => '0'));"
        assert sub.count(a1) == 1
        sub = sub.replace(a1, f'   constant PM_INIT : t_pm := {agg};')
    res = sub + '\n\n' + res
    if report is not None:
        report.update({'ports': pm, 'inmap': inmap, 'nin': nin, 'S': S})
    return res


def write_inc(rep, path):
    with open(path, 'w') as f:
        f.write('; generated by scripts/cd_rewrite_061_prod.py: cd_top <-> cd_subcpu map\n')
        for s, p, w in rep['ports']:
            f.write(f'.equ P_{s} {p}\n')
        for s, i, w in rep['inmap']:
            f.write(f'.equ IN_{s} {i}\n')
        for i, s in enumerate(STROBES):
            f.write(f'.equ S_{s} {1 << i}\n.equ SB_{s} {i}\n')
        for i, s in enumerate(SRC_NAMES):
            f.write(f'.equ SRC_{s} {i}\n')
        f.write(f'.equ NIN {rep["nin"]}\n')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('src'); ap.add_argument('dst'); ap.add_argument('--fw'); ap.add_argument('--inc')
    a = ap.parse_args()
    rep = {}
    open(a.dst, 'w').write(transform(open(a.src).read(), a.fw, rep))
    if a.inc:
        write_inc(rep, a.inc)
    print('ports', len(rep['ports']), 'in words', rep['nin'])
