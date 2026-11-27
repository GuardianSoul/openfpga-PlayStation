#!/usr/bin/env python3
"""cd-rewrite-061: generate the two measurement probes for the CD controller rewrite (map/fit only, NOT functional).

Both probes cut the command process, the drive process and the motor-start process out of cd_top.vhd, prune the
concurrent helpers and processes that only fed them, and keep everything else (bus/IRQ process and read mux, FIFOs,
sector fetch/process/copy data path, XA, CD audio, track-info/subchannel process, save-state taps).
The signals the cut processes drove that the kept logic still reads (the interface set S) become registers driven by:

  mode 'off' (switch-off probe): a plain register chain fed by bus_dataWrite (no LUTs, shift-register recognition
             off), i.e. the interface state survives, its logic does not;
  mode 'cpu' (stand-in): a microcoded sub-CPU with its real interfaces: 1,024 x 20 microcode RAM (loadable through
             the track-info port), 16 x 32 register file and 32 x 32 data RAM in MLAB, 32-bit ALU with flags, branch,
             call/return stack, three 32-bit deadline timers against a free-running clock, an event latch with WAIT,
             an IN mux over every kept signal the cut processes used to read (polled inputs), and OUT ports that
             write S back (strobes auto-clear).

usage as a library: transform(text, mode) -> text.  As a script: cd_rewrite_061_gen.py IN.vhd OUT.vhd off|cpu
"""
import math, re, sys

CUT_ANCHORS = [
    # (line that identifies the process, which must sit inside it)
    "            motorStartClocks <= unsigned(ss_in(26)(25 downto 0));",
    "      variable workDelay_c : integer range 0 to 11;",
    "      variable driveDelay_c : integer range 0 to 13;",
]
# kept signals the cut processes read but which are derived combinationally from cut state: not CPU inputs
NOT_INPUTS = {'ss_in', 'rising_edge', 'falling_edge', 'to_integer', 'unsigned', 'signed', 'resize', 'to_unsigned',
              'std_logic_vector'}
EXTRA_INPUTS = [('FifoParam_Dout', 'std_logic_vector(7 downto 0)'), ('FifoParam_Empty', 'std_logic'),
                ('hasCD', 'std_logic'), ('LIDopen', 'std_logic'), ('INSTANTSEEK', 'std_logic'),
                ('FORCECDSPEED', 'std_logic_vector(2 downto 0)'), ('fastCD', 'std_logic'),
                ('LIMITREADSPEED', 'std_logic'), ('pauseOnCDSlow', 'std_logic'), ('testSeek', 'std_logic'),
                ('FifoData_Empty', 'std_logic'), ('mdec_idle', 'std_logic'),
                ('region', 'std_logic_vector(1 downto 0)'), ('FifoResponse_Empty', 'std_logic'),
                ('trackInfo_DataOutA', 'std_logic_vector(18 downto 0)')]
STROBE = re.compile(r'^(.*_Wr|.*_Rd|.*_reset|ack\w*|cmdAck|cmdIRQ|driveAck|getIDAck|handle\w+|clearSectorBuffers|'
                    r'process\w+|triggerUpdateSubchannel|start(Reading|Playing)|softReset|errorResponse\w*_new|'
                    r'ClearXACurrentSet|cmdResetXa|seekOnDiskDrive|reportSeekTarget|reportPauseNext)$')


def code(l):
    return re.sub(r'--.*', '', l)


def find_units(lines, lo, hi):
    """statements of the architecture body between lo and hi (0-based, exclusive): (start, end_incl, kind)"""
    units = []; i = lo
    while i < hi:
        l = code(lines[i])
        if not l.strip() or not re.match(r'^   \S', lines[i]) or re.match(r'^   --', lines[i]):
            i += 1; continue
        s = l.strip()
        if re.match(r'^(\w+\s*:\s*)?process\b', s):
            j = i
            while not re.match(r'^\s*end process', code(lines[j])): j += 1
            units.append((i, j, 'process')); i = j + 1; continue
        if re.match(r'^\w+\s*:\s*block\b', s):
            j = i
            while not re.match(r'^   end block', lines[j]): j += 1
            units.append((i, j, 'block')); i = j + 1; continue
        if re.match(r'^\w+\s*:.*\bgenerate\b', s):
            j = i
            while not re.match(r'^   end generate', lines[j]): j += 1
            units.append((i, j, 'generate')); i = j + 1; continue
        if re.match(r'^\w+\s*:\s*entity\b', s):
            j = i
            while not re.match(r'^   \);', lines[j]): j += 1
            units.append((i, j, 'instance')); i = j + 1; continue
        j = i
        while not code(lines[j]).rstrip().endswith(';'): j += 1
        units.append((i, j, 'assign')); i = j + 1
    return units


def assigned(txt):
    return set(re.findall(r'(?:^|;|\bthen\b|\belse\b|=>|\bbegin\b)\s*(\w+)(?:\s*\([^;<]*?\))*\s*<=', txt, re.M))


def words(txt):
    return set(re.findall(r'\b[A-Za-z_]\w*\b', txt))


def decls(lines):
    d = {}
    for l in lines:
        m = re.match(r'\s*signal\s+([\w,\s]+?)\s*:\s*([^:;]+?)\s*(:=.*)?;', code(l))
        if m:
            for n in m.group(1).split(','):
                d[n.strip()] = m.group(2).strip()
    m = re.search(r'entity cd_top is(.*?)end entity', '\n'.join(lines), re.S)
    for pl in m.group(1).split('\n'):
        pm = re.match(r'\s*(\w+)\s*:\s*(in|out)\s+([^:;]+?)\s*(:=.*)?;?$', code(pl))
        if pm:
            d[pm.group(1)] = pm.group(3).strip()
    return d


def enum_lits(text, t):
    m = re.search(r'type\s+' + t + r'\s+is\s*\((.*?)\);', text, re.S | re.I)
    return [x.strip() for x in code(m.group(1)).replace('\n', ' ').split(',') if x.strip()] if m else None


def width_of(t, text):
    """(width, kind, extra) for a declared type"""
    t = t.strip()
    m = re.match(r'(std_logic_vector|unsigned|signed)\s*\((\d+)\s+downto\s+(\d+)\)', t)
    if m: return int(m.group(2)) - int(m.group(3)) + 1, m.group(1), None
    if t == 'std_logic': return 1, 'sl', None
    m = re.match(r'integer\s+range\s+(-?\d+)\s+to\s+(\d+)', t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return max(1, b.bit_length()) + (1 if a < 0 else 0), 'int', (a, b)
    if t == 'tsubdata': return 96, 'subdata', None
    lits = enum_lits(text, t)
    if lits: return max(1, math.ceil(math.log2(len(lits)))), 'enum', (t, tuple(lits))
    raise ValueError('type ' + t)


def from_bits(name, t, text, src):
    """VHDL statement(s) assigning `name` from std_logic_vector expression `src` of the right width"""
    w, k, x = width_of(t, text)
    if k == 'sl':
        m = re.match(r'(\w+)\((\d+) downto (\d+)\)$', src)
        return f'{name} <= {m.group(1)}({m.group(3)});' if m else f'{name} <= {src}(0);'
    if k == 'std_logic_vector': return f'{name} <= {src};'
    if k in ('unsigned', 'signed'): return f'{name} <= {k}({src});'
    if k == 'int':
        a, b = x
        conv = f'to_integer(signed({src}))' if a < 0 else f'to_integer(unsigned({src}))'
        if (a == 0 or a == -1) and b == (1 << b.bit_length()) - 1:
            return f'{name} <= {conv};'
        return f'if {conv} > {b} then {name} <= {b}; elsif {conv} < {a} then {name} <= {a}; else {name} <= {conv}; end if;'
    if k == 'subdata':
        m = re.match(r'(\w+)\((\d+) downto (\d+)\)$', src)
        base, o = (m.group(1), int(m.group(3))) if m else (src, 0)
        return ' '.join(f'{name}({i}) <= {base}({o + 8 * i + 7} downto {o + 8 * i});' for i in range(12))
    if k == 'enum':
        tn, lits = x
        return f'{name} <= cdrw_to_{tn}(unsigned({src}));'
    raise ValueError(k)


def to_bits(name, t, text):
    """std_logic_vector expression of a signal"""
    w, k, x = width_of(t, text)
    if k == 'sl': return f'(0 => {name})', w
    if k == 'std_logic_vector': return name, w
    if k in ('unsigned', 'signed'): return f'std_logic_vector({name})', w
    if k == 'int':
        a, b = x
        return (f'std_logic_vector(to_signed({name}, {w}))' if a < 0 else f'std_logic_vector(to_unsigned({name}, {w}))'), w
    if k == 'subdata':
        return '(' + ' & '.join(f'{name}({i})' for i in reversed(range(12))) + ')', 96
    if k == 'enum':
        tn, lits = x
        return f'cdrw_from_{tn}({name})', w
    raise ValueError(k)


def enum_funcs(tn, lits):
    w = max(1, math.ceil(math.log2(len(lits))))
    a = [f'   function cdrw_to_{tn}(v : unsigned) return {tn} is begin', '      case to_integer(v) is']
    a += [f'         when {i} => return {l};' for i, l in enumerate(lits)]
    a += [f'         when others => return {lits[0]};', '      end case;', '   end function;']
    a += [f'   function cdrw_from_{tn}(e : {tn}) return std_logic_vector is begin', '      case e is']
    a += [f'         when {l} => return std_logic_vector(to_unsigned({i}, {w}));' for i, l in enumerate(lits)]
    a += ['      end case;', '   end function;']
    return a


def transform(text, mode):
    assert mode in ('off', 'cpu', 'analyse')
    # VHDL is case-insensitive; the analysis below is not: one spelling per identifier
    ids = {}
    for w in re.findall(r'\b[A-Za-z_]\w*\b', re.sub(r'--[^\n]*', '', text)):
        ids.setdefault(w.lower(), {}).setdefault(w, 0)
        ids[w.lower()][w] += 1
    for low, forms in ids.items():
        if len(forms) > 1 and low not in ('data',):
            main = max(forms, key=forms.get)
            for f in forms:
                if f != main:
                    text = re.sub(r'\b' + f + r'\b', main, text)
    lines = text.split('\n')
    arch_begin = next(i for i, l in enumerate(lines) if l == 'begin')
    arch_end = next(i for i, l in enumerate(lines) if l.startswith('end architecture'))
    # synthesis translate_off region: invisible to liveness, left as is
    toff = [i for i, l in enumerate(lines) if 'synthesis translate_off' in l]
    body_end = toff[-1] if toff and toff[-1] > arch_begin else arch_end
    units = find_units(lines, arch_begin + 1, body_end)
    cut = []
    for a in CUT_ANCHORS:
        idx = [i for i, l in enumerate(lines) if l == a]
        assert len(idx) == 1, a
        u = [x for x in units if x[0] <= idx[0] <= x[1] and x[2] == 'process']
        assert len(u) == 1, a
        cut.append(u[0])
    cut_txt = '\n'.join(code(l) for s, e, _ in cut for l in lines[s:e + 1])
    cut_asg = assigned(cut_txt)
    keep = [u for u in units if u not in cut]
    # ss_out taps are dead in this build (cd_top's SS read path is not connected): drop them all
    def is_ss(u):
        t = '\n'.join(code(l) for l in lines[u[0]:u[1] + 1])
        return re.search(r'\bss_out\s*\(', t) and not re.search(r'\b(?!ss_out\b)\w+(?:\s*\([^;<]*?\))*\s*<=', t.replace('ss_out', '')) \
            if u[2] in ('assign', 'generate') else False
    keep = [u for u in keep if not is_ss(u)]
    dropped_ss = [u for u in units if u not in cut and u not in keep]
    ports_out = set()
    m = re.search(r'entity cd_top is(.*?)end entity', text, re.S)
    for pl in m.group(1).split('\n'):
        pm = re.match(r'\s*(\w+)\s*:\s*out\b', pl)
        if pm: ports_out.add(pm.group(1))
    # iterative pruning of dead assigns/processes/blocks (instances and generates always kept)
    pruned = []
    while True:
        texts = {u: '\n'.join(code(l) for l in lines[u[0]:u[1] + 1]) for u in keep}
        changed = False
        for u in list(keep):
            if u[2] not in ('assign', 'process', 'block'): continue
            outs = assigned(texts[u]) - {'ss_out'}
            if not outs or outs & ports_out: continue
            others = set().union(*(words(texts[v]) for v in keep if v != u))
            if not outs & others:
                keep.remove(u); pruned.append(u); changed = True
        if not changed: break
    if mode in ('cpu', 'analyse'):
        cw = words(cut_txt)
        pa = set().union(*(assigned('\n'.join(code(l) for l in lines[v[0]:v[1] + 1])) for v in pruned))
        for u in list(pruned):
            t = '\n'.join(code(l) for l in lines[u[0]:u[1] + 1])
            if u[2] == 'assign' and assigned(t) & cw and not (words(t) - assigned(t)) & (cut_asg | pa) and 'FifoParam_Dout' not in t:
                pruned.remove(u); keep.append(u)
    texts = {u: '\n'.join(code(l) for l in lines[u[0]:u[1] + 1]) for u in keep}
    kept_words = set().union(*(words(t) for t in texts.values()))
    kept_asg = set().union(*(assigned(t) for t in texts.values()))
    for u in keep:
        if u[2] == 'instance':
            kept_asg |= set(re.findall(r'=>\s*(\w+)', texts[u])) & set(re.findall(r'\b(\w+)\s*\)?\s*$', texts[u], re.M)) & set()
    D = decls(lines)
    inst_out = {'FifoParam_Dout'}
    S = sorted(s for s in cut_asg if s in kept_words and s not in inst_out and s in D and not s.startswith('ss_'))
    both = [s for s in S if s in kept_asg]
    assert not both, ('assigned in kept logic too', both)
    # polled inputs: kept signals the cut processes read
    cut_words = words(cut_txt)
    I = sorted(s for s in cut_words if s in D and s not in cut_asg and s not in NOT_INPUTS and s in kept_asg
               and not s.startswith('ss_'))
    pruned_asg = set().union(*(assigned('\n'.join(code(l) for l in lines[u[0]:u[1] + 1])) for u in pruned)) if pruned else set()
    I = [s for s in I if s not in pruned_asg]
    inputs = [(s, D[s]) for s in I] + [x for x in EXTRA_INPUTS if x[0] not in I]
    # --- emit
    remove = set()
    for u in cut + pruned + dropped_ss:
        remove.update(range(u[0], u[1] + 1))
    out_lines = [l for i, l in enumerate(lines) if i not in remove]
    if mode == 'analyse':
        return {'lines': out_lines, 'S': [(x, D[x]) for x in S], 'inputs': inputs, 'D': D, 'toff': bool(toff),
                'cut_txt': cut_txt}
    types_needed = set()
    for s in S:
        w, k, x = width_of(D[s], text)
        if k == 'enum': types_needed.add(x)
    if mode == 'cpu':
        for s, t in inputs:
            w, k, x = width_of(t, text)
            if k == 'enum': types_needed.add(x)
    # declarations before the architecture's begin
    decl = ['   -- cd-rewrite-061 probe (' + mode + '): NOT functional, area measurement only']
    for tn, lits in sorted(types_needed):
        decl += enum_funcs(tn, lits)
    Wtot = sum(width_of(D[s], text)[0] for s in S)
    body = []
    if mode == 'off':
        decl += [f'   signal cdrw_chain : std_logic_vector({Wtot + 7} downto 0) := (others => \'0\');',
                 '   attribute altera_attribute : string;',
                 '   attribute altera_attribute of cdrw_chain : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";']
        body += ['   process (clk1x) begin if rising_edge(clk1x) then   -- cd-rewrite-061 probe off',
                 # option pins the cut logic read stay live (config-CDC geometry in host.sdc counts their registers)
                 f'      cdrw_chain <= cdrw_chain({Wtot - 1} downto 0) & (bus_dataWrite xor (FORCECDSPEED & INSTANTSEEK & '
                 'LIMITREADSPEED & fastCD & testSeek & pauseOnCDSlow) xor ("000000" & region));',
                 '   end if; end process;',
                 '   process (all) begin   -- cd-rewrite-061 probe off: interface state = chain bits']
        pos = 8
        for s in S:
            w = width_of(D[s], text)[0]
            body.append('      ' + from_bits(s, D[s], text, f'cdrw_chain({pos + w - 1} downto {pos})'))
            pos += w
        body.append('   end process;')
    else:
        decl += cpu_decls()
        # input words
        bits = []
        for s, t in inputs:
            e, w = to_bits(s, t, text)
            bits.append((e, w))
        flat = ' & '.join(f'{e}' for e, w in bits)
        Win = sum(w for e, w in bits)
        nin = (Win + 31) // 32
        decl += [f'   signal cdrw_inbits : std_logic_vector({nin * 32 - 1} downto 0);']
        body += [f'   cdrw_inbits <= std_logic_vector(to_unsigned(0, {nin * 32 - Win})) & {flat};' if nin * 32 > Win
                 else f'   cdrw_inbits <= {flat};']
        # events: edges/levels the old processes reacted to
        ev = [x for x in ['beginCommand', 'FifoParam_Wr', 'UpdateSubchannelDone', 'XA_start', 'ackRead_valid',
                          'ackPendingIRQ', 'fetchErr', 'newCD', 'readOnDisk_buffer', 'headerIsData', 'LIDopen', 'hasCD']
              if x in D]
        body += cpu_body(nin, ev)
        # output ports: pack S into 32-bit words
        body.append('   process (clk1x) begin if rising_edge(clk1x) then   -- cd-rewrite-061 stand-in: OUT write-back')
        strobes = [s for s in S if width_of(D[s], text)[1] == 'sl' and STROBE.match(s)]
        for s in strobes:
            body.append(f"      {s} <= '0';")
        port = 0; pos = 0
        body.append('      if cdrw_out_we = \'1\' then case to_integer(cdrw_out_adr) is')
        cur = []
        def flush():
            nonlocal port, cur, pos
            if cur:
                body.append(f'         when {port} =>')
                for c in cur: body.append('            ' + c)
                port += 1; cur = []; pos = 0
        for s in S:
            w = width_of(D[s], text)[0]
            if w > 32:
                flush()
                for k in range(0, w, 32):
                    hi = min(w, k + 32)
                    body.append(f'         when {port} => cdrw_wide_{s}({hi - 1} downto {k}) <= cdrw_out_d({hi - k - 1} downto 0);')
                    port += 1
                decl.append(f'   signal cdrw_wide_{s} : std_logic_vector({w - 1} downto 0) := (others => \'0\');')
                continue
            if pos + w > 32: flush()
            cur.append(from_bits(s, D[s], text, f'cdrw_out_d({pos + w - 1} downto {pos})'))
            pos += w
        flush()
        body.append('         when others => null;')
        body.append('      end case; end if;')
        # commit stage: response bytes and IRQ strobes are staged by firmware and released in hardware when the
        # chosen deadline timer fires, so the guest sees them at the deadline, not at firmware time
        pri = ['cmdAck', 'cmdIRQ', 'driveAck', 'ackDrive', 'ackDriveEnd', 'ackRead', 'getIDAck', 'ackPendingIRQNext',
               'errorResponseNext_new', 'errorResponseCmd_new', 'errorResponseDrive_new', 'softReset', 'startReading',
               'startPlaying', 'FifoParam_Rd', 'FifoParam_reset']
        cm = [x for x in pri if x in strobes][:16]
        body += ['      if cdrw_out_we = \'1\' and cdrw_out_adr = 200 then',
                 '         cdrw_stage(to_integer(cdrw_scount)) <= cdrw_out_d(7 downto 0); cdrw_scount <= cdrw_scount + 1;',
                 '      end if;',
                 '      if cdrw_out_we = \'1\' and cdrw_out_adr = 201 then',
                 '         cdrw_cpend <= \'1\'; cdrw_csel <= unsigned(cdrw_out_d(17 downto 16)); cdrw_cmask <= cdrw_out_d(15 downto 0);',
                 '      end if;',
                 '      if cdrw_cpend = \'1\' and cdrw_tfire(to_integer(cdrw_csel) mod 3) = \'1\' then',
                 '         cdrw_cpend <= \'0\'; cdrw_cdrain <= \'1\'; cdrw_ci <= (others => \'0\');',
                 '      end if;',
                 '      if cdrw_cdrain = \'1\' then',
                 '         if cdrw_ci /= cdrw_scount then',
                 '            FifoResponse_Din <= cdrw_stage(to_integer(cdrw_ci)); FifoResponse_Wr <= \'1\'; cdrw_ci <= cdrw_ci + 1;',
                 '         else',
                 '            cdrw_cdrain <= \'0\'; cdrw_scount <= (others => \'0\');']
        for i, st in enumerate(cm):
            body.append(f"            if cdrw_cmask({i}) = '1' then {st} <= '1'; end if;")
        body += ['         end if;', '      end if;']
        decl += ['   type cdrw_t_stage is array (0 to 7) of std_logic_vector(7 downto 0);',
                 '   signal cdrw_stage : cdrw_t_stage;',
                 '   signal cdrw_scount, cdrw_ci : unsigned(2 downto 0) := (others => \'0\');',
                 '   signal cdrw_cpend, cdrw_cdrain : std_logic := \'0\';',
                 '   signal cdrw_csel : unsigned(1 downto 0) := (others => \'0\');',
                 '   signal cdrw_cmask : std_logic_vector(15 downto 0) := (others => \'0\');']
        body.append('   end if; end process;')
        for s in S:
            if width_of(D[s], text)[0] > 32:
                body.append('   ' + from_bits(s, D[s], text, f'cdrw_wide_{s}').replace(f'{s} <=', f'{s} <=', 1)
                            if width_of(D[s], text)[1] != 'subdata' else
                            '   ' + ' '.join(f'{s}({i}) <= cdrw_wide_{s}({8 * i + 7} downto {8 * i});' for i in range(12)))
        decl.append(f'   -- cd-rewrite-061: {len(S)} interface signals, {Wtot} bits, {port} OUT ports; '
                    f'{len(inputs)} polled inputs, {Win} bits, {nin} IN words')
    ab = next(i for i, l in enumerate(out_lines) if l == 'begin')
    out_lines[ab:ab] = decl
    ea = max(i for i, l in enumerate(out_lines) if 'synthesis translate_off' in l) if toff else \
        next(i for i, l in enumerate(out_lines) if l.startswith('end architecture'))
    out_lines[ea:ea] = body + ['']
    report = {'cut_lines': sum(e - s + 1 for s, e, _ in cut), 'pruned': [(lines[s].strip()[:60], e - s + 1) for s, e, _ in pruned],
              'ss_dropped': len(dropped_ss), 'S': [(s, D[s]) for s in S], 'Wtot': Wtot, 'inputs': inputs}
    transform.report = report
    return '\n'.join(out_lines)


def cpu_decls():
    return [
        '   type cdrw_t_ucode is array (0 to 1023) of std_logic_vector(19 downto 0);',
        '   signal cdrw_ucode : cdrw_t_ucode;',
        '   attribute ramstyle of cdrw_ucode : signal is "M10K";',
        '   -- register file (2 read ports = 2 copies) and data RAM: RamMLAB (altdpram MLAB, async read) as the CPU uses',
        '   signal cdrw_rf_we, cdrw_dm_we : std_logic := \'0\';',
        '   signal cdrw_rf_wa : std_logic_vector(3 downto 0) := (others => \'0\');',
        '   signal cdrw_dm_wa, cdrw_dm_ra : std_logic_vector(4 downto 0) := (others => \'0\');',
        '   signal cdrw_rf_wd, cdrw_dm_wd, cdrw_dm_q : std_logic_vector(31 downto 0) := (others => \'0\');',
        '   signal cdrw_ir : std_logic_vector(19 downto 0);',
        '   signal cdrw_pc : unsigned(9 downto 0) := (others => \'0\');',
        '   signal cdrw_phase : std_logic := \'0\';',
        '   type cdrw_t_stack is array (0 to 3) of std_logic_vector(9 downto 0);',
        '   signal cdrw_stack : cdrw_t_stack;',
        '   signal cdrw_sp : unsigned(1 downto 0) := (others => \'0\');',
        '   signal cdrw_fz, cdrw_fc, cdrw_fn : std_logic := \'0\';',
        '   signal cdrw_now : unsigned(31 downto 0) := (others => \'0\');',
        '   type cdrw_t_dl is array (0 to 2) of std_logic_vector(31 downto 0);',
        '   signal cdrw_dl : cdrw_t_dl;',
        '   signal cdrw_tarm : std_logic_vector(2 downto 0) := (others => \'0\');',
        '   signal cdrw_tfire : std_logic_vector(2 downto 0) := (others => \'0\');',
        '   signal cdrw_ev : std_logic_vector(15 downto 0) := (others => \'0\');',
        '   signal cdrw_evin, cdrw_evin_1 : std_logic_vector(15 downto 0) := (others => \'0\');',
        '   signal cdrw_out_we : std_logic := \'0\';',
        '   signal cdrw_out_adr : unsigned(7 downto 0) := (others => \'0\');',
        '   signal cdrw_out_d : std_logic_vector(31 downto 0) := (others => \'0\');',
        '   signal cdrw_ra, cdrw_rb, cdrw_inw : std_logic_vector(31 downto 0);',
    ]


def cpu_body(nin, ev):
    evs = ' & '.join(f"{e}" for e in ev)
    nev = len(ev)
    b = ['   -- cd-rewrite-061 stand-in: microcoded CD sub-CPU (2 clocks per instruction)',
         f'   cdrw_evin <= std_logic_vector(to_unsigned(0, {16 - nev - 3})) & cdrw_tarm & {evs};',
         '   icdrw_rfa : entity work.RamMLAB generic map (width => 32, widthad => 4) port map (inclock => clk1x, wren => cdrw_rf_we,',
         '      data => cdrw_rf_wd, wraddress => cdrw_rf_wa, rdaddress => cdrw_ir(11 downto 8), q => cdrw_ra);',
         '   icdrw_rfb : entity work.RamMLAB generic map (width => 32, widthad => 4) port map (inclock => clk1x, wren => cdrw_rf_we,',
         '      data => cdrw_rf_wd, wraddress => cdrw_rf_wa, rdaddress => cdrw_ir(3 downto 0), q => cdrw_rb);',
         '   cdrw_dm_ra <= std_logic_vector(unsigned(cdrw_ra(4 downto 0)) + unsigned(cdrw_ir(9 downto 5)));',
         '   icdrw_dm : entity work.RamMLAB generic map (width => 32, widthad => 5) port map (inclock => clk1x, wren => cdrw_dm_we,',
         '      data => cdrw_dm_wd, wraddress => cdrw_dm_wa, rdaddress => cdrw_dm_ra, q => cdrw_dm_q);',
         '   process (all) begin',
         '      cdrw_inw <= (others => \'0\');',
         '      case to_integer(unsigned(cdrw_ir(4 downto 0))) is']
    for k in range(nin):
        b.append(f'         when {k} => cdrw_inw <= cdrw_inbits({32 * k + 31} downto {32 * k});')
    b += [f'         when {nin} => cdrw_inw <= std_logic_vector(cdrw_now);',
          f'         when {nin + 1} => cdrw_inw <= x"0000" & cdrw_ev;',
          f'         when {nin + 2} => cdrw_inw <= cdrw_dm_q;',
          '         when others => null;',
          '      end case;',
          '   end process;',
          '   process (clk1x)',
          '      variable a, b, r : unsigned(32 downto 0);',
          '      variable wr : std_logic;',
          '      variable op : integer range 0 to 15;',
          '   begin',
          '      if rising_edge(clk1x) then',
          '         -- microcode load through the track-info bridge window (firmware is data, not ROM)',
          '         if trackinfo_write = \'1\' and trackinfo_addr = "111111111" then',
          '            cdrw_ucode(to_integer(unsigned(trackinfo_data(29 downto 20)))) <= trackinfo_data(19 downto 0);',
          '         end if;',
          '         cdrw_ir <= cdrw_ucode(to_integer(cdrw_pc));',
          '         cdrw_now <= cdrw_now + 1;',
          '         cdrw_evin_1 <= cdrw_evin;',
          '         cdrw_ev <= cdrw_ev or (cdrw_evin and not cdrw_evin_1);',
          '         for k in 0 to 2 loop',
          '            cdrw_tfire(k) <= \'0\';',
          '            if cdrw_tarm(k) = \'1\' and cdrw_now = unsigned(cdrw_dl(k)) then cdrw_tarm(k) <= \'0\'; cdrw_ev(13 + k) <= \'1\'; cdrw_tfire(k) <= \'1\'; end if;',
          '         end loop;',
          '         cdrw_out_we <= \'0\'; cdrw_rf_we <= \'0\'; cdrw_dm_we <= \'0\';',
          '         if reset = \'1\' then',
          '            cdrw_pc <= (others => \'0\'); cdrw_phase <= \'0\'; cdrw_tarm <= (others => \'0\'); cdrw_ev <= (others => \'0\');',
          '         elsif ce = \'1\' then',
          '            cdrw_phase <= not cdrw_phase;',
          '            if cdrw_phase = \'1\' then',
          '               op := to_integer(unsigned(cdrw_ir(19 downto 16)));',
          '               a := unsigned(\'0\' & cdrw_ra);',
          '               if op = 1 or op = 3 then b := unsigned(resize(signed(cdrw_ir(7 downto 0)), 33)); else b := unsigned(\'0\' & cdrw_rb); end if;',
          '               wr := \'0\'; r := a;',
          '               cdrw_pc <= cdrw_pc + 1;',
          '               case op is',
          '                  when 0 =>   -- ALU reg-reg, function in ir(7:4)',
          '                     wr := \'1\';',
          '                     case to_integer(unsigned(cdrw_ir(7 downto 4))) is',
          '                        when 0 => r := a + b;',
          '                        when 1 => r := a - b;',
          '                        when 2 => r := a and b;',
          '                        when 3 => r := a or b;',
          '                        when 4 => r := a xor b;',
          '                        when 5 => r := b;',
          '                        when 6 => r := a(31 downto 0) & \'0\';',
          '                        when 7 => r := \'0\' & \'0\' & a(31 downto 1);',
          '                        when 8 => r := \'0\' & a(23 downto 0) & x"00";',
          '                        when 9 => r := \'0\' & x"00" & a(31 downto 8);',
          '                        when 10 => if a(31 downto 0) < b(31 downto 0) then r := (0 => \'1\', others => \'0\'); else r := (others => \'0\'); end if;',
          '                        when others => r := not a;',
          '                     end case;',
          '                     if r(31 downto 0) = 0 then cdrw_fz <= \'1\'; else cdrw_fz <= \'0\'; end if; cdrw_fc <= r(32); cdrw_fn <= r(31);',
          '                  when 1 => r := a + b; wr := \'1\';   -- ADDI',
          '                  when 2 => r := \'0\' & unsigned(cdrw_ra(23 downto 0)) & unsigned(cdrw_ir(7 downto 0)); wr := \'1\';   -- shift in a byte',
          '                  when 3 => r := a - b; if r(31 downto 0) = 0 then cdrw_fz <= \'1\'; else cdrw_fz <= \'0\'; end if; cdrw_fc <= r(32); cdrw_fn <= r(31);   -- CMPI',
          '                  when 4 =>   -- branch: cond in rd field, absolute 10-bit target',
          '                     if (cdrw_ir(15 downto 12) = "0000") or (cdrw_ir(15 downto 12) = "0001" and cdrw_fz = \'1\') or',
          '                        (cdrw_ir(15 downto 12) = "0010" and cdrw_fz = \'0\') or (cdrw_ir(15 downto 12) = "0011" and cdrw_fc = \'1\') or',
          '                        (cdrw_ir(15 downto 12) = "0100" and cdrw_fc = \'0\') or (cdrw_ir(15 downto 12) = "0101" and cdrw_fn = \'1\') or',
          '                        (cdrw_ir(15 downto 12) = "0110" and cdrw_fn = \'0\') then',
          '                        cdrw_pc <= unsigned(cdrw_ir(9 downto 0));',
          '                     end if;',
          '                  when 5 => cdrw_stack(to_integer(cdrw_sp)) <= std_logic_vector(cdrw_pc + 1); cdrw_sp <= cdrw_sp + 1; cdrw_pc <= unsigned(cdrw_ir(9 downto 0));   -- CALL',
          '                  when 6 => cdrw_pc <= unsigned(cdrw_stack(to_integer(cdrw_sp - 1))); cdrw_sp <= cdrw_sp - 1;   -- RET',
          '                  when 7 => r := unsigned(\'0\' & cdrw_inw); wr := \'1\';   -- IN (port in ir(4:0)); port ev clears the latch',
          f'                     if to_integer(unsigned(cdrw_ir(4 downto 0))) = {nin + 1} then cdrw_ev <= (others => \'0\'); end if;',
          '                  when 8 => cdrw_out_we <= \'1\'; cdrw_out_adr <= unsigned(cdrw_ir(7 downto 0)); cdrw_out_d <= cdrw_ra;   -- OUT',
          '                  when 9 => if (cdrw_ev(15 downto 8) and cdrw_ir(7 downto 0)) = x"00" and (cdrw_ev(7 downto 0) and cdrw_ir(15 downto 8)) = x"00" then cdrw_pc <= cdrw_pc; end if;   -- WAIT',
          '                  when 10 => cdrw_dl(to_integer(unsigned(cdrw_ir(1 downto 0)) mod 3)) <= std_logic_vector(cdrw_now + unsigned(cdrw_ra));   -- TIMER k = now + rs',
          '                             cdrw_tarm(to_integer(unsigned(cdrw_ir(1 downto 0)) mod 3)) <= \'1\';',
          '                  when 11 => cdrw_dm_we <= \'1\'; cdrw_dm_wa <= cdrw_dm_ra; cdrw_dm_wd <= cdrw_rb;   -- ST',
          '                  when others => null;',
          '               end case;',
          '               if wr = \'1\' then',
          '                  cdrw_rf_we <= \'1\'; cdrw_rf_wa <= cdrw_ir(15 downto 12); cdrw_rf_wd <= std_logic_vector(r(31 downto 0));',
          '               end if;',
          '            end if;',
          '         end if;',
          '      end if;',
          '   end process;']
    return b


if __name__ == '__main__':
    src, dst, mode = sys.argv[1:4]
    t = transform(open(src).read(), mode)
    open(dst, 'w').write(t)
    r = transform.report
    print('cut lines', r['cut_lines'], 'ss taps dropped', r['ss_dropped'])
    print('pruned', r['pruned'])
    print('interface S', len(r['S']), 'signals', r['Wtot'], 'bits')
    print('inputs', len(r['inputs']), [s for s, t in r['inputs']])
