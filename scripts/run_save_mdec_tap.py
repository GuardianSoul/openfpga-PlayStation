#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Benches for prepare_save_mdec_tap.py (docs/save-states-finish/mdec-tap.md).

PROJECT is a frozen project with the save states (its mdec.vhd, chroma ROM
and psx_save_exp1.sv are read, nothing in it is written).

  step1     the project's MDEC as it is (GHDL, sim/reference/tb_mdec_tap.vhd):
            a decode after configuration with the tables never written, DMA-out
            reads with the output FIFO empty, the same decode with tables, the
            routine's load sequence with the tables kept and on a fresh MDEC;
            with --state, the frame a saved state's game last sent.
  routine   the step's save and load code alone on the MIPS model: idle MDEC,
            busy MDEC (no tap, no flag), a state from before the step.
  module    the MDEC with the tap: tables written, read through the tap and
            compared, the same stream decoded before and after the tap; then a
            fresh MDEC (zero tables), the routine's own save and load code
            (assembled from the routine text, run by the small MIPS model
            below against the tap bytes of the first run) and the stream again:
            the output must equal the first run's word for word.
  lockstep  the MDEC with the tap (tap off) against the project's MDEC in
            sim/reference/tb_mdec_idct_pipe.vhd: every port output on every
            clk2x cycle.
  device    the project's psx_save_exp1.sv with the step (Icarus): SPU_HOLD
            bit 1, the two tap read addresses, the byte selection.
  faults    every HW_FAULTS and FAULTS entry of the step must be caught by one
            of the above; the result names the bench that caught it.
Usage: run_save_mdec_tap.py PROJECT [--only step1,module,routine,lockstep,device,faults] [--state FILE.sta ...] [--output DIR]
"""
import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_save_mdec_tap as step
import run_mdec_reference as reference
from prepare_dpram_sdp import primitive
from run_mdec_idct_mac_lockstep import MODEL as MAC_MODEL
from run_mdec_idct_pipe import vectors as lockstep_vectors
from tool_paths import ROOT, find_tool

UPSTREAM = ROOT / 'references/PSX_MiSTer'
TB = ROOT / 'sim/reference/tb_mdec_tap.vhd'
LOCKSTEP_TB = ROOT / 'sim/reference/tb_mdec_idct_pipe.vhd'
OPTS = ['--std=08', '-frelaxed', '-fsynopsys']
WORDS = {0: 8, 1: 16, 2: 192, 3: 128}


def project_file(project, name):
    rows = [Path(r['path']) for r in json.loads((project / 'fit-inputs.json').read_text())['quartus_sources'] if Path(r['path']).name == name]
    if len(rows) != 1:
        raise SystemExit(f'{project} does not compile exactly one {name}')
    return rows[0]


# ---------------------------------------------------------------------------
# GHDL
# ---------------------------------------------------------------------------
def build(directory, project, units, top):
    """units: (entity name or None, VHDL text) analysed in order after the libraries; top: (tb text, entity)."""
    ghdl = find_tool('ghdl'); lib = Path(find_tool('quartus_sh')).parents[1] / 'eda/sim_lib'
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    log = (directory / 'build.log').open('w')
    def call(*a): subprocess.run([ghdl, *a], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
    call('-a', *OPTS, '--work=altera_mf', str(lib / 'altera_mf_components.vhd'), str(lib / 'altera_mf.vhd'))
    mem = UPSTREAM / 'sim/system/src/mem'
    for library in ('mem', 'work'):
        call('-a', *OPTS, f'--work={library}', str(mem / 'dpram.vhd'))
    for name in ('RamMLAB.vhd', 'SyncFifo.vhd', 'SyncFifoFallThrough.vhd'):
        source = mem / name
        call('-a', *OPTS, '--work=mem', str(source if source.exists() else UPSTREAM / 'rtl' / name))
    pinned = (UPSTREAM / 'rtl/dpram.vhd').read_text()
    for name, reverse in (('pocket_dpram_sdp', False), ('pocket_dpram_sdp_reverse', True)):
        path = directory / f'{name}.vhd'; path.write_text(primitive(pinned, reverse)); call('-a', *OPTS, str(path))
    (directory / 'mac-model.vhd').write_text(MAC_MODEL); call('-a', *OPTS, 'mac-model.vhd')
    chroma = project_file(project, 'psx_mdec_chroma_rom.vhd')
    for name in ('psx_mdec_chroma_rom.vhd', 'psx_mdec_chroma_cr.mif', 'psx_mdec_chroma_cb.mif'):
        (directory / name).write_bytes((chroma.parent / name).read_bytes())
    call('-a', *OPTS, 'psx_mdec_chroma_rom.vhd')
    for name, text in units:
        (directory / f'{name}.vhd').write_text(text); call('-a', *OPTS, f'{name}.vhd')
    text, entity = top
    (directory / f'{entity}.vhd').write_text(text); call('-a', *OPTS, f'{entity}.vhd')
    (directory / 'mdec-tap.txt').write_text('W 00 00000001\n')
    (directory / 'mdec-random.txt').write_text('B 04 60000000\n')
    call('-e', *OPTS, entity)


def tap_bench(directory, project, mdec_text, tap):
    tb = TB.read_text()
    if not tap:
        tb = ''.join(line for line in tb.splitlines(True) if '-- TAP' not in line)
    build(directory, project, [('mdec', mdec_text)], (tb, 'tb_mdec_tap'))


def run_vectors(directory, name, lines):
    path = directory / f'{name}.txt'; out = directory / f'{name}-out.txt'
    path.write_text('\n'.join(lines) + '\n')
    r = subprocess.run(['nice', '-n', '10', find_tool('ghdl'), '-r', *OPTS, 'tb_mdec_tap', f'-gVECTOR_FILE={path}', f'-gOUT_FILE={out}',
                        '--assert-level=error', '--ieee-asserts=disable-at-0'], cwd=directory, capture_output=True, text=True, timeout=3600)
    text = r.stdout + r.stderr
    (directory / f'{name}.log').write_text(text)
    rows = [l.split() for l in out.read_text().splitlines()] if out.exists() else []
    return r.returncode == 0 and 'PASS MDEC tap bench run' in text, rows, text


# ---------------------------------------------------------------------------
# stimulus
# ---------------------------------------------------------------------------
def tables(seed):
    """(command lines, quantisation bytes 0-127, scale halfwords 0-63, model)"""
    rng = random.Random(seed)
    quant = [rng.getrandbits(32) & 0x7F7F7F7F | 0x01010101 for _ in range(32)]
    scale = reference.standard_scale() if seed % 2 == 0 else [rng.randrange(-32768, 32768) for _ in range(64)]
    data = [(scale[i] & 0xFFFF) | ((scale[i + 1] & 0xFFFF) << 16) for i in range(0, 64, 2)]
    model = reference.Model('mednafen'); model.set_quant(quant); model.set_scale(data)
    lines = ['B 00 40000001'] + [f'D 00 {w:08x}' for w in quant] + ['B 00 60000000'] + [f'D 00 {w:08x}' for w in data]
    raw = b''.join(w.to_bytes(4, 'little') for w in quant)
    return lines, list(raw), [s & 0xFFFF for s in scale], model


def stream(seed, model=None, plans=((3, 0, 1), (2, 0, 0), (1, 0, 0), (3, 1, 0)), count=2, read='R'):
    """Decode commands as a game sends them (command word by register, data by DMA), each followed by
    the read of its output. Returns (lines, words expected by the Mednafen model or None, words total)."""
    rng = random.Random(1000 + seed)
    lines, expected = [], []
    for depth, signed, bit15 in plans:
        blocks = 6 if depth >= 2 else 1
        halfwords = [h for _ in range(count * blocks) for h in reference.block(rng, rng.choice(('small', 'normal')))]
        if len(halfwords) % 2:
            halfwords.append(0xFE00)
        data = [halfwords[i] | (halfwords[i + 1] << 16) for i in range(0, len(halfwords), 2)]
        lines.append(f'B 00 {0x20000000 | depth << 27 | signed << 26 | bit15 << 25 | len(data):08x}')
        lines += [f'D 00 {w:08x}' for w in data]
        lines.append(f'{read} 00 {count * WORDS[depth]:08x}')
        if model:
            queue = list(halfwords)
            for _ in range(count):
                expected += model.pack(model.macroblock(queue, depth, signed), depth, bit15, signed)
    total = sum(count * WORDS[d] for d, _, _ in plans)
    return lines, (expected if model else None), total


def words(rows, tag='O'):
    return [int(r[1], 16) for r in rows if r[0] == tag]


def histogram(values, top=4):
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return ', '.join(f'{v:08x} x{n}' for v, n in sorted(counts.items(), key=lambda kv: -kv[1])[:top]) + (' ...' if len(counts) > top else '')


# ---------------------------------------------------------------------------
# the routine's own code on a small MIPS I model
# ---------------------------------------------------------------------------
class Stop(Exception):
    pass


class Mips:
    """MIPS I subset with branch delay slots and load delays (a loaded value is not there for the next
    instruction). bus: read(address, size) / write(address, size, value)."""
    def __init__(self, code, base, bus):
        self.code, self.base, self.bus = code, base, bus
        self.r = [0] * 32
        self.steps = 0

    def run(self, pc, limit=2_000_000):
        r, pending, npc = self.r, None, pc + 4
        s16 = lambda v: v - 0x10000 if v & 0x8000 else v
        s32 = lambda v: v - (1 << 32) if v >> 31 else v
        while True:
            self.steps += 1
            if self.steps > limit:
                raise Stop('step limit')
            w = int.from_bytes(self.code[pc - self.base:pc - self.base + 4], 'little')
            op, rs, rt, rd, sh, fn, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, (w >> 6) & 31, w & 63, w & 0xFFFF
            dest = value = load = branch = None
            if op == 0:
                if fn == 13: return                                     # break: the end of the block
                a, b = r[rs], r[rt]
                table = {0: lambda: b << sh, 2: lambda: b >> sh, 3: lambda: s32(b) >> sh, 33: lambda: a + b, 32: lambda: a + b, 35: lambda: a - b,
                         36: lambda: a & b, 37: lambda: a | b, 38: lambda: a ^ b, 39: lambda: ~(a | b), 42: lambda: int(s32(a) < s32(b)), 43: lambda: int(a < b)}
                if fn == 8: branch = r[rs]
                elif fn in table: dest, value = rd, table[fn]()
                else: raise Stop(f'special {fn} at {pc:08x}')
            elif op == 1:
                if rt not in (0, 1): raise Stop(f'regimm {rt}')
                if (s32(r[rs]) >= 0) == (rt == 1): branch = pc + 4 + (s16(imm) << 2)
            elif op in (2, 3):
                branch = (pc & 0xF0000000) | ((w & 0x3FFFFFF) << 2)
                if op == 3: dest, value = 31, pc + 8
            elif op in (4, 5, 6, 7):
                taken = {4: r[rs] == r[rt], 5: r[rs] != r[rt], 6: s32(r[rs]) <= 0, 7: s32(r[rs]) > 0}[op]
                if taken: branch = pc + 4 + (s16(imm) << 2)
            elif op in (8, 9): dest, value = rt, r[rs] + s16(imm)
            elif op == 10: dest, value = rt, int(s32(r[rs]) < s16(imm))
            elif op == 11: dest, value = rt, int(r[rs] < (s16(imm) & 0xFFFFFFFF))
            elif op == 12: dest, value = rt, r[rs] & imm
            elif op == 13: dest, value = rt, r[rs] | imm
            elif op == 14: dest, value = rt, r[rs] ^ imm
            elif op == 15: dest, value = rt, imm << 16
            elif op in (32, 33, 35, 36, 37):
                size = {32: 1, 33: 2, 35: 4, 36: 1, 37: 2}[op]
                v = self.bus.read((r[rs] + s16(imm)) & 0xFFFFFFFF, size)
                if op == 32 and v & 0x80: v |= 0xFFFFFF00
                if op == 33 and v & 0x8000: v |= 0xFFFF0000
                load = (rt, v)
            elif op in (40, 41, 43):
                size = {40: 1, 41: 2, 43: 4}[op]
                self.bus.write((r[rs] + s16(imm)) & 0xFFFFFFFF, size, r[rt] & ((1 << 8 * size) - 1))
            else:
                raise Stop(f'opcode {op} at {pc:08x}')
            if pending and dest != pending[0]:                           # the load before this instruction lands now
                r[pending[0]] = pending[1] & 0xFFFFFFFF
            pending = load
            if dest: r[dest] = value & 0xFFFFFFFF
            r[0] = 0
            pc, npc = npc, (branch if branch is not None else npc + 4)


def run_block(code, base, entry, bus, regs):
    m = Mips(code, base, bus)
    for k, v in regs.items():
        m.r[k] = v
    m.run(entry)
    return m


def routine_blocks(text):
    """The step's save block, mdec_quiet and the R1 MDEC load block, cut out of the routine text and
    assembled with the routine's own equates. Returns (code, base, {label: address})."""
    equates = ''.join(l + '\n' for l in text.splitlines() if re.match(r'\s+\.equ\s', l))
    def cut(start, end, inclusive=True):
        a = text.index(start); b = text.index(end, a)
        if text.count(start) != 1:
            raise ValueError(f'block start not unique: {start[:50]!r}')
        return text[a:b + (len(end) if inclusive else 0)]
    # the table read as the routine reaches it: straight after its mdec_quiet call (t2 = 0, the second
    # half of the retries, so a busy MDEC gets here too); then save:'s flag block
    save = ('        jal     mdec_quiet\n        move    $t2, $zero\n' +
            cut("        # mdec-tap: the MDEC's quantisation and scale tables", '62:\n') +
            cut('        # mdec-tap: bit 19 of the stored MDEC status', '65:\n'))
    quiet = cut('mdec_quiet:\n', '81:     jr      $ra\n        move    $v0, $t2\n')
    load = cut('        lw      $t3, R1_MDEC($k0)\n', '        jal     load_start', inclusive=False)
    source = ('        .set    noreorder\n        .set    noat\n        .text\n' + equates +
              '        .globl  entry\nentry:\nblock_save:\n' + save + '        break\n' + quiet + 'block_load:\n' + load + '        break\n')
    link = 'SECTIONS { .text 0x80200000 : { *(.text) } /DISCARD/ : { *(.MIPS.abiflags) *(.reginfo) *(.comment) } }\n'
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'b.S').write_text(source); (tmp / 'b.ld').write_text(link)
        subprocess.run([find_tool('llvm-mc') or 'llvm-mc', '-triple=mipsel', '-mcpu=mips1', '-filetype=obj', str(tmp / 'b.S'), '-o', str(tmp / 'b.o')], check=True)
        subprocess.run([find_tool('ld.lld') or 'ld.lld', '-m', 'elf32ltsmip', '-T', str(tmp / 'b.ld'), '-e', 'entry', '--no-dynamic-linker', '-o', str(tmp / 'b.elf'), str(tmp / 'b.o')], check=True)
        subprocess.run([find_tool('llvm-objcopy') or 'llvm-objcopy', '-O', 'binary', '-j', '.text', str(tmp / 'b.elf'), str(tmp / 'b.bin')], check=True)
        nm = subprocess.run([find_tool('llvm-nm') or 'llvm-nm', str(tmp / 'b.elf')], check=True, capture_output=True, text=True).stdout
        code = (tmp / 'b.bin').read_bytes()
    symbols = {l.split()[2]: int(l.split()[0], 16) for l in nm.splitlines() if len(l.split()) == 3}
    return code, 0x80200000, symbols


EXP1 = 0xBF000000


class Machine:
    """What the two blocks touch: the staging page, the save device's SS_ADR / SPU_HOLD / tap reads
    (the device bench checks the same byte selection on the real psx_save_exp1) and the MDEC's two
    registers (status as given; writes recorded in order)."""
    def __init__(self, quant, scale, status, page=None):
        self.quant, self.scale, self.status = quant, scale, status
        self.page = bytearray(page) if page else bytearray(random.Random(7).randbytes(0x800))   # a save finds leftovers
        self.adr = self.hold = 0
        self.mdec_writes, self.tap_reads_off, self.status_reads = [], 0, 0

    def read(self, address, size):
        if size > 1:
            if address == 0x1F801824:
                self.status_reads += 1
                return self.status
            return sum(self.read(address + i, 1) << 8 * i for i in range(size))
        offset = address - EXP1
        if 0x1000 <= offset < 0x1800:
            return self.page[offset - 0x1000]
        if offset in (0x1824, 0x1825):
            adr = self.adr & 127
            if not self.hold & 2:                 # tap off: the decoder's own idle read address (entry 0), not the asked one
                self.tap_reads_off += 1; adr = 0
            return self.quant[adr] if offset == 0x1824 else (self.scale[adr >> 1] >> (8 * (adr & 1))) & 0xFF
        raise Stop(f'read of {address:08x}')

    def write(self, address, size, value):
        if address in (0x1F801820, 0x1F801824) and size == 4:
            self.mdec_writes.append((address & 0xF, value)); return
        offset = address - EXP1
        if size > 1:
            for i in range(size):
                self.write(address + i, 1, (value >> 8 * i) & 0xFF)
        elif 0x1000 <= offset < 0x1800: self.page[offset - 0x1000] = value
        elif offset == 0x1820: self.adr = value
        elif offset == 0x180C: self.hold = value
        else: raise Stop(f'write of {address:08x}')


def routine_round_trip(text, quant, scale, status, busy=False):
    """Save block on (quant, scale) -> page -> load block. Returns (MDEC writes of the load, page, save machine)."""
    code, base, sym = routine_blocks(text)
    save = Machine(quant, scale, (status & ~0xE0000000) | 0x20000000 if busy else status)
    r1 = 4 * 59
    save.page[r1:r1 + 4] = (status & ~(1 << 19)).to_bytes(4, 'little')      # the R1 store before the block
    run_block(code, base, sym['block_save'], save, {26: EXP1, 25: 0x1F800000})
    load = Machine([0] * 128, [0] * 64, 0, page=save.page)
    run_block(code, base, sym['block_load'], load, {26: EXP1, 25: 0x1F800000})
    return load.mdec_writes, bytes(save.page), save


# ---------------------------------------------------------------------------
# benches
# ---------------------------------------------------------------------------
def state_frame(path):
    """The decode command the game of a saved state last sent to the MDEC: (command word, data words),
    found in the state's main RAM as the block that ends at DMA 0's address. None if there is none."""
    import struct
    data = Path(path).read_bytes()
    data = data[data.index(b'XS') - 1:]                 # past the Pocket's header: page 0 starts with the magic
    page0, page1 = data[:2048], data[2048:4096]
    ram, stored = bytearray(2048 * 1024), 2
    for page in range(2, 2 + 1024):                     # page 0 words 64-127: bit p = logical page p is all zero, not stored
        if not page0[256 + (page >> 3)] >> (page & 7) & 1:
            ram[(page - 2) * 2048:(page - 1) * 2048] = data[stored * 2048:(stored + 1) * 2048]; stored += 1
    madr = struct.unpack_from('<I', page1, 0x510)[0] & 0x1FFFFC
    status = struct.unpack_from('<I', page0, 4 * 59)[0]
    for start in range(madr - 4, max(madr - 4 * 0x10000, 0), -4):
        word = struct.unpack_from('<I', ram, start)[0]
        if word >> 29 == 1 and start + 4 + 4 * (word & 0xFFFF) == madr:
            return word, [struct.unpack_from('<I', ram, start + 4 + 4 * i)[0] for i in range(word & 0xFFFF)], status
    return None


def step1_state(d, path):
    """A real frame (a saved state's last MDEC input) through the MDEC with the tables never written."""
    frame = state_frame(path)
    if not frame:
        return dict(state=Path(path).name, frame=None)
    word, data, status = frame
    per = WORDS[(word >> 27) & 3]
    lines = ['B 04 60000000', f'B 00 {word:08x}'] + [f'I 00 {w:08x}' for w in data] + [f'Q 00 {400 * 192:08x}', 'S 00 00000000', 'F 00 00000002']
    ok, rows, _ = run_vectors(d, 'state-' + re.sub(r'[^0-9A-Za-z]+', '-', Path(path).stem)[:40], lines)
    got = words(rows)
    return dict(state=Path(path).name, saved_mdec_status=f'{status:08x}', command=f'{word:08x}', input_words=len(data), bench_ok=ok,
                words_out=len(got), macroblocks=len(got) / per, values=histogram(got), status_after=[r[1] for r in rows if r[0] == 'S'],
                empty_reads=[(r[1], r[2]) for r in rows if r[0] == 'F'])


def step1(project, out, states=()):
    """The project's MDEC, unchanged."""
    mdec = project_file(project, 'mdec.vhd').read_text()
    d = out / 'step1'
    tap_bench(d, project, mdec, tap=False)
    result = {}
    table_lines, _, _, model = tables(0)
    plans = ((3, 0, 1), (2, 0, 0))
    lines, expected, total = stream(0, model, plans)
    # (1) never written: decode, read what is requested, then four reads with nothing requested
    ok, rows, text = run_vectors(d, 'fresh', ['B 04 60000000', 'S 00 00000000'] + stream(0, None, plans, read='Q')[0] + ['W 00 00002000', 'S 00 00000000', 'F 00 00000004', 'S 00 00000000'])
    got = words(rows)
    result['fresh'] = dict(bench_ok=ok, words_requested=total, words_out=len(got), by_depth={
        'depth3 (15-bit)': histogram(got[:2 * WORDS[3]]), 'depth2 (24-bit)': histogram(got[2 * WORDS[3]:])},
        status=[r[1] for r in rows if r[0] == 'S'], empty_reads=[(r[1], r[2]) for r in rows if r[0] == 'F'], q=[' '.join(r) for r in rows if r[0] == 'Q'])
    # (2) tables first
    ok2, rows2, _ = run_vectors(d, 'tables', ['B 04 60000000'] + table_lines + lines + ['S 00 00000000', 'F 00 00000004'])
    got2 = words(rows2)
    result['tables'] = dict(bench_ok=ok2, words_out=len(got2), equal_mednafen_model=got2 == expected, distinct_words=len(set(got2)),
                            empty_reads=[(r[1], r[2]) for r in rows2 if r[0] == 'F'])
    # (3) the routine's load sequence between two frames of an FMV, tables kept (the game already running):
    #     reset, R1 decode command, enables (no table commands), then the next frame
    r1 = ['B 04 80000000', 'B 00 36000001', 'B 00 fe00fe00', 'B 04 60000000']
    next_lines, next_expected, _ = stream(1, model, plans)
    ok3, rows3, _ = run_vectors(d, 'running-load', ['B 04 60000000'] + table_lines + lines + r1 + ['S 00 00000000'] + next_lines)
    got3 = words(rows3)
    result['load_with_tables_kept'] = dict(bench_ok=ok3, next_frame_equal_model=got3[len(expected):] == next_expected,
                                           status_after_r1=[r[1] for r in rows3 if r[0] == 'S'])
    # (4) the same on a fresh MDEC (wake from Sleep): every following frame
    ok4, rows4, _ = run_vectors(d, 'wake-load', r1 + ['S 00 00000000'] + stream(1, None, plans, read='Q')[0] + stream(2, None, plans, read='Q')[0])
    got4 = words(rows4)
    result['load_on_fresh_mdec'] = dict(bench_ok=ok4, words_out=len(got4), histogram=histogram(got4), q=[' '.join(r) for r in rows4 if r[0] == 'Q'])
    result['saved_state_frames'] = [step1_state(d, s) for s in states]
    return result


def module_bench(project, out, name='module', mdec_fault=None, routine_fault=None, seeds=(0, 1)):
    """Bench (a). Returns (passed, details)."""
    plain = project_file(project, 'mdec.vhd').read_text()
    mdec = step.mdec(plain)
    if mdec_fault:
        mdec = step.hw_fault('mdec', mdec, mdec_fault)
    from prepare_save_final_routine import tail_text      # SAVE_FINAL_ROUTINE=f|g: the blocks of the final routine
    routine = step.firmware_text(step.big_build_text())
    if routine_fault:
        routine = step.fault_text(routine, routine_fault)
    routine = tail_text(routine, 'mdec-tap')
    d = out / name
    tap_bench(d, project, mdec, tap=True)
    details, passed = [], True
    for seed in seeds:
        table_lines, quant, scale, model = tables(seed)
        lines, expected, total = stream(seed, model)
        taps = ['M 00 00000001'] + [f'T {i:02x} 00000000' for i in range(128)] + ['M 00 00000000']
        ok, rows, text = run_vectors(d, f'first-{seed}', ['B 04 60000000'] + table_lines + lines + ['S 00 00000000'] + taps + lines + ['S 00 00000000'])
        got = words(rows)
        a, a2 = got[:total], got[total:]
        tap = {int(r[1], 16): (int(r[2], 16), int(r[3], 16)) for r in rows if r[0] == 'T'}
        tap_quant = [tap[i][0] for i in range(128)] if len(tap) == 128 else []
        tap_scale = [tap[2 * i][1] for i in range(64)] if len(tap) == 128 else []
        status = [int(r[1], 16) for r in rows if r[0] == 'S']
        row = dict(seed=seed, first_ok=ok, words=len(a), equal_mednafen_model=a == expected,
                   tap_quant_equal=tap_quant == quant, tap_scale_equal=tap_scale == scale,
                   tap_scale_same_on_odd_address=all(tap[2 * i][1] == tap[2 * i + 1][1] for i in range(64)) if tap else False,
                   decode_after_tap_equal=a2 == a and len(a) == total, status_idle=[f'{s:08x}' for s in status])
        # the routine: save block on the tap's bytes, load block on its page
        try:
            writes, page, machine = routine_round_trip(routine, tap_quant, tap_scale, status[0])
            row.update(flag=bool(page[4 * 59 + 2] & 8), tap_reads_with_tap_off=machine.tap_reads_off, save_status_reads=machine.status_reads,
                       tap_left_on=bool(machine.hold & 2), spu_hold_kept=not machine.hold & 1,   # the SPU is not held yet at the table read: bit 0 stays 0
                       load_mdec_writes=len(writes))
        except (Stop, IndexError, KeyError, ValueError) as error:
            writes, row['routine_error'] = [], str(error)
        replay = [f'B {a:02x} {v:08x}' for a, v in writes]
        zero_lines = stream(seed, None, read='Q')[0]
        ok2, rows2, _ = run_vectors(d, f'second-{seed}', ['B 04 60000000'] + zero_lines + replay + ['S 00 00000000'] + stream(seed, None, read='Q')[0] + ['S 00 00000000'])
        got2 = words(rows2)
        z, b = got2[:total], got2[total:]
        status2 = [int(r[1], 16) for r in rows2 if r[0] == 'S']
        row.update(second_ok=ok2, zero_table_words=len(z), zero_table_histogram=histogram(z), zero_tables_differ=z != a,
                   after_load_equal=b == a and len(b) == total, after_load_words=len(b),
                   status_after_load_equal=bool(status2) and status2[0] == status[0], status_after_load=[f'{s:08x}' for s in status2])
        checks = dict(first_run=ok, second_run=ok2, decode_equals_mednafen_model=row['equal_mednafen_model'],
                      tap_quant_equal=row['tap_quant_equal'], tap_scale_equal=row['tap_scale_equal'],
                      tap_scale_same_on_odd_address=row['tap_scale_same_on_odd_address'], decode_after_tap_equal=row['decode_after_tap_equal'],
                      routine_ran='routine_error' not in row, flag_set=bool(row.get('flag')), no_tap_read_with_tap_off=row.get('tap_reads_with_tap_off') == 0,
                      tap_switched_off=row.get('tap_left_on') is False, spu_hold_kept=bool(row.get('spu_hold_kept')),
                      zero_tables_differ=row['zero_tables_differ'], after_load_equal=row['after_load_equal'],
                      status_after_load_equal=row['status_after_load_equal'],
                      # cold start: tables never written by a guest, only by the routine's load; against the reference decoder
                      cold_start_equals_model=b == expected)
        row['failed'] = [k for k, v in checks.items() if not v]
        row['passed'] = not row['failed']; passed = passed and row['passed']
        details.append(row)
    return passed, details


def routine_cases(out):
    """The routine blocks alone: a busy MDEC is not tapped, a state without the flag sends no tables."""
    from prepare_save_final_routine import tail_text
    routine = tail_text(step.firmware_text(step.big_build_text()), 'mdec-tap')
    _, quant, scale, _ = tables(5)
    status = 0x9684FFFF
    rows = {}
    writes, page, machine = routine_round_trip(routine, quant, scale, status)
    rows['idle'] = dict(flag=bool(page[4 * 59 + 2] & 8), tables_in_page=page[0x200:0x280] == bytes(quant) and
                        page[0x280:0x300] == b''.join(s.to_bytes(2, 'little') for s in scale),
                        mdec_writes=len(writes), first_writes=[f'{a:x}:{v:08x}' for a, v in writes[:2]], last_writes=[f'{a:x}:{v:08x}' for a, v in writes[-3:]],
                        looks=machine.status_reads)
    before = bytes(random.Random(7).randbytes(0x800))
    writes, page, machine = routine_round_trip(routine, quant, scale, status, busy=True)
    rows['busy'] = dict(flag=bool(page[4 * 59 + 2] & 8), page_words_128_191_untouched=page[0x200:0x300] == before[0x200:0x300],
                        tap_switched=machine.hold != 0, mdec_writes=len(writes), writes=[f'{a:x}:{v:08x}' for a, v in writes])
    old = step.big_build_text()
    code, base, sym = routine_blocks(routine)
    load = Machine([0] * 128, [0] * 64, 0)
    load.page[4 * 59:4 * 59 + 4] = status.to_bytes(4, 'little')     # a state of the routine before the step
    run_block(code, base, sym['block_load'], load, {26: EXP1, 25: 0x1F800000})
    rows['old_state'] = dict(mdec_writes=len(load.mdec_writes), writes=[f'{a:x}:{v:08x}' for a, v in load.mdec_writes])
    ok = (rows['idle']['flag'] and rows['idle']['tables_in_page'] and rows['idle']['mdec_writes'] == 70 and not rows['busy']['flag']
          and rows['busy']['page_words_128_191_untouched'] and not rows['busy']['tap_switched'] and rows['busy']['mdec_writes'] == 4
          and rows['old_state']['mdec_writes'] == 4 and rows['busy']['writes'] == rows['old_state']['writes'])
    return ok, rows


def lockstep(project, out, name='lockstep', mdec_fault=None, seeds=(1, 2)):
    """Bench (b): tap off, every port output on every clk2x cycle against the project's MDEC."""
    plain = project_file(project, 'mdec.vhd').read_text()
    mdec = step.mdec(plain)
    if mdec_fault:
        mdec = step.hw_fault('mdec', mdec, mdec_fault)
    d = out / name
    build(d, project, [('mdec', plain), ('mdec_pipe', re.sub(r'\bmdec\b', 'mdec_pipe', mdec))], (LOCKSTEP_TB.read_text(), 'tb_mdec_idct_pipe'))
    rows, passed = [], True
    for seed in seeds:
        v = d / f'vectors-{seed}.txt'; lockstep_vectors(v, seed)
        r = subprocess.run(['nice', '-n', '10', find_tool('ghdl'), '-r', *OPTS, 'tb_mdec_idct_pipe', f'-gVECTOR_FILE={v}', '--assert-level=error',
                            '--ieee-asserts=disable-at-0'], cwd=d, capture_output=True, text=True, timeout=3600)
        text = r.stdout + r.stderr; (d / f'seed-{seed}.log').write_text(text)
        ok = r.returncode == 0 and 'PASS MDEC IDCT pipeline lockstep' in text
        tail = (re.search(r'PASS MDEC IDCT pipeline lockstep.*|MDEC lockstep mismatch.*', text) or [text.strip()[-200:]])[0]
        rows.append(dict(seed=seed, passed=ok, result=tail)); passed = passed and ok
    return passed, rows


DEVICE_TB = r'''
`timescale 1ns/1ps
module tb_save_mdec_tap;
    reg clk2x = 0, clk1x = 0, clk_74a = 0, reset = 1;
    always #5 clk2x = !clk2x;
    always @(posedge clk2x) clk1x <= !clk1x;
    always #7 clk_74a = !clk_74a;
    reg [12:0] addr = 0; reg wr = 0; reg [7:0] wdata = 0; wire [7:0] rdata;
    wire mdec_tap, spu_hold; wire [8:0] ss_adr;
    reg [7:0] quant [0:127]; reg [15:0] scale [0:63];
    // the MDEC side as mdec.vhd gives it: both table RAMs registered on the tap address
    reg [7:0] yuv_q = 0; reg [15:0] scale_q = 0;
    always @(posedge clk1x) yuv_q <= mdec_tap ? quant[ss_adr[6:0]] : 8'hEE;
    always @(posedge clk2x) scale_q <= mdec_tap ? scale[ss_adr[6:1]] : 16'hDDCC;
    psx_save_exp1 dut (.clk2x(clk2x), .reset_2x(reset), .bus_addr(addr), .bus_write(wr), .bus_dataWrite(wdata), .bus_dataRead(rdata),
        .timer_flags(12'd0), .gpu_ranges(63'd0), .clk1x(clk1x), .spu_hold(spu_hold), .spu_quiet(1'b1), .spu_ss_adr(ss_adr), .spu_ss_rdata(32'd0),
        .spuram_rdata(16'd0), .spuram_done(1'b0), .mdec_tap(mdec_tap), .mdec_q({scale_q, yuv_q}),
        .aux_dout(32'd0), .aux_done(1'b0), .up_req(1'b0), .up_we(1'b0), .up_addr(21'd0), .up_din(32'd0),
        .clk_74a(clk_74a), .savestate_start(1'b0), .savestate_load(1'b0), .disc(3'd0), .disc_settled(1'b1), .disc_wrong(1'b0),
        .core_held(1'b0), .cd_state(64'd0));
    integer errors = 0, checks = 0, i;
    task bus_write(input [12:0] a, input [7:0] d); begin
        @(posedge clk1x); #1 addr = a; wdata = d; wr = 1; @(posedge clk1x); #1 wr = 0; @(posedge clk1x);
    end endtask
    // a read as memorymux does it: the address, the data two clk1x later (the routine's reads are slower)
    task bus_read(input [12:0] a, output [7:0] d); begin
        @(posedge clk1x); #1 addr = a; @(posedge clk1x); @(posedge clk1x); #1 d = rdata;
    end endtask
    task check_eq(input [7:0] got, input [7:0] want, input [255:0] what); begin
        checks = checks + 1;
        if (got !== want) begin errors = errors + 1; if (errors < 10) $display("ERROR %0s: got %02x want %02x (i=%0d)", what, got, want, i); end
    end endtask
    reg [7:0] q, s;
    initial begin
        for (i = 0; i < 128; i = i + 1) quant[i] = (i * 37 + 11) & 8'hFF;
        for (i = 0; i < 64; i = i + 1) scale[i] = (i * 16'h1357 + 16'h2468) & 16'hFFFF;
        repeat (8) @(posedge clk1x); reset = 0; repeat (4) @(posedge clk1x);
        i = 0;
        if (mdec_tap !== 1'b0 || spu_hold !== 1'b0) begin errors = errors + 1; $display("ERROR tap or hold set after reset"); end
        bus_write(13'h180C, 8'd1);
        if (mdec_tap !== 1'b0 || spu_hold !== 1'b1) begin errors = errors + 1; $display("ERROR SPU_HOLD 1: tap %b hold %b", mdec_tap, spu_hold); end
        bus_write(13'h1820, 8'd5); bus_read(13'h1824, q); check_eq(q, 8'hEE, "tap off quant");
        bus_write(13'h180C, 8'd3);
        if (mdec_tap !== 1'b1 || spu_hold !== 1'b1) begin errors = errors + 1; $display("ERROR SPU_HOLD 3: tap %b hold %b", mdec_tap, spu_hold); end
        // the routine's order: the next address is written, three bus accesses later it is read
        bus_write(13'h1820, 8'd128);
        for (i = 128; i >= 0; i = i - 1) begin
            bus_read(13'h1824, q); bus_read(13'h1825, s);
            bus_write(13'h1820, (i - 1) & 8'hFF);
            check_eq(q, quant[i & 127], "quant byte");
            check_eq(s, i[0] ? scale[(i & 127) >> 1][15:8] : scale[(i & 127) >> 1][7:0], "scale byte");
        end
        bus_read(13'h1822, q); check_eq(q, 8'd0, "SS busy read");
        bus_read(13'h1823, q); check_eq(q, 8'd1, "DISC_SET read");
        bus_read(13'h180C, q); check_eq(q, 8'd1, "SPU quiet read");
        bus_write(13'h180C, 8'd1);
        if (mdec_tap !== 1'b0 || spu_hold !== 1'b1) begin errors = errors + 1; $display("ERROR tap off: tap %b hold %b", mdec_tap, spu_hold); end
        bus_write(13'h180C, 8'd2);
        if (mdec_tap !== 1'b1 || spu_hold !== 1'b0) begin errors = errors + 1; $display("ERROR SPU_HOLD 2: tap %b hold %b", mdec_tap, spu_hold); end
        reset = 1; repeat (4) @(posedge clk1x);
        if (mdec_tap !== 1'b0) begin errors = errors + 1; $display("ERROR tap survives reset"); end
        if (errors == 0) $display("PASS save mdec tap device checks=%0d", checks);
        else $display("FAIL save mdec tap device errors=%0d checks=%0d", errors, checks);
        $finish;
    end
endmodule
'''


def device(project, out, name='device', fault=None):
    """The project's psx_save_exp1 with the step; then the repository's device bench on old and new."""
    old = project_file(project, 'psx_save_exp1.sv').read_text()
    new = step.exp1(old)
    if fault:
        new = step.hw_fault('exp1', new, fault)
    d = out / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    tb = DEVICE_TB
    if 'core_reset' in new:                    # save-core-reset (E3): its edge must clear the tap too
        tb = tb.replace('.core_held(1\'b0),', '.core_held(1\'b0), .core_reset(core_reset),').replace(
            'reg [7:0] q, s;', 'reg [7:0] q, s; reg core_reset = 0;').replace(
            "        reset = 1; repeat (4) @(posedge clk1x);\n", "        core_reset = 1; repeat (4) @(posedge clk1x); core_reset = 0;\n"
            "        if (mdec_tap !== 1'b0) begin errors = errors + 1; $display(\"ERROR tap survives the core reset\"); end\n"
            "        bus_write(13'h180C, 8'd2); reset = 1; repeat (4) @(posedge clk1x);\n")
        assert 'core_reset = 1' in tb and '.core_reset(core_reset)' in tb
    (d / 'psx_save_exp1.sv').write_text(new); (d / 'tb.sv').write_text(tb)
    models = ROOT / 'sim/reference/mister_memory_models.sv'
    subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_save_mdec_tap', '-o', str(d / 'tb.vvp'), str(d / 'tb.sv'), str(models), str(d / 'psx_save_exp1.sv')], check=True)
    r = subprocess.run([find_tool('vvp'), '-n', 'tb.vvp'], cwd=d, capture_output=True, text=True, timeout=600)
    (d / 'run.log').write_text(r.stdout + r.stderr)
    line = (re.search(r'(PASS|FAIL) save mdec tap device.*', r.stdout) or ['no result: ' + (r.stdout + r.stderr)[-200:]])[0]
    first = (re.search(r'ERROR[^\n]*', r.stdout) or [''])[0]
    info = dict(result=line, first_error=first)
    ok = 'PASS save mdec tap device' in r.stdout
    if not fault:
        # the repository's device bench (page copies, SPU RAM pages, card accesses) on the project's device, old and new
        import run_save_sp_far_edge as far_edge
        from build_save_fw import rom40_from_byte_hex
        far_edge.OUT.mkdir(parents=True, exist_ok=True)
        rng = random.Random(0x5A7E)
        (far_edge.OUT / 'tb_save_exp1_rom.hex').write_text(''.join(f'{rng.randrange(256):02x}\n' for _ in range(4096)))
        (far_edge.OUT / 'tb_save_exp1_rom2.hex').write_text(''.join(f'{rng.randrange(256):02x}\n' for _ in range(1024)))
        (far_edge.OUT / 'tb_save_exp1_rom40.hex').write_text(rom40_from_byte_hex(far_edge.OUT / 'tb_save_exp1_rom.hex', far_edge.OUT / 'tb_save_exp1_rom2.hex'))
        for label, text in (('old', old), ('new', new)):
            log = far_edge.run(f'mdec-tap-{label}', text, False)
            info[f'tb_save_exp1_{label}'] = (re.search(r'PASS save exp1.*', log) or ['FAIL'])[0] + ' | ' + (re.search(r'aux accesses.*', log) or [''])[0]
            ok = ok and 'PASS save exp1' in log
        ok = ok and info['tb_save_exp1_old'] == info['tb_save_exp1_new']
    return ok, info


def faults(project, out):
    rows, all_caught = {}, True
    for name, (kind, _, _) in step.HW_FAULTS.items():
        caught_by = []
        if kind == 'exp1':
            ok, info = device(project, out, f'fault-{name}', fault=name)
            if not ok: caught_by.append('device: ' + info['first_error'][:80])
        else:
            ok, info = lockstep(project, out, f'fault-{name}-lockstep', mdec_fault=name, seeds=(1,))
            if not ok: caught_by.append('lockstep: ' + info[0]['result'][:80])
            ok, info = module_bench(project, out, f'fault-{name}-module', mdec_fault=name, seeds=(1,))
            if not ok: caught_by.append('module: ' + ', '.join(info[0]['failed']))
        rows[name] = dict(where=kind, caught=bool(caught_by), by=caught_by); all_caught = all_caught and bool(caught_by)
        print(f'fault {name} ({kind}):', 'caught by ' + ' | '.join(caught_by) if caught_by else 'NOT DETECTED', flush=True)
    for name in step.FAULTS:
        ok, info = module_bench(project, out, f'fault-{name}-module', routine_fault=name, seeds=(1,))
        by = [] if ok else ['module: ' + ', '.join(info[0]['failed'])]
        if name == 'save-tap-off' and info[0].get('tap_reads_with_tap_off'):
            by.append(f"{info[0]['tap_reads_with_tap_off']} tap reads with the tap off")
        rows[name] = dict(where='routine', caught=bool(by), by=by); all_caught = all_caught and bool(by)
        print(f'fault {name} (routine):', 'caught by ' + ' | '.join(by) if by else 'NOT DETECTED', flush=True)
    return all_caught, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project', type=Path)
    ap.add_argument('--only', default='step1,module,routine,lockstep,device,faults')
    ap.add_argument('--output', type=Path, default=ROOT / 'build/sim/save-mdec-tap')
    ap.add_argument('--state', type=Path, action='append', default=[], help='a saved state (.sta): step1 decodes its last MDEC frame with empty tables')
    args = ap.parse_args()
    project, out = args.project.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report, failures = {}, []
    only = args.only.split(',')
    if 'step1' in only:
        report['step1'] = step1(project, out, args.state)
        print('step1:', json.dumps(report['step1'], indent=1), flush=True)
    if 'module' in only:
        ok, report['module'] = module_bench(project, out)
        print('module:', 'PASS' if ok else 'FAIL', json.dumps(report['module'], indent=1), flush=True)
        if not ok: failures.append('module')
    if 'routine' in only:
        ok, report['routine'] = routine_cases(out)
        print('routine:', 'PASS' if ok else 'FAIL', json.dumps(report['routine'], indent=1), flush=True)
        if not ok: failures.append('routine')
    if 'lockstep' in only:
        ok, report['lockstep'] = lockstep(project, out)
        print('lockstep:', 'PASS' if ok else 'FAIL', json.dumps(report['lockstep'], indent=1), flush=True)
        if not ok: failures.append('lockstep')
    if 'device' in only:
        ok, report['device'] = device(project, out)
        print('device:', 'PASS' if ok else 'FAIL', report['device'], flush=True)
        if not ok: failures.append('device')
    if 'faults' in only:
        ok, report['faults'] = faults(project, out)
        if not ok: failures.append('faults')
    name = 'report.json' if set(only) >= {'step1', 'module', 'routine', 'lockstep', 'device', 'faults'} else 'report-' + '-'.join(only) + '.json'
    (out / name).write_text(json.dumps(report, indent=2) + '\n')
    if failures:
        raise SystemExit('save mdec tap check failed: ' + ', '.join(failures))
    print(f'PASS save mdec tap ({", ".join(only)}); report {out / name}')


if __name__ == '__main__':
    main()
