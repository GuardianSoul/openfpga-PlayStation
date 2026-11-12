#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Own staged PXS4 CPU fixture for run_save_project_cpu --staged-full-context.

Derived from the project's ss059 staged fixture and full-SPU context model.
The selected-project runner binds the actual CPU/cache/memorymux inputs.
Devices remain models; this is not full-console, electrical or game proof.
"""
import os
import argparse
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_staged_save_fw import image_parts, word_hex
from prepare_save_states import cpu_trap_text, memorymux_exp1_text
import prepare_cpu_exceptions
from run_memorymux_fifo_ram import ports, zero, stub_value
from tool_paths import ROOT, find_tool

UPSTREAM = ROOT / 'references/PSX_MiSTer'
BUILD = ROOT / 'build/sim/staged-save-cpu'
FW = ROOT / 'rtl/pocket/save_fw/staged_handler.S'
GHDL_FLAGS = ['--std=08', '-frelaxed']
TEST_RETRY_MAX = 10     # the handler's 500 retries take ~0.5 s; the bench retries every 3,000 cycles
TEST_QUIET_POLLS = 512  # the handler's GPU/DMA looks per retry (2,048 on hardware): ~6,500 cycles here
# GPU_HOLD lengths (cycles from SAVE_AT; the handler's first look comes ~1,500 cycles after it):
GPU_DRAWING = 4000      # ends inside the first poll window
GPU_BUSY = 30000        # outlasts it: retries
GPU_NEVER = 2 * 30000 * (TEST_RETRY_MAX + 4)   # a retry: 3,000-cycle wait + stub (two I-cache flushes) + poll window
DMA_BUSY = 30000
RAM_PAGES = 8           # bench: main RAM 0..16 KiB is the "whole" RAM the handler copies
VRAM_ROWS = 5           # bench: the GPU model's VRAM rows (tb VRAM_ROWS; row 4 stays zero)
SPU_PAGES = 2           # bench: the SPU model's RAM pages (tb SPU_PAGES)
ITERATIONS = 400


def assemble(source, out, ram_pages):
    out.mkdir(parents=True, exist_ok=True)
    rom, stage2 = image_parts(source, ram_pages, VRAM_ROWS, SPU_PAGES)
    (out / 'rom_exp1.hex').write_text(word_hex(rom))
    # STAGE2_WARM runs: stage 2 already unpacked at STAGE2 (as after an earlier trap)
    (out / 'stage2.hex').write_text(word_hex(stage2 + bytes(-len(stage2) % 4)))


def base_game_program(seed, directory):
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


def game_program(seed, directory):
    base_game_program(seed, directory)
    from prepare_save_entry_stress import kernel_guest
    kernel_guest(directory)
    # Read and change writable COP0 debug context on every guest iteration.
    # A load that keeps post-save debug values must change the final guest
    # checksum, even if later guest code writes those registers again.
    ram = directory / 'ram.hex'
    words = {int(a, 16): int(w, 16) for a, w in (line.split() for line in ram.read_text().splitlines())}
    branches = [(a, w) for a, w in words.items() if w & 0xffff0000 == 0x17000000 and w & 0x8000]
    if len(branches) != 1 or any(0x900 <= a < 0xa00 or 0xf80 <= a < 0x1000 for a in words):
        raise ValueError('Debug-context guest witness layout changed')
    a, branch = branches[0]
    loop = a + 4 + ((branch & 0xffff) - 65536) * 4
    original = [words[loop], words[loop + 4]]
    words[loop], words[loop + 4] = 0x08000240, 0   # J 0x80000900, no link-register side effect
    prefix, probe = [], []
    for reg, value in ((3, 0x00100000), (5, 0x10123456), (7, 3), (9, 0x00ffffff), (11, 0x00fffff0)):
        prefix += [0x3c180000 | (value >> 16), 0x37180000 | (value & 0xffff), 0x40980000 | (reg << 11)]
        probe += [0x40180000 | (reg << 11), 0,
                  (29 << 21) | (24 << 16) | (29 << 11) | 0x26,
                  0x27180001, 0x40980000 | (reg << 11)]
    # The kernel prefix's original jump to the game now initializes COP0.
    if words.get(0xf10) != 0x08000400:
        raise ValueError('Kernel prefix jump changed')
    words[0xf10] = 0x080003e0   # J 0x80000f80
    prefix += [0x08000400, 0]
    probe += original + [0x08000000 | ((loop + 8) // 4), 0]
    for base, data in ((0x900, probe), (0xf80, prefix)):
        for i, word in enumerate(data):
            words[base + 4 * i] = word
    ram.write_text(''.join(f'{a:08x} {w:08x}\n' for a, w in sorted(words.items())))


# The selected memorymux has the save-mode-clk1x port (prepare_save_mode_clk1x.py): the bench's
# SAVE_MODE level goes into it, and its simulation-only check watches every RAM access.
SAVE_MODE_PORT = False


def base_bench_text():
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
    if SAVE_MODE_PORT:
        mux.append(('save_mode', 'in', 'std_logic'))
        mux_inputs['save_mode'] = 'save_mode_s'
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
    driven = {'bus_dma_dataRead', 'bus_memc_dataRead', 'bus_memc2_dataRead', 'bus_exp1_dataRead', 'bus_gpu_dataRead', 'bus_gpu_stall',
              'bus_spu_dataRead', 'bus_cd_dataRead', 'bus_pad_dataRead'} | memctrl_out
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
    text = (ROOT / 'sim/reference/tb_staged_save_cpu.vhd').read_text()
    for marker in ('   -- GENERATED_DECLARATIONS\n', '   -- GENERATED_INSTANCES\n', '         -- GENERATED_STUBS\n'):
        if text.count(marker) != 1:
            raise SystemExit('bench marker missing: ' + marker)
    text = text.replace('   -- GENERATED_DECLARATIONS\n', '\n'.join(declarations) + '\n')
    text = text.replace('         -- GENERATED_STUBS\n', '\n'.join(stubs) + '\n')
    return text.replace('   -- GENERATED_INSTANCES\n', instances)


def bench_text():
    from prepare_save_entry_stress import kernel_bench
    return kernel_bench(base_bench_text())


def run(command, directory, log, timeout=int(__import__("os").environ.get("SAVE_SIM_TIMEOUT", "3600"))):
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
    # as the release recipe builds it: the accuracy step (fetch bus errors) before the trap
    cpu = cpu_trap_text(prepare_cpu_exceptions.transform((rtl / 'cpu.vhd').read_text()))
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
    fw, n = re.subn(r'^        \.equ    RETRY_MAX, \d+', f'        .equ    RETRY_MAX, {TEST_RETRY_MAX}', fw, flags=re.M)
    if n != 1:
        raise SystemExit('RETRY_MAX not found in the save handler')
    fw, n = re.subn(r'^        \.equ    QUIET_POLLS, \d+', f'        .equ    QUIET_POLLS, {TEST_QUIET_POLLS}', fw, flags=re.M)
    if n != 1:
        raise SystemExit('QUIET_POLLS not found in the save handler')
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


def scenarios(directory, stop_on_failure=False, first=(), only=()):
    """Run the scenarios (those named in first before the others); returns a list of failure strings."""
    failures = []
    ok, ref, ref_image, why = simulate(directory, {}, 'reference')
    if not ok:
        return [f'reference run failed: {why}']
    cycles = int(ref['cycles'])
    print(f'  reference: {ref}', flush=True)
    save_at = cycles // 3
    runs = [
        ('save', {'SAVE_AT': save_at}, dict(saves='1', loads='0'), False),
        # stage 2 already in SDRAM (no unpacking): the save's own cost, and the base for "GPU drawing"
        ('save warm', {'SAVE_AT': save_at, 'STAGE2_WARM': 'true'}, dict(saves='1', loads='0'), False),
        ('save+load', {'SAVE_AT': save_at, 'LOAD_AT': None}, dict(saves='1', loads='1', spu_rekeys='0'), True),
        # a wake from Sleep: the load finds the drive fresh (motor off, idle, settings reset)
        ('save+load fresh drive', {'SAVE_AT': save_at, 'LOAD_AT': None, 'CD_FRESH': 'true'},
         dict(saves='1', loads='1', spu_rekeys='0'), True),
        # a wake from Sleep while the BIOS still waits for its last CD command: INT3 unacked, INT2
        # queued behind it; the load must take neither as an answer and leave nothing for the game
        ('save+load stray CD response', {'SAVE_AT': save_at, 'LOAD_AT': None, 'CD_FRESH': 'true', 'CD_STRAY': 'true'},
         dict(saves='1', loads='1', spu_rekeys='0'), True),
        # A restarted core: the state is in the save buffer (savebuf.hex, the plain save's pages) and the
        # only request of the run is a load, with the game a third of its run past the saved point.
        # Staging page, scratch pages and device registers are as at power-on; cold: the load unpacks stage 2.
        ('load first cold', {'LOAD_AT': save_at + cycles // 3, 'PLANT_BUF': 'true', 'PROGRESS': 50000},
         dict(saves='0', loads='1', loads_failed='0', spu_rekeys='0'), True),
        ('load first warm', {'LOAD_AT': save_at + cycles // 3, 'PLANT_BUF': 'true', 'PROGRESS': 50000, 'STAGE2_WARM': 'true'},
         dict(saves='0', loads='1', loads_failed='0', spu_rekeys='0'), True),
        ('bad load', {'LOAD_AT': save_at}, dict(saves='0', loads='0', loads_failed='1'), False),
        # a state saved on disc 1 does not load while disc 2 is in the drive
        ('save+load other disc', {'SAVE_AT': save_at, 'LOAD_AT': None, 'LOAD_DISC': 1},
         dict(saves='1', loads='0', loads_failed='1'), False),
        # not at a quiet point: the handler retries until the GPU is idle
        ('save GPU busy', {'SAVE_AT': save_at, 'GPU_HOLD': GPU_BUSY, 'STAGE2_WARM': 'true'}, dict(saves='1', loads='0'), False),
        # the GPU drawing for less than the handler's poll window: it waits (no retry beyond the plain save's)
        ('save GPU drawing', {'SAVE_AT': save_at, 'GPU_HOLD': GPU_DRAWING, 'STAGE2_WARM': 'true'}, dict(saves='1', loads='0'), False),
        # GPUSTAT 27 set throughout (VRAM data the game never reads): no obstacle to a save
        ('save VRAM data waiting', {'SAVE_AT': save_at, 'VRAM_SEND': 'true'}, dict(saves='1', saves_failed='0', loads='0'), False),
        # a response byte the game leaves unread: saved from retry RETRY_MAX/2 on, drained by the load
        ('save+load unread CD response', {'SAVE_AT': save_at, 'LOAD_AT': None, 'CD_UNREAD': 'true'},
         dict(saves='1', loads='1', spu_rekeys='0'), True),
        # the game left 2,340 bytes (a whole raw sector: both FIFO pages) in the data FIFO: the
        # save keeps them and gives them back, the load puts them in an emptied FIFO
        ('save+load CD data left', {'SAVE_AT': save_at, 'LOAD_AT': None, 'CD_LEFT': 2340},
         dict(saves='1', loads='1', spu_rekeys='0'), True),
        # never a quiet point: RETRY_MAX retries, then the request ends with an error
        # (a retry takes the 3,000-cycle wait plus the handler's own run, ~7,000 in all)
        ('save never quiet', {'SAVE_AT': save_at, 'GPU_HOLD': GPU_NEVER, 'STAGE2_WARM': 'true'},
         dict(saves='0', saves_failed='1', loads='0', retries=str(TEST_RETRY_MAX)), False),
        # a memory card session (the BIOS between two bytes): the save waits for its end
        ('save card session', {'SAVE_AT': save_at, 'PAD_SELECT': 30000, 'STAGE2_WARM': 'true'},
         dict(saves='1', saves_failed='0', loads='0'), False),
        # an FMV: MDEC in/out and CD DMA busy, retries with the CPU stalled in bursts
        ('save DMA busy', {'SAVE_AT': save_at, 'DMA_HOLD': DMA_BUSY, 'STAGE2_WARM': 'true'}, dict(saves='1', saves_failed='0', loads='0'), False),
        ('save DMA never quiet', {'SAVE_AT': save_at, 'DMA_HOLD': 2000000, 'STAGE2_WARM': 'true'},
         dict(saves='0', saves_failed='1', loads='0', retries=str(TEST_RETRY_MAX)), False),
    ]
    save_cycles = save_retries = None
    # save and save warm stay first: the others' loads and comparisons depend on them
    runs = runs[:2] + [r for r in runs[2:] if r[0] in first] + [r for r in runs[2:] if r[0] not in first]
    if only:
        runs = runs[:2] + [r for r in runs[2:] if r[0] in only]
    # A second machine running the same project in the opposite order (SAVE_CYCLES_HINT = the plain
    # save's cycle count, from an earlier run of the same CPU path): the selected scenarios first,
    # then save warm; the plain save is the other machine's. Bench, generics and checks are the same.
    if os.environ.get('SAVE_CYCLES_HINT'):
        save_cycles = int(os.environ['SAVE_CYCLES_HINT'])
        runs = [r for r in runs[2:] if not only or r[0] in only] + [runs[1]]
    # the load-first scenarios take the plain save's buffer: that save writes it (same run otherwise)
    if any('PLANT_BUF' in r[1] for r in runs):
        if runs[0][0] != 'save':
            return ['load first: needs the plain save of the same run for its buffer']
        runs[0] = (runs[0][0], dict(runs[0][1], DUMP_BUF='true'), *runs[0][2:])
    for tag, generics, expect, replays in runs:
        if 'LOAD_AT' in generics and generics['LOAD_AT'] is None:
            if save_cycles is None:
                failures.append(f'{tag}: skipped, the save run failed')
                continue
            # a third of the game after the plain save's handler has returned; the CD scenarios'
            # saves take longer (the retries, the FIFO bytes): their load follows their own save
            if 'CD_LEFT' in generics or 'CD_UNREAD' in generics:
                generics = {k: v for k, v in generics.items() if k != 'LOAD_AT'}
                generics['LOAD_DELAY'] = cycles // 3
            else:
                generics = dict(generics, LOAD_AT=save_at + (save_cycles - cycles) + cycles // 3)
        # (moving 2,340 FIFO bytes out, back and in again takes about 1.5M cycles more)
        generics = dict(generics, MAX_CYCLES=cycles * (40 if 'CD_LEFT' in generics else 20))
        ok, values, image, why = simulate(directory, generics, tag.replace(' ', '-').replace('+', '-'))
        line = f'  {tag}: {values}'
        if tag == 'save' and ok:
            save_cycles = int(values['cycles'])
        if tag == 'save warm' and ok:
            save_retries = values.get('retries')
        if not ok:
            failures.append(f'{tag}: {why}')
        elif image != ref_image:
            failures.append(f'{tag}: final RAM differs from the reference')
        elif any(values.get(k) != v for k, v in expect.items()):
            failures.append(f'{tag}: counters {values}, expected {expect}')
        elif replays != (int(values['spu_writes']) > int(ref['spu_writes'])):
            failures.append(f'{tag}: guest SPU writes {values["spu_writes"]} vs reference {ref["spu_writes"]}')
        elif int(values['handler_fetches']) == 0:
            failures.append(f'{tag}: the handler never ran')
        elif tag == 'save GPU drawing' and values.get('retries') != save_retries:
            failures.append(f'{tag}: retries {values.get("retries")}, the warm plain save {save_retries}')
        elif tag == 'save+load unread CD response' and int(values.get('retries', 0)) < TEST_RETRY_MAX // 2:
            failures.append(f'{tag}: retries {values.get("retries")}, below RETRY_MAX/2')
        # a plain save may retry too: its first try can land in the CD model's busy window
        elif (tag != 'save GPU drawing' and ('GPU_HOLD' in generics or 'DMA_HOLD' in generics or 'PAD_SELECT' in generics)
              and int(values.get('retries', 0)) == 0):
            failures.append(f'{tag}: retries {values.get("retries")}')
        elif 'DMA_HOLD' in generics and int(values.get('dma_bursts', 0)) == 0:
            failures.append(f'{tag}: no DMA burst ran')
        print(line + ('' if not failures or not failures[-1].startswith(tag) else '  <- ' + failures[-1]), flush=True)
        if failures and stop_on_failure:
            break
    return failures




if __name__ == "__main__":
    raise SystemExit("Use run_save_project_cpu.py --staged-full-context with a source-bound project")
