#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""CPU-level test of CPU-assisted save states (scripts/prepare_save_states.py).

sim/reference/tb_save_trap_cpu.vhd runs the MiSTer CPU + memorymux with the
trap and EXP1 transforms, the save handler (rtl/pocket/save_fw) in an EXP1
device model, and a generated game program that mixes all GPRs, HI/LO and a
RAM table while interrupts are enabled, and writes and reads SPU registers
(the bench's SPU model changes its savestate port words and SPU RAM with
each write). Runs:

  reference   no requests
  save        a save mid-run: the game must finish exactly as the reference
  save+load   a save, then a load later: the game resumes at the save point
              and must again finish as the reference (with more log stores)
  bad load    a load with no state in the buffer: FINISH error, game unchanged
  GPU busy    a save before a quiet point: the handler retries, then saves
  never quiet no quiet point at all: RETRY_MAX retries, then FINISH error
              (the bench builds the handler with RETRY_MAX = TEST_RETRY_MAX)

Planted faults that must be caught: the handler forgets one register (or
part of the SPU state, or writes the SPU port before its registers), the
CPU trap vectors to the normal interrupt handler, EXP1 fetch without the
memorymux decode.
"""
import argparse
import random
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_save_fw import image, word_hex
from prepare_save_states import cpu_trap_text, memorymux_exp1_text
from run_memorymux_fifo_ram import ports, zero, stub_value
from tool_paths import ROOT, find_tool

UPSTREAM = ROOT / 'references/PSX_MiSTer'
BUILD = ROOT / 'build/sim/save-trap-cpu'
FW = ROOT / 'rtl/pocket/save_fw/save_handler.S'
GHDL_FLAGS = ['--std=08', '-frelaxed']
TEST_RETRY_MAX = 10     # the handler's 1,500 retries take ~1.5 s; the bench retries every 3,000 cycles
RAM_PAGES = 8           # bench: main RAM 0..16 KiB is the "whole" RAM the handler copies
VRAM_ROWS = 4           # bench: the GPU model's VRAM rows (tb VRAM_ROWS)
SPU_PAGES = 2           # bench: the SPU model's RAM pages (tb SPU_PAGES)
ITERATIONS = 400


def assemble(source, out, ram_pages):
    out.mkdir(parents=True, exist_ok=True)
    (out / 'rom_exp1.hex').write_text(word_hex(image(source, ram_pages, VRAM_ROWS, SPU_PAGES)))


def game_program(seed, directory):
    """Bootstrap (BIOS) sets SR.IEc and jumps to a game at 0x80001000 that mixes
    GPRs 1-25 and 28-31, HI/LO and a 16-word RAM sum table, logs one word per
    iteration at 0x80003000, then dumps the registers and stores the marker."""
    rng = random.Random(seed)
    words = {}
    pc = 0x80001000

    def emit(word):
        nonlocal pc
        words[pc] = word & 0xffffffff
        pc += 4

    def itype(op, rs, rt, imm):
        emit(op << 26 | rs << 21 | rt << 16 | imm & 0xffff)

    def rtype(rs, rt, rd, funct, shamt=0):
        emit(rs << 21 | rt << 16 | rd << 11 | shamt << 6 | funct)

    def li(reg, value):
        emit(0x3c000000 | reg << 16 | value >> 16 & 0xffff)
        emit(0x34000000 | reg << 21 | reg << 16 | value & 0xffff)

    mix = [r for r in range(2, 24)] + [28, 29, 30, 31]    # r1 = counter, r24/r25 temporaries, k0/k1 unused
    for reg in mix:
        li(reg, rng.getrandbits(32))
    li(1, 0)
    # GTE: random control registers (rotation, translation, offsets, H, DQA/DQB)
    for creg in list(range(0, 8)) + [24, 25, 26, 27, 28]:
        li(24, rng.getrandbits(32) & (0xffff if creg == 26 else 0xffffffff))
        emit(0x48C00000 | 24 << 16 | creg << 11)                     # ctc2 t8, creg
    # GPU: random settings (GP0 E1-E6, GP1 03-08) and the GPU IRQ on
    emit(0x3c191f80)                                                     # lui t9, 0x1f80
    gpu_init = [(0x1810, 0xE1000000 | rng.getrandbits(12)), (0x1810, 0xE2000000 | rng.getrandbits(20)),
                (0x1810, 0xE3000000 | rng.getrandbits(19)), (0x1810, 0xE4000000 | rng.getrandbits(19)),
                (0x1810, 0xE5000000 | rng.getrandbits(22)), (0x1810, 0xE6000000 | rng.getrandbits(2)),
                (0x1814, 0x03000000 | rng.getrandbits(1)), (0x1814, 0x04000000 | rng.getrandbits(2)),
                (0x1814, 0x05000000 | rng.getrandbits(19) & ~1), (0x1814, 0x06000000 | rng.getrandbits(24)),
                (0x1814, 0x07000000 | rng.getrandbits(20)), (0x1814, 0x08000000 | rng.getrandbits(8)),
                (0x1810, 0x1F000000)]
    for offset, value in gpu_init:
        li(24, value)
        itype(0x2b, 25, 24, offset)                                      # sw t8, offset(t9)
    top = pc
    for _ in range(3):
        for i, reg in enumerate(mix):
            other = mix[(i + rng.randrange(1, len(mix))) % len(mix)]
            rtype(reg, other, reg, rng.choice([0x21, 0x26, 0x23]))      # addu / xor / subu
            if rng.random() < 0.3:                                       # rotate: no fixed points to settle into
                k = rng.randrange(1, 32)
                rtype(0, reg, 24, 0x00, k)                               # sll t8, reg, k
                rtype(0, reg, reg, 0x02, 32 - k)                         # srl reg, reg, 32-k
                rtype(reg, 24, reg, 0x25)                                # or reg, reg, t8
        rtype(1, mix[0], mix[0], 0x21)                                   # fold in the counter
    a, b = rng.sample(mix, 2)
    rtype(a, b, 0, 0x19)                                                 # multu
    emit(0); emit(0)
    rtype(0, 0, 9, 0x10)                                                 # mfhi r9 (a mix register)
    rtype(0, 0, 10, 0x12)                                                # mflo r10
    rtype(9, 10, 11, 0x26)
    rtype(11, 0, 0, 0x11)                                                # mthi r11: HI carried on
    # GTE: the last RTPS's IR3 into r7 (a load must restore IR1-3 after IRGB),
    # V0 from r2/r3, RTPS, results into r4/r5; IR1 from r6
    emit(0x48000000 | 24 << 16 | 11 << 11)                                # mfc2 t8, IR3
    emit(0)
    rtype(7, 24, 7, 0x26)                                                 # xor r7, r7, t8
    emit(0x48800000 | 2 << 16 | 0 << 11)                                  # mtc2 r2, VXY0
    emit(0x48800000 | 3 << 16 | 1 << 11)                                  # mtc2 r3, VZ0
    emit(0x48800000 | 6 << 16 | 9 << 11)                                  # mtc2 r6, IR1
    emit(0x48400000 | 24 << 16 | 5 << 11)                                 # cfc2 t8, TRX (control state carries on too)
    emit(0)
    rtype(24, 8, 24, 0x21)                                                # addu t8, t8, r8
    emit(0x48C00000 | 24 << 16 | 5 << 11)                                 # ctc2 t8, TRX
    emit(0); emit(0)
    emit(0x4A180001)                                                      # RTPS
    emit(0); emit(0)
    emit(0x48000000 | 4 << 16 | 14 << 11)                                 # mfc2 r4, SXY2
    emit(0x48000000 | 5 << 16 | 25 << 11)                                 # mfc2 r5, MAC1
    emit(0)
    # scratch[r1 & 15] ^= r7   (0x1F800000)
    itype(0x0c, 1, 24, 15)
    rtype(0, 24, 24, 0x00, 2)
    emit(0x3c191f80)                                                     # lui t9, 0x1f80
    rtype(24, 25, 24, 0x21)
    itype(0x23, 24, 25, 0x0000)
    emit(0)
    rtype(25, 7, 25, 0x26)
    itype(0x2b, 24, 25, 0x0000)
    # sum[r1 & 15] += r12   (0x80002000)
    itype(0x0c, 1, 24, 15)                                               # andi t8, r1, 15
    rtype(0, 24, 24, 0x00, 2)                                            # sll t8, t8, 2
    emit(0x3c198000)                                                     # lui t9, 0x8000
    rtype(24, 25, 24, 0x21)
    itype(0x23, 24, 25, 0x2000)                                          # lw t9, 0x2000(t8)
    emit(0)
    rtype(25, 12, 25, 0x21)
    itype(0x2b, 24, 25, 0x2000)                                          # sw t9, 0x2000(t8)
    # log[r1] = r13 ^ r12  (0x80003000)
    rtype(0, 1, 24, 0x00, 2)
    emit(0x3c198000)
    rtype(24, 25, 24, 0x21)
    rtype(13, 12, 25, 0x26)
    itype(0x2b, 24, 25, 0x3000)
    # GPU: two pixels of r14 at (r1, r1 & 3) by GP0 A0, E2 from r15, GP1 05 from r16
    emit(0x3c191f80)                                                     # lui t9, 0x1f80
    emit(0x3c18a000)                                                     # lui t8, 0xa000
    itype(0x2b, 25, 24, 0x1810)
    itype(0x0c, 1, 24, 3)                                                # andi t8, r1, 3
    rtype(0, 24, 24, 0x00, 16)                                           # sll t8, t8, 16
    rtype(24, 1, 24, 0x25)                                               # or t8, t8, r1
    itype(0x2b, 25, 24, 0x1810)
    emit(0x3c180001); emit(0x37180002)                                   # t8 = 1 << 16 | 2
    itype(0x2b, 25, 24, 0x1810)
    itype(0x2b, 25, 14, 0x1810)                                          # sw r14 (the pixels)
    itype(0x0c, 15, 24, 0xffff)
    emit(0x3c19e200)                                                     # lui t9, 0xe200
    rtype(24, 25, 24, 0x25)
    emit(0x3c191f80)
    itype(0x2b, 25, 24, 0x1810)
    itype(0x0c, 16, 24, 0xfffe)
    emit(0x3c190500)                                                     # lui t9, 0x0500
    rtype(24, 25, 24, 0x25)
    emit(0x3c191f80)
    itype(0x2b, 25, 24, 0x1814)
    # SPU: r18 into register 0x1F801C00 + (r17 & 0x3FE); r19 ^= global (r20 & 0x7E)
    emit(0x3c191f80)                                                     # lui t9, 0x1f80
    itype(0x0c, 17, 24, 0x3fe)
    rtype(24, 25, 24, 0x21)
    itype(0x29, 24, 18, 0x1c00)                                          # sh r18, 0x1c00(t8)
    itype(0x0c, 20, 24, 0x7e)
    rtype(24, 25, 24, 0x21)
    itype(0x25, 24, 24, 0x1d80)                                          # lhu t8, 0x1d80(t8)
    emit(0)
    rtype(19, 24, 19, 0x26)
    itype(0x09, 1, 1, 1)                                                 # r1++
    itype(0x0a, 1, 24, ITERATIONS)                                       # slti t8, r1, N
    emit(0x14000000 | 24 << 21 | ((top - pc - 4) // 4) & 0xffff)         # bne t8, r0, top
    emit(0)
    # dump GPRs and HI/LO at 0x80002100, then the marker
    emit(0x3c188000)                                                     # lui t8, 0x8000
    for reg in [1] + mix:
        itype(0x2b, 24, reg, 0x2100 + 4 * reg)
    rtype(0, 0, 25, 0x10)
    itype(0x2b, 24, 25, 0x2180)
    rtype(0, 0, 25, 0x12)
    itype(0x2b, 24, 25, 0x2184)
    # scratchpad table and GTE data/control registers into RAM (0x2200, 0x2300)
    emit(0x3c191f80)                                                     # lui t9, 0x1f80
    for k in range(16):
        itype(0x23, 25, 2, 4 * k); emit(0); itype(0x2b, 24, 2, 0x2200 + 4 * k)
    for g in range(32):
        if g not in (15, 28, 29, 30, 31):        # FIFO alias / derived registers are checked through the others
            emit(0x48000000 | 2 << 16 | g << 11); emit(0); itype(0x2b, 24, 2, 0x2300 + 4 * g)
        emit(0x48400000 | 2 << 16 | g << 11); emit(0); itype(0x2b, 24, 2, 0x2380 + 4 * g)
    li(25, 0x600df00d)
    emit(0x3c18a001)                                                     # lui t8, 0xa001 (0xff00 is -0x100)
    itype(0x2b, 24, 25, 0xff00)
    emit(0x1000ffff)
    emit(0)
    if pc > 0x80002000:
        raise SystemExit('game program too long')
    # BIOS bootstrap: SR = CU2 + IEc (no IRQ mask bits), jump to the game
    bootstrap = [0x3c084000, 0x35080001, 0x40886000, 0, 0, 0x3c1a8000, 0x375a1000, 0x03400008, 0]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'rom.hex').write_text(''.join(f'{w:08x}\n' for w in bootstrap))
    (directory / 'ram.hex').write_text(''.join(f'{a & 0x1fffffff:08x} {w:08x}\n' for a, w in sorted(words.items())))


GTE_INSTANCE = '''   igte : entity work.gte port map (
      clk1x => clk1x, clk2x => clk2x, clk2xIndex => clk2xIndex, ce => ce, reset => reset,
      WIDESCREEN => "00", TURBO => '0',
      gte_busy => gte_busy, gte_readAddr => cpu_gte_readAddr, gte_readData => gte_readData,
      gte_readEna => cpu_gte_readEna, gte_writeAddr_in => cpu_gte_writeAddr, gte_writeData_in => cpu_gte_writeData,
      gte_writeEna_in => cpu_gte_writeEna, gte_cmdData => cpu_gte_cmdData, gte_cmdEna => cpu_gte_cmdEna,
      loading_savestate => '0', SS_reset => ss_reset, SS_DataWrite => (others => '0'), SS_Adr => (others => '0'),
      SS_wren => '0', SS_rden => '0', SS_DataRead => open, SS_idle => open, debug_firstGTE => open
   );
'''


def bench_text():
    mux = ports(UPSTREAM / 'rtl/memorymux.vhd', 'memorymux')
    cpu = ports(UPSTREAM / 'rtl/cpu.vhd', 'cpu')
    mux += [('bus_exp1_addr', 'out', 'unsigned(12 downto 0)'), ('bus_exp1_dataWrite', 'out', 'std_logic_vector(7 downto 0)'),
            ('bus_exp1_write', 'out', 'std_logic')]
    shared = {'clk1x', 'clk2x', 'clk3x'}
    cpu_from_gte = {'gte_busy': 'gte_busy', 'gte_readData': 'gte_readData'}
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
    declarations, stubs = [], []
    cpu_map = ['trapRequest => trap']
    mux_map = []
    # the real memctrl, so the handler's EXP1 delay/size writes take effect
    memctrl = ports(UPSTREAM / 'rtl/memctrl.vhd', 'memctrl')
    memctrl_out = {name for name, direction, _ in memctrl if direction == 'out'}
    memctrl_map = []
    for name, direction, kind in memctrl:
        if direction == 'out':
            target = {'bus_dataRead': 'bus_memc_dataRead', 'bus2_dataRead': 'bus_memc2_dataRead'}.get(name, name)
            if target == name and name not in {n for n, d, _ in mux if d == 'in'}:
                declarations.append(f'   signal memctrl_{name} : {kind};')
                target = f'memctrl_{name}'
            memctrl_map.append(f'{name} => {target}')
        elif name in ('clk1x', 'ce', 'reset'):
            memctrl_map.append(f'{name} => {name}')
        elif name.startswith('bus2_'):
            memctrl_map.append(f'{name} => mux_bus_memc2_{name[5:]}')
        elif name.startswith('bus_'):
            memctrl_map.append(f'{name} => mux_bus_memc_{name[4:]}')
        else:
            memctrl_map.append(f'{name} => {zero(kind)}')
    driven = {'bus_memc_dataRead', 'bus_memc2_dataRead', 'bus_exp1_dataRead', 'bus_gpu_dataRead', 'bus_gpu_stall',
              'bus_spu_dataRead'} | memctrl_out
    for name, direction, kind in mux:
        if direction == 'in' and name not in shared and name not in mux_inputs and not name.startswith('mem_in_'):
            declarations.append(f'   signal {name} : {kind} := {zero(kind)};')
            if name not in driven:
                # other buses read as 0: memorymux ORs their read data together
                value = zero(kind) if name.endswith('_dataRead') else stub_value(name, kind)
                stubs.append(f'         {name} <= {value};')
    for name, direction, kind in cpu:
        if direction == 'out':
            declarations.append(f'   signal cpu_{name} : {kind};')
            cpu_map.append(f'{name} => cpu_{name}')
        elif name in shared:
            cpu_map.append(f'{name} => {name}')
        elif name in cpu_from_mux:
            cpu_map.append(f'{name} => mux_{cpu_from_mux[name]}')
        elif name in cpu_from_gte:
            cpu_map.append(f'{name} => {cpu_from_gte[name]}')
        else:
            cpu_map.append(f'{name} => {cpu_inputs[name]}')
    for name, direction, kind in mux:
        if direction == 'out':
            declarations.append(f'   signal mux_{name} : {kind};')
            mux_map.append(f'{name} => mux_{name}')
        elif name in shared:
            mux_map.append(f'{name} => {name}')
        elif name.startswith('mem_in_'):
            mux_map.append(f'{name} => cpu_mem_{name[7:]}')
        elif name in mux_inputs:
            mux_map.append(f'{name} => {mux_inputs[name]}')
        else:
            mux_map.append(f'{name} => {name}')
    instances = ('   cpu : entity work.cpu port map (\n      ' + ',\n      '.join(cpu_map) + '\n   );\n'
                 '   mux : entity work.memorymux port map (\n      ' + ',\n      '.join(mux_map) + '\n   );\n'
                 '   imemctrl : entity work.memctrl port map (\n      ' + ',\n      '.join(memctrl_map) + '\n   );\n'
                 + GTE_INSTANCE)
    text = (ROOT / 'sim/reference/tb_save_trap_cpu.vhd').read_text()
    for marker in ('   -- GENERATED_DECLARATIONS\n', '   -- GENERATED_INSTANCES\n', '         -- GENERATED_STUBS\n'):
        if text.count(marker) != 1:
            raise SystemExit('bench marker missing: ' + marker)
    text = text.replace('   -- GENERATED_DECLARATIONS\n', '\n'.join(declarations) + '\n')
    text = text.replace('         -- GENERATED_STUBS\n', '\n'.join(stubs) + '\n')
    return text.replace('   -- GENERATED_INSTANCES\n', instances)


def run(command, directory, log, timeout=3600):
    result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=timeout)
    with log.open('a') as handle:
        handle.write('$ ' + ' '.join(map(str, command)) + '\n' + result.stdout + result.stderr + '\n')
    return result


def build(name, cpu_fault=None, mux_fault=None, fw_fault=None, seed=1):
    ghdl = find_tool('ghdl')
    directory = BUILD / name
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    log = directory / 'checks.log'
    log.write_text('')
    rtl = UPSTREAM / 'rtl'
    cpu = cpu_trap_text((rtl / 'cpu.vhd').read_text())
    mux = memorymux_exp1_text((rtl / 'memorymux.vhd').read_text())
    for fault, text in ((cpu_fault, 'cpu'), (mux_fault, 'mux')):
        if fault:
            source = cpu if text == 'cpu' else mux
            changed = source.replace(*fault)
            if changed == source:
                raise SystemExit(f'fault {name} did not apply')
            if text == 'cpu':
                cpu = changed
            else:
                mux = changed
    (directory / 'cpu.vhd').write_text(cpu)
    (directory / 'memorymux.vhd').write_text(mux)
    fw = FW.read_text()
    limit = '        .equ    RETRY_MAX, 1500\n'
    if limit not in fw:
        raise SystemExit('RETRY_MAX not found in the save handler')
    fw = fw.replace(limit, f'        .equ    RETRY_MAX, {TEST_RETRY_MAX}\n')
    for old, new in ([fw_fault] if fw_fault and isinstance(fw_fault[0], str) else fw_fault or []):
        changed = fw.replace(old, new)
        if changed == fw:
            raise SystemExit(f'fault {name} did not apply')
        fw = changed
    (directory / 'save_handler.S').write_text(fw)
    assemble(directory / 'save_handler.S', directory, RAM_PAGES)
    game_program(seed, directory)
    (directory / 'tb_save_trap_cpu.vhd').write_text(bench_text())
    steps = [('mem', UPSTREAM / 'sim/system/src/mem/RamMLAB.vhd'), ('mem', rtl / 'SyncFifoFallThroughMLAB.vhd'),
             ('work', UPSTREAM / 'sim/system/src/mem/dpram.vhd'), ('work', rtl / 'export.vhd'),
             ('work', rtl / 'divider.vhd'), ('work', rtl / 'datacache.vhd'), ('work', rtl / 'memctrl.vhd'),
             ('work', rtl / 'pGTE.vhd'), ('work', rtl / 'gte_mac0.vhd'), ('work', rtl / 'gte_mac123.vhd'), ('work', rtl / 'gte_UNRDivide.vhd'),
             ('work', rtl / 'gte.vhd'),
             ('work', directory / 'cpu.vhd'),
             ('work', directory / 'memorymux.vhd'), ('work', directory / 'tb_save_trap_cpu.vhd')]
    for library, source in steps:
        result = run([ghdl, '-a', *GHDL_FLAGS, f'--work={library}', str(source)], directory, log)
        if result.returncode:
            raise SystemExit(f'GHDL analysis failed for {source}; log: {log}\n' + result.stdout + result.stderr)
    result = run([ghdl, '-e', *GHDL_FLAGS, 'tb_save_trap_cpu'], directory, log)
    if result.returncode:
        raise SystemExit(f'GHDL elaboration failed; log: {log}\n' + result.stdout + result.stderr)
    return directory


def simulate(directory, generics, tag):
    ghdl = find_tool('ghdl')
    dump = directory / 'dump.hex'
    if dump.exists():
        dump.unlink()
    command = [ghdl, '-r', *GHDL_FLAGS, 'tb_save_trap_cpu', *(f'-g{k}={v}' for k, v in generics.items()),
               '--assert-level=error', '--ieee-asserts=disable']
    result = run(command, directory, directory / 'checks.log')
    output = result.stdout + result.stderr
    stats = next((line[line.index('STATS') + 6:] for line in output.splitlines() if 'STATS save trap' in line), '')
    ok = result.returncode == 0 and 'PASS save trap run' in output and dump.exists()
    failure = '' if ok else next((line for line in output.splitlines() if 'failure' in line or 'error' in line), output[-300:])
    values = dict(item.split('=') for item in stats.split() if '=' in item)
    image = dump.read_text() if dump.exists() else None
    if dump.exists():
        shutil.copy(dump, directory / f'dump-{tag}.hex')
    return ok, values, image, failure.strip()


def scenarios(directory, stop_on_failure=False):
    """Run the four scenarios; returns a list of failure strings."""
    failures = []
    ok, ref, ref_image, why = simulate(directory, {}, 'reference')
    if not ok:
        return [f'reference run failed: {why}']
    cycles = int(ref['cycles'])
    print(f'  reference: {ref}', flush=True)
    save_at = cycles // 3
    runs = [
        ('save', {'SAVE_AT': save_at}, dict(saves='1', loads='0'), False),
        ('save+load', {'SAVE_AT': save_at, 'LOAD_AT': None}, dict(saves='1', loads='1'), True),
        ('bad load', {'LOAD_AT': save_at}, dict(saves='0', loads='0', loads_failed='1'), False),
        # a state saved on disc 1 does not load while disc 2 is in the drive
        ('save+load other disc', {'SAVE_AT': save_at, 'LOAD_AT': None, 'LOAD_DISC': 1},
         dict(saves='1', loads='0', loads_failed='1'), False),
        # not at a quiet point: the handler retries until the GPU is idle
        ('save GPU busy', {'SAVE_AT': save_at, 'GPU_HOLD': 8000}, dict(saves='1', loads='0'), False),
        # never a quiet point: RETRY_MAX retries, then the request ends with an error
        ('save never quiet', {'SAVE_AT': save_at, 'GPU_HOLD': 3000 * (TEST_RETRY_MAX + 4)},
         dict(saves='0', saves_failed='1', loads='0', retries=str(TEST_RETRY_MAX)), False),
    ]
    save_cycles = None
    for tag, generics, expect, replays in runs:
        if 'LOAD_AT' in generics and generics['LOAD_AT'] is None:
            if save_cycles is None:
                failures.append(f'{tag}: skipped, the save run failed')
                continue
            # a third of the game after the save's handler has returned
            generics = dict(generics, LOAD_AT=save_at + (save_cycles - cycles) + cycles // 3)
        generics = dict(generics, MAX_CYCLES=cycles * 20)
        ok, values, image, why = simulate(directory, generics, tag.replace(' ', '-').replace('+', '-'))
        line = f'  {tag}: {values}'
        if tag == 'save' and ok:
            save_cycles = int(values['cycles'])
        if not ok:
            failures.append(f'{tag}: {why}')
        elif image != ref_image:
            failures.append(f'{tag}: final RAM differs from the reference')
        elif any(values.get(k) != v for k, v in expect.items()):
            failures.append(f'{tag}: counters {values}, expected {expect}')
        elif replays != (int(values['log_stores']) > int(ref['log_stores'])):
            failures.append(f'{tag}: log stores {values["log_stores"]} vs reference {ref["log_stores"]}')
        elif int(values['handler_fetches']) == 0:
            failures.append(f'{tag}: the handler never ran')
        elif ('GPU_HOLD' in generics) != (int(values.get('retries', 0)) > 0):
            failures.append(f'{tag}: retries {values.get("retries")}')
        print(line + ('' if not failures or not failures[-1].startswith(tag) else '  <- ' + failures[-1]), flush=True)
        if failures and stop_on_failure:
            break
    return failures


FAULTS = {
    # the handler restores every GPR but r13
    'fault-handler-skips-r13': dict(fw_fault=('        \\op     $13, PAGE+4*13($k0)\n', '')),
    # the scratchpad restore stops after 32 bytes (the game's table is 64)
    'fault-handler-short-scratch': dict(fw_fault=('jal     copy_in\n        li      $a1, 0x400', 'jal     copy_in\n        li      $a1, 0x20')),
    # GP1 06 (horizontal display range, only the tap reads it) is not restored
    'fault-handler-skips-gp1-06': dict(fw_fault=('        sw      $t0, 0x1814($t9)        # GP1 06\n', '')),
    # the VRAM upload stops one row short
    'fault-handler-short-vram': dict(fw_fault=('        addu    $s3, $s3, $s2\n7:', '        addu    $s3, $s3, $s2\n        addiu   $s3, $s3, -1\n7:')),
    # GTE control registers 4 and 5 (TRX, which the game changes) are not restored
    'fault-handler-skips-gte-ctrl4': dict(fw_fault=('.irp    n, 0,2,4,', '.irp    n, 0,2,')),
    # IRGB (28) restored after IR1-3, overwriting them
    'fault-handler-gte-irgb-last': dict(fw_fault=[('        GTE_W   28,0,', '        GTE_W   0,'),
                                                   (',27,30\n', ',27,30,28\n')]),
    # SPU: the voice records (port words 384-479) are not kept
    'fault-handler-skips-spu-records': dict(fw_fault=('320, 370, 384, 480, 0, 0', '320, 370, 0, 0')),
    # SPU: the port words are written before the guest register writes
    'fault-handler-spu-port-first': dict(fw_fault=[('        jal     spu_load\n        nop\n', '        jal     spu_load_ss\n        nop\n'),
                                                    ('spu_load:\n', 'spu_load_ss:\n        move    $s7, $ra\n        jal     spu_ss\n        li      $a0, 2\n        move    $ra, $s7\nspu_load:\n'),
                                                    ('        nop\n        li      $a0, 2\n\n', '        nop\n        jr      $ra\n        nop\n\n')]),
    # SPU: the last SPU RAM page is not restored
    'fault-handler-short-spu-ram': dict(fw_fault=('        bne     $s5, $s4, spu_in\n', '        addiu   $t0, $s4, -1\n        bne     $s5, $t0, spu_in\n')),
    # the trap goes to the game's interrupt vector
    'fault-cpu-normal-vector': dict(cpu_fault=('x"BF000000" when (exception > 0 and exceptionTrap', 'x"80000080" when (exception > 0 and exceptionTrap')),
    # memorymux has no EXP1 instruction fetch decode
    'fault-mux-no-exp1-fetch': dict(mux_fault=('(28 downto 23) = "111110") then -- EXP1', '(28 downto 23) = "111111") then -- EXP1')),
    # the EXP1 fetch keeps the data address in the bits it narrowed to 12:0
    'fault-mux-fetch-data-address': dict(mux_fault=('addressData_buf(12 downto 0) <= mem_addressInstr(12 downto 0);',
                                                     'addressData_buf(12 downto 0) <= mem_addressData(12 downto 0);')),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--seeds', type=int, default=1)
    parser.add_argument('--skip-faults', action='store_true')
    parser.add_argument('--fault', action='append', choices=sorted(FAULTS), help='run only these faults (repeatable)')
    args = parser.parse_args()
    if not find_tool('ghdl'):
        raise SystemExit('GHDL is required (scripts/setup_ghdl.py)')
    failures = []
    for seed in range(1, args.seeds + 1):
        directory = build(f'good-seed{seed}', seed=seed)
        print(f'seed {seed}:', flush=True)
        failures += [f'seed {seed} {f}' for f in scenarios(directory)]
    if not args.skip_faults:
        for name, fault in FAULTS.items():
            if args.fault and name not in args.fault:
                continue
            directory = build(name, **fault)
            caught = scenarios(directory, stop_on_failure=True)
            print(f'{name}: {"caught" if caught else "NOT caught"}', flush=True)
            if not caught:
                failures.append(f'{name} was not caught')
    if failures:
        print('FAIL save trap cpu:\n  ' + '\n  '.join(failures))
        sys.exit(1)
    print('PASS save trap cpu')


if __name__ == '__main__':
    main()
