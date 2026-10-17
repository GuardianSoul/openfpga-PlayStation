#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Lockstep checks for memorymux-fifo-ram (scripts/prepare_memorymux_fifo_ram.py).

Bench 1 (sim/reference/tb_memorymux_fifo_ram.vhd): unmodified MiSTer
memorymux against the patched copy under random CPU store/load/fetch traffic,
DMA pauses, CE pauses, resets, GPU stalls and SDRAM latency; every output at
every clk2x edge, and the FIFO's Empty/NearFull/Dout every clk1x edge.

Bench 2 (sim/reference/tb_memorymux_fifo_cpu.vhd): two complete MiSTer CPU +
memorymux pairs (reference, patched) running a generated cached MIPS program
of store bursts, loads and ALU work against RAM and I/O, with DMA pauses;
every CPU and memorymux output is compared at every clk2x/clk3x edge.

Both READ_MODEs are checked with a DONT_CARE M10K model (X on a mixed-port
collision). Negative controls (planted faults) must be rejected.
"""
import argparse
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_memorymux_fifo_ram import (FIFO_RTL, RAM_SIM, UPSTREAM, check_pin,
                                        memorymux_fifo_ram_source)
from tool_paths import ROOT, find_tool

BUILD = ROOT / 'build/sim/memorymux-fifo-ram'
GHDL_FLAGS = ['--std=08', '-frelaxed']


def ports(path, entity):
    """(name, direction, type) for each port of `entity` in a VHDL file."""
    text = Path(path).read_text()
    start = re.search(rf'entity\s+{entity}\s+is', text, re.I).end()
    body = text[start:text.index('end', text.index('port', start))]
    body = body[body.index('(', body.index('port')) + 1:]
    result = []
    for raw in body.splitlines():
        line = raw.split('--')[0].strip()
        match = re.match(r'(\w+)\s*:\s*(in|out)\s+(.*)$', line, re.I)
        if match:
            kind = match[3].split(':=')[0].strip().rstrip(';').strip()
            while kind.count(')') > kind.count('('):
                kind = kind[:-1].strip()
            result.append((match[1], match[2].lower(), kind))
    return result


def zero(kind):
    if kind.lower() == 'std_logic':
        return "'0'"
    if kind.lower().startswith('integer') or kind.lower().startswith('natural'):
        return '0'
    return "(others => '0')"


def same(left, right, kind):
    """Equality that treats metavalues literally (numeric_std '=' does not)."""
    if kind.lower().startswith(('unsigned', 'signed')):
        return f'std_logic_vector({left}) = std_logic_vector({right})'
    return f'{left} = {right}'


def mux_bench():
    definitions = ports(UPSTREAM / 'rtl/memorymux.vhd', 'memorymux')
    declarations, ref, cand, compare = [], [], [], []
    for name, direction, kind in definitions:
        if direction == 'in':
            init = "'1'" if name in ('clk1x', 'clk2x') else zero(kind)
            declarations.append(f'   signal {name} : {kind} := {init};')
            ref.append(f'{name} => {name}')
            cand.append(f'{name} => {name}')
        else:
            declarations.append(f'   signal ref_{name}, cand_{name} : {kind};')
            ref.append(f'{name} => ref_{name}')
            cand.append(f'{name} => cand_{name}')
            compare.append(f'         assert {same("ref_" + name, "cand_" + name, kind)} report "memorymux output mismatch: {name}" '
                           'severity failure;')
    instances = ('   ref_mux : entity work.memorymux port map (\n      ' + ',\n      '.join(ref) + '\n   );\n'
                 '   cand_mux : entity cand.memorymux port map (\n      ' + ',\n      '.join(cand) + '\n   );\n')
    observer = ('   process (clk2x)\n   begin\n      if rising_edge(clk2x) then\n' + '\n'.join(compare) +
                '\n      end if;\n   end process;\n')
    text = (ROOT / 'sim/reference/tb_memorymux_fifo_ram.vhd').read_text()
    for marker in ('   -- GENERATED_DECLARATIONS\n', '   -- GENERATED_COMPARE\n'):
        if text.count(marker) != 1:
            raise SystemExit('bench marker missing: ' + marker)
    text = text.replace('   -- GENERATED_DECLARATIONS\n', '\n'.join(declarations) + '\n')
    return text.replace('   -- GENERATED_COMPARE\n', instances + '\n' + observer), len(compare)


def run(command, directory, log, timeout=1800):
    result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=timeout)
    with log.open('a') as handle:
        handle.write('$ ' + ' '.join(map(str, command)) + '\n' + result.stdout + result.stderr + '\n')
    return result


def build_variant(name, mode, fifo_fault=None, model_rdw='DONT_CARE', bench='mux'):
    """Analyze reference + candidate libraries and the bench in a fresh directory."""
    ghdl = find_tool('ghdl')
    directory = BUILD / name
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    log = directory / 'checks.log'
    log.write_text('')
    patched = memorymux_fifo_ram_source(directory / 'patched', mode)
    fifo = Path(next(s['path'] for s in patched['fifo_sources'] if s['path'].endswith('psx_wfifo_m10k.vhd')))
    if fifo_fault:
        text = fifo.read_text()
        faulted = fifo_fault(text)
        if faulted == text:
            raise SystemExit(f'fault {name} did not apply')
        fifo.write_text(faulted)
    model = directory / 'psx_m10k_sdp_model.vhd'
    text = RAM_SIM.read_text()
    old = 'MIXED_RDW  : string  := "DONT_CARE"'
    if text.count(old) != 1:
        raise SystemExit('M10K model generic changed')
    model.write_text(text.replace(old, f'MIXED_RDW  : string  := "{model_rdw}"'))
    rtl = UPSTREAM / 'rtl'
    steps = [('mem', UPSTREAM / 'sim/system/src/mem/RamMLAB.vhd'), ('mem', rtl / 'SyncFifoFallThroughMLAB.vhd'),
             ('mem', model), ('mem', fifo)]
    if bench == 'mux':
        text, outputs = mux_bench()
        tb = directory / 'tb_memorymux_fifo_ram.vhd'
        top = 'tb_memorymux_fifo_ram'
    else:
        text, outputs = cpu_bench()
        tb = directory / 'tb_memorymux_fifo_cpu.vhd'
        top = 'tb_memorymux_fifo_cpu'
        cpu_program(1, directory)  # the bench reads rom.hex/ram.hex when elaborated
        steps += [('work', UPSTREAM / 'sim/system/src/mem/dpram.vhd'), ('work', rtl / 'export.vhd'),
                  ('work', rtl / 'divider.vhd'), ('work', rtl / 'datacache.vhd'), ('work', rtl / 'cpu.vhd')]
    tb.write_text(text)
    steps += [('work', rtl / 'memorymux.vhd'), ('cand', Path(patched['path'])), ('work', tb)]
    for library, source in steps:
        result = run([ghdl, '-a', *GHDL_FLAGS, f'--work={library}', str(source)], directory, log)
        if result.returncode:
            raise SystemExit(f'GHDL analysis failed for {source}; log: {log}\n' + result.stdout + result.stderr)
    result = run([ghdl, '-e', *GHDL_FLAGS, top], directory, log)
    if result.returncode:
        raise SystemExit(f'GHDL elaboration failed; log: {log}\n' + result.stdout + result.stderr)
    return directory, top, outputs


def simulate(directory, top, generics, timeout=3600):
    ghdl = find_tool('ghdl')
    command = [ghdl, '-r', *GHDL_FLAGS, top, *(f'-g{k}={v}' for k, v in generics.items()),
               '--assert-level=error', '--ieee-asserts=disable']
    result = run(command, directory, directory / 'checks.log', timeout)
    output = result.stdout + result.stderr
    passes = [line for line in output.splitlines() if 'PASS memorymux' in line]
    stats = [line[line.index('STATS') + 6:] for line in output.splitlines() if 'STATS memorymux' in line]
    failure = next((line for line in output.splitlines() if 'failure' in line or 'error' in line), '')
    ok = result.returncode == 0 and len(passes) == 1
    detail = ('PASS ' if ok else 'FAIL ') + (stats[0] if stats else '')
    if not ok:
        detail += ' | ' + failure.strip()[-200:]
    return ok, detail


# ---------------------------------------------------------------- CPU bench

def cpu_program(seed, directory):
    """Cached MIPS program: loops of store bursts, loads and ALU work."""
    rng = random.Random(seed)
    words = {}
    pc = 0x80010000

    def emit(word):
        nonlocal pc
        words[pc] = word & 0xffffffff
        pc += 4

    def lui_ori(reg, value):
        emit(0x3c000000 | reg << 16 | value >> 16)
        emit(0x34000000 | reg << 21 | reg << 16 | value & 0xffff)

    def itype(op, rs, rt, imm):
        emit(op << 26 | rs << 21 | rt << 16 | imm & 0xffff)

    def rtype(rs, rt, rd, funct, shamt=0):
        emit(rs << 21 | rt << 16 | rd << 11 | shamt << 6 | funct)

    bases = {16: 0xa0100000, 17: 0x00180000, 18: 0x1f801000, 19: 0x1f801c00,
             20: 0x1f000000, 21: 0x1f802000, 22: 0x1f801800, 23: 0xa000ff00}
    for reg, value in bases.items():
        lui_ori(reg, value)
    for reg in range(8, 16):
        lui_ori(reg, rng.getrandbits(32))
    io_offsets = [0x000, 0x004, 0x008, 0x00c, 0x010, 0x014, 0x018, 0x01c, 0x020, 0x040, 0x044, 0x048,
                  0x04c, 0x050, 0x058, 0x060, 0x070, 0x074, 0x080, 0x084, 0x088, 0x0f0, 0x0f4,
                  0x100, 0x104, 0x108, 0x110, 0x114, 0x118, 0x120, 0x124, 0x810, 0x814, 0x820, 0x824]

    def access(store):
        region = rng.choices(['ram', 'kuseg', 'io', 'spu', 'exp1', 'exp2', 'cd'],
                             [40, 15, 15, 15, 4, 6, 5])[0]
        size = rng.choice([4, 4, 2, 1])
        if region in ('ram', 'kuseg'):
            base = 16 if region == 'ram' else 17
            offset = rng.randrange(0, 0x7ff0) & ~(size - 1)
        elif region == 'io':
            base, size, offset = 18, 4, rng.choice(io_offsets)
        elif region == 'spu':
            base, size = 19, rng.choice([2, 2, 4])
            offset = rng.randrange(0, 0x3f0) & ~(size - 1)
        elif region == 'exp1':
            base, offset = 20, rng.randrange(0, 0x100) & ~(size - 1)
        elif region == 'exp2':
            base, offset = 21, rng.randrange(0, 0x80) & ~(size - 1)
        else:
            base, size, offset = 22, 1, rng.randrange(0, 4)
        rt = rng.randrange(8, 16)
        if store:
            op = {4: 0x2b, 2: 0x29, 1: 0x28}[size]
        else:
            op = {4: 0x23, 2: rng.choice([0x21, 0x25]), 1: rng.choice([0x20, 0x24])}[size]
        itype(op, base, rt, offset)

    loops = 0
    while pc < 0x80010000 + 0x2c00:
        loops += 1
        itype(0x09, 0, 24, rng.randrange(2, 7))   # addiu r24, r0, iterations
        top = pc
        for _ in range(rng.randrange(12, 40)):
            choice = rng.random()
            if choice < 0.30:
                for _ in range(rng.randrange(3, 11)):
                    access(True)
            elif choice < 0.45:
                access(True)
            elif choice < 0.62:
                access(False)
            elif choice < 0.80:
                rd, rs, rt = rng.randrange(8, 16), rng.randrange(8, 16), rng.randrange(8, 16)
                rtype(rs, rt, rd, rng.choice([0x21, 0x26, 0x25, 0x24]))
            elif choice < 0.92:
                itype(0x09, rng.randrange(8, 16), rng.randrange(8, 16), rng.getrandbits(16))
            else:
                itype(0x09, rng.choice([16, 17]), rng.choice([16, 17]), 0)  # keep bases, vary timing
        itype(0x09, 16, 16, 0x40)                   # move the RAM window each iteration
        itype(0x09, 24, 24, -1)
        emit(0x14000000 | 24 << 21 | ((top - pc - 4) // 4) & 0xffff)  # bne r24, r0, top
        emit(0)
    lui_ori(8, 0x600df00d)
    itype(0x2b, 23, 8, 0)                           # finish marker store
    emit(0x1000ffff)                                # b .
    emit(0)
    bootstrap = [0x3c1a0001, 0x409a6000] + [0] * 6 + [0x3c088000, 0x24090100]
    bootstrap += [0xad000000, 0x25080010, 0x2529ffff, 0x1520fffc, 0]
    bootstrap += [0x40806000] + [0] * 6 + [0x3c1a8001, 0x03400008, 0]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'rom.hex').write_text(''.join(f'{w:08x}\n' for w in bootstrap))
    (directory / 'ram.hex').write_text(''.join(f'{a & 0x1fffffff:08x} {w:08x}\n' for a, w in sorted(words.items())))
    return len(words) + len(bootstrap), loops


def cpu_bench():
    mux = ports(UPSTREAM / 'rtl/memorymux.vhd', 'memorymux')
    cpu = ports(UPSTREAM / 'rtl/cpu.vhd', 'cpu')
    shared = {'clk1x', 'clk2x', 'clk3x'}
    cpu_inputs = {'ce': 'ce', 'reset': 'reset', 'TURBO': "'0'", 'TURBO_CACHE': "'0'", 'TURBO_CACHE50': "'0'",
                  'irqRequest': "'0'", 'dmaStallCPU': "'0'", 'cpuPaused': 'cpuPaused',
                  'cache_wr': 'cache_wr', 'cache_data': 'cache_data', 'cache_addr': 'cache_addr',
                  'dma_cache_Adr': "(others => '0')", 'dma_cache_data': "(others => '0')",
                  'dma_cache_write': "'0'", 'ram_dataRead': 'ram_data', 'ram_done': 'ram_cpu_done',
                  'gte_busy': "'0'", 'gte_readData': "(others => '0')", 'SS_reset': 'ss_reset',
                  'SS_DataWrite': "(others => '0')", 'SS_Adr': "(others => '0')", 'SS_wren_CPU': "'0'",
                  'SS_wren_SCP': "'0'", 'SS_rden_CPU': "'0'", 'SS_rden_SCP': "'0'", 'debug_firstGTE': "'0'"}
    cpu_from_mux = {'mem_dataRead': 'mem_dataRead', 'mem_done': 'mem_done', 'mem_fifofull': 'mem_fifofull',
                    'mem_tagvalids': 'mem_tagvalids', 'ram_rnw': 'ram_rnw'}
    mux_inputs = {'ce': 'ce', 'reset': 'reset', 'pauseNext': 'pause_next', 'ram_dataRead': 'ram_data',
                  'ram_done': 'ram_cpu_done', 'TURBO': "'0'", 'loading_savestate': "'0'",
                  'SS_reset': "'0'", 'loadExe': "'0'"}
    declarations, instances, compare = [], [], []
    stub_inputs = []
    for name, direction, kind in mux:
        if direction == 'in' and name not in shared and name not in mux_inputs and not name.startswith('mem_in_'):
            stub_inputs.append((name, kind))
            declarations.append(f'   signal {name} : {kind} := {zero(kind)};')
    for side, library in (('ref', 'work'), ('cand', 'cand')):
        cpu_map, mux_map = [], []
        for name, direction, kind in cpu:
            if direction == 'out':
                declarations.append(f'   signal {side}_cpu_{name} : {kind};')
                cpu_map.append(f'{name} => {side}_cpu_{name}')
                compare.append((f'{side}_cpu_{name}', f'cpu {name}', kind))
            elif name in shared:
                cpu_map.append(f'{name} => {name}')
            elif name in cpu_from_mux:
                cpu_map.append(f'{name} => {side}_mux_{cpu_from_mux[name]}')
            else:
                cpu_map.append(f'{name} => {cpu_inputs[name]}')
        for name, direction, kind in mux:
            if direction == 'out':
                declarations.append(f'   signal {side}_mux_{name} : {kind};')
                mux_map.append(f'{name} => {side}_mux_{name}')
                compare.append((f'{side}_mux_{name}', f'memorymux {name}', kind))
            elif name in shared:
                mux_map.append(f'{name} => {name}')
            elif name.startswith('mem_in_'):
                mux_map.append(f'{name} => {side}_cpu_mem_{name[7:]}')
            elif name in mux_inputs:
                mux_map.append(f'{name} => {mux_inputs[name]}')
            else:
                mux_map.append(f'{name} => {name}')
        instances.append(f'   {side}_cpu : entity work.cpu port map (\n      ' + ',\n      '.join(cpu_map) + '\n   );')
        instances.append(f'   {side}_mux : entity {library}.memorymux port map (\n      ' + ',\n      '.join(mux_map) + '\n   );')
    checks = [f'         assert {same(s, s.replace("ref_", "cand_", 1), kind)} report "{what} mismatch" severity failure;'
              for s, what, kind in compare if s.startswith('ref_')]
    observer = ('   process (clk2x, clk3x)\n   begin\n      if rising_edge(clk2x) or rising_edge(clk3x) then\n' +
                '\n'.join(checks) + '\n      end if;\n   end process;\n')
    stubs = '\n'.join(f'         {name} <= {stub_value(name, kind)};' for name, kind in stub_inputs)
    text = (ROOT / 'sim/reference/tb_memorymux_fifo_cpu.vhd').read_text()
    for marker in ('   -- GENERATED_DECLARATIONS\n', '   -- GENERATED_INSTANCES\n', '         -- GENERATED_STUBS\n'):
        if text.count(marker) != 1:
            raise SystemExit('bench marker missing: ' + marker)
    text = text.replace('   -- GENERATED_DECLARATIONS\n', '\n'.join(declarations) + '\n')
    text = text.replace('         -- GENERATED_STUBS\n', stubs + '\n')
    return text.replace('   -- GENERATED_INSTANCES\n', '\n'.join(instances) + '\n\n' + observer), len(checks)


def stub_value(name, kind):
    """Static configuration and per-cycle random bus data for memorymux stubs."""
    width = re.search(r'\((\d+)\s+downto\s+0\)', kind)
    bits = int(width[1]) + 1 if width else 1
    if name.endswith('_dataRead'):
        return f'std_logic_vector(lfsr({bits - 1} downto 0))'
    if name == 'bus_gpu_stall':
        return "lfsr(37)"
    if name.endswith('_memctrl'):
        return 'to_unsigned(16#0213#, 14)'
    if name.endswith('_delay'):
        return 'to_unsigned(3, 4)'
    if name == 'region_in':
        return '"00"'
    if kind.lower() == 'std_logic':
        return "'0'"
    return '(others => \'0\')'


# ---------------------------------------------------------------- driver

FAULTS = {
    # Bypass register removed: a word written on the edge that also reads it
    # comes from the M10K's mixed-port read-during-write.
    'fault-no-bypass': ('BYPASS', lambda t: t.replace(
        "      Dout <= bypass_data when bypass_sel = '1' else ram_q;\n", "      Dout <= ram_q;\n"), 'DONT_CARE'),
    # Same, assuming OLD_DATA read-during-write (wrong mode for this design).
    'fault-no-bypass-old-data': ('BYPASS', lambda t: t.replace(
        "      Dout <= bypass_data when bypass_sel = '1' else ram_q;\n", "      Dout <= ram_q;\n"), 'OLD_DATA'),
    # Read address register samples the current rdcnt, not the post-edge one.
    'fault-late-address': ('BYPASS', lambda t: t.replace(
        '         rdaddress => std_logic_vector(rd_next),\n', '         rdaddress => std_logic_vector(rdcnt),\n'),
        'DONT_CARE'),
    # NearFull one entry late: one more store before the CPU stalls.
    'fault-nearfull-late': ('BYPASS', lambda t: t.replace(
        'if count < NEARFULLDISTANCE then', 'if count < NEARFULLDISTANCE + 1 then'), 'DONT_CARE'),
    # CLK2X design with its read clock on the clk1x edge (one cycle late).
    'fault-clk2x-read-on-clk1x': ('CLK2X', lambda t: t.replace(
        '         rdclock   => clk2x,\n', '         rdclock   => clk,\n').replace(
        'DUAL_CLOCK => true', 'DUAL_CLOCK => false'), 'DONT_CARE'),
}

CPU_FAULTS = ('fault-no-bypass', 'fault-nearfull-late')


def main():
    parser = argparse.ArgumentParser(description='memorymux-fifo-ram lockstep checks')
    parser.add_argument('--cycles', type=int, default=200000, help='clk1x cycles per bench-1 run')
    parser.add_argument('--seeds', type=int, default=3)
    parser.add_argument('--quick', action='store_true', help='fewer cycles and seeds')
    parser.add_argument('--skip-cpu', action='store_true')
    args = parser.parse_args()
    if not find_tool('ghdl'):
        raise SystemExit('GHDL is required (scripts/setup_ghdl.py)')
    check_pin()
    cycles = 40000 if args.quick else args.cycles
    seeds = range(1, (2 if args.quick else args.seeds) + 1)
    failures = []

    for mode, rdw in (('BYPASS', 'DONT_CARE'), ('CLK2X', 'DONT_CARE'), ('BYPASS', 'OLD_DATA')):
        name = f'{mode.lower()}-{rdw.lower()}'
        directory, top, outputs = build_variant(name, mode, model_rdw=rdw)
        print(f'bench 1 {name}: {outputs} memorymux outputs compared per clk2x edge', flush=True)
        for seed in seeds:
            for turbo, aggressive in ((False, False), (True, False), (False, True)):
                ok, line = simulate(directory, top, {'SEED': seed, 'CYCLES': cycles, 'TURBO_ON': str(turbo).lower(),
                                                      'AGGRESSIVE': str(aggressive).lower(), 'LABEL_TEXT': name})
                print('  ' + line, flush=True)
                if not ok:
                    failures.append(f'{name} seed={seed}')
            if rdw == 'OLD_DATA':
                break

    if not args.skip_cpu:
        for mode in ('BYPASS', 'CLK2X'):
            name = f'cpu-{mode.lower()}'
            directory, top, outputs = build_variant(name, mode, bench='cpu')
            print(f'bench 2 {name}: {outputs} CPU+memorymux outputs compared per clk2x/clk3x edge', flush=True)
            for seed in seeds:
                instructions, loops = cpu_program(seed, directory)
                ok, line = simulate(directory, top, {'SEED': seed, 'LABEL_TEXT': name})
                print('  ' + line + f' program_words={instructions} loops={loops}', flush=True)
                if not ok:
                    failures.append(f'{name} seed={seed}')

    for name, (mode, fault, rdw) in FAULTS.items():
        directory, top, _ = build_variant(name, mode, fifo_fault=fault, model_rdw=rdw)
        ok, line = simulate(directory, top, {'SEED': 1, 'CYCLES': 40000, 'TURBO_ON': 'false',
                                             'AGGRESSIVE': 'false', 'LABEL_TEXT': name,
                                             'CHECK_INTERNAL': 'false'})
        if ok:
            print(f'  NOT REJECTED {name}: {line}', flush=True)
            failures.append(name)
        else:
            print(f'  rejected {name}: {line}', flush=True)

    if not args.skip_cpu:
        # Externally visible at the CPU too (all CPU + memorymux ports compared).
        for name in CPU_FAULTS:
            mode, fault, rdw = FAULTS[name]
            directory, top, _ = build_variant('cpu-' + name, mode, fifo_fault=fault, model_rdw=rdw, bench='cpu')
            ok, line = simulate(directory, top, {'SEED': 1, 'LABEL_TEXT': 'cpu-' + name})
            if ok:
                print(f'  NOT REJECTED cpu-{name}: {line}', flush=True)
                failures.append('cpu-' + name)
            else:
                print(f'  rejected cpu-{name}: {line}', flush=True)

    if failures:
        raise SystemExit('memorymux-fifo-ram checks failed: ' + ', '.join(failures))
    print('PASS memorymux-fifo-ram: all lockstep runs matched and all planted faults were rejected')


if __name__ == '__main__':
    main()
