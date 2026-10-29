"""CPU program for the result-select check (prepare_cpu_result_select.py).

cpu_execute_program's arithmetic/shift/overflow prelude, plus the result
sources it does not reach (with ADD/ADDU/SUBU/SLT/SLTU and all six shifts
again, so every result source of the execute stage has checks): AND/OR/XOR/NOR with register and immediate
(ANDI/ORI/XORI, zero-extended) operands, LUI, MULT/MULTU with MFHI/MFLO,
MTHI/MTLO round trips, JAL and BGEZAL links. Then the existing cached
branch/IRQ/COP0/coherency program, unchanged; its exception handler reads
COP0 (Cause, EPC), the COP0 result sources. Every check has an independently computed value.
"""
from pathlib import Path
import random

import cpu_execute_program
from cpu_cache_program import Program, write_program as cached_program

MASK = 0xffffffff
signed = cpu_execute_program.signed


def write_program(directory):
    directory = Path(directory); cached_program(directory)
    ram = {int(a, 16): int(b, 16) for a, b in [r.split() for r in (directory/'ram.hex').read_text().splitlines()]}
    prior = [int(x, 16) for x in (directory/'expected.hex').read_text().splitlines()]
    p = Program(); p.at(0x80018000); p.const(20, 0xa0001000); p.const(22, 0xa000ff00); p.const(23, 0xa0008000)
    census = {}; rng = random.Random(4211)

    def case(word, result, name, dest=10):
        p.emit(word); p.check(dest, result, name); census[name] = census.get(name, 0) + 1
    edges = [0, 1, 2, 0x7fffffff, 0x80000000, 0xffffffff, 0x80000001, 0x7ffffffe, 0x0000ffff, 0xffff0000]
    pairs = [(a, b) for a in edges for b in edges] + [(rng.getrandbits(32), rng.getrandbits(32)) for _ in range(24)]
    for a, b in pairs:
        p.const(8, a); p.const(9, b)
        for fn, name, value in [(0x24, 'AND', a & b), (0x25, 'OR', a | b), (0x26, 'XOR', a ^ b), (0x27, 'NOR', ~(a | b)),
                                (0x21, 'ADDU', a + b), (0x23, 'SUBU', a - b), (0x2a, 'SLT', int(signed(a) < signed(b))),
                                (0x2b, 'SLTU', int(a < b))]:
            case(8 << 21 | 9 << 16 | 10 << 11 | fn, value & MASK, name)
        if -(1 << 31) <= signed(a) + signed(b) < (1 << 31):   # ADD without overflow: the calcResult source
            case(8 << 21 | 9 << 16 | 10 << 11 | 0x20, (a + b) & MASK, 'ADD')
        amount = b & 31   # shifts: the sharedShift source, immediate and register amounts
        for fn, name, value in [(0, 'SLL', a << amount), (2, 'SRL', a >> amount), (3, 'SRA', signed(a) >> amount)]:
            case(8 << 16 | 10 << 11 | amount << 6 | fn, value & MASK, name)
        for fn, name, value in [(4, 'SLLV', a << amount), (6, 'SRLV', a >> amount), (7, 'SRAV', signed(a) >> amount)]:
            case(9 << 21 | 8 << 16 | 10 << 11 | fn, value & MASK, name)
        product = signed(a) * signed(b); uproduct = a * b
        p.emit(8 << 21 | 9 << 16 | 0x18); case(10 << 11 | 0x10, (product >> 32) & MASK, 'MULT/MFHI'); case(10 << 11 | 0x12, product & MASK, 'MULT/MFLO')
        p.emit(8 << 21 | 9 << 16 | 0x19); case(10 << 11 | 0x10, (uproduct >> 32) & MASK, 'MULTU/MFHI'); case(10 << 11 | 0x12, uproduct & MASK, 'MULTU/MFLO')
    for a in edges:
        p.const(8, a)
        p.emit(8 << 21 | 0x11); case(10 << 11 | 0x10, a, 'MTHI/MFHI')
        p.emit(8 << 21 | 0x13); case(10 << 11 | 0x12, a, 'MTLO/MFLO')
        for imm in (0, 1, 0x7fff, 0x8000, 0xffff, rng.getrandbits(16)):
            for op, name, value in [(0x0c, 'ANDI', a & imm), (0x0d, 'ORI', a | imm), (0x0e, 'XORI', a ^ imm)]:
                case(op << 26 | 8 << 21 | 10 << 16 | imm, value, name)
            case(0x0f << 26 | 10 << 16 | imm, imm << 16, 'LUI')
    for n in range(4):
        pc = p.pc; p.jump(f'jal{n}', link=True); p.nop(); p.label(f'jal{n}'); p.check(31, pc + 8, 'JAL link'); census['JAL link'] = census.get('JAL link', 0) + 1
        pc = p.pc; p.branch(0x04000000, 0, 0x11, f'bgezal{n}'); p.nop(); p.label(f'bgezal{n}'); p.check(31, pc + 8, 'BGEZAL link'); census['BGEZAL link'] = census.get('BGEZAL link', 0) + 1
    p.finish_fixups()
    # then the existing cached/IRQ/exception/coherency program, result base past the new checks
    count = len(p.expected); p.const(20, 0xa0001000 + count*4); p.emit(0x08000000 | ((0x80010008 >> 2) & 0x3ffffff)); p.nop()
    assert p.pc < 0x80020000 and count*4 < 0x7000
    for addr, word in p.words.items():
        addr &= 0x1fffffff
        assert addr not in ram
        ram[addr] = word
    ram[0x10000] = 0x08000000 | ((0x80018000 >> 2) & 0x3ffffff); ram[0x10004] = 0
    (directory/'ram.hex').write_text(''.join(f'{a:08x} {v:08x}\n' for a, v in sorted(ram.items())))
    (directory/'expected.hex').write_text(''.join(f'{v & MASK:08x}\n' for v in p.expected + prior))
    return dict(expected_checks=count + len(prior), added_checks=count, existing_checks=len(prior), instruction_census=census, seed=4211)
