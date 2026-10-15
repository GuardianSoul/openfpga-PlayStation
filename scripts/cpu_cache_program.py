"""Authored cached MIPS program; no BIOS or game assets.

Result stores have independently assigned expected values. Cache coherency
checks deliberately distinguish uncached code writes from isolated invalidation.
"""
from pathlib import Path


class Program:
    def __init__(self):
        self.pc = 0x80010000
        self.words = {}
        self.labels = {}
        self.fixups = []
        self.expected = []
        self.checks = []

    def emit(self, word):
        if self.pc in self.words:
            raise ValueError(f"overlapping diagnostic code: {self.pc:08x}")
        self.words[self.pc] = word & 0xffffffff
        self.pc += 4

    def at(self, address):
        self.pc = address

    def label(self, name):
        if name in self.labels:
            raise ValueError(name)
        self.labels[name] = self.pc

    def const(self, reg, value):
        self.emit(0x3c000000 | reg << 16 | value >> 16)
        self.emit(0x34000000 | reg << 21 | reg << 16 | value & 0xffff)

    def addiu(self, rt, rs, value):
        self.emit(0x24000000 | rs << 21 | rt << 16 | value & 0xffff)

    def sw(self, rt, base, offset=0):
        self.emit(0xac000000 | base << 21 | rt << 16 | offset & 0xffff)

    def lw(self, rt, base, offset=0):
        self.emit(0x8c000000 | base << 21 | rt << 16 | offset & 0xffff)

    def branch(self, opcode, rs, rt, label):
        self.fixups.append((self.pc, label, 'branch'))
        self.emit(opcode | rs << 21 | rt << 16)

    def jump(self, label, link=False):
        self.fixups.append((self.pc, label, 'jump'))
        self.emit(0x0c000000 if link else 0x08000000)

    def jr(self, rs):
        self.emit(rs << 21 | 8)

    def nop(self, count=1):
        for _ in range(count):
            self.emit(0)

    def check(self, rt, value, description):
        self.sw(rt, 20, len(self.expected)*4)
        self.expected.append(value & 0xffffffff)
        self.checks.append(description)

    def mtc0(self, rt, rd):
        self.emit(0x40800000 | rt << 16 | rd << 11)

    def finish_fixups(self):
        for pc, label, kind in self.fixups:
            target = self.labels[label]
            if kind == 'branch':
                offset = (target - pc - 4)//4
                if not -32768 <= offset <= 32767:
                    raise ValueError('branch too far')
                self.words[pc] |= offset & 0xffff
            else:
                if (pc+4) >> 28 != target >> 28:
                    raise ValueError('jump segment mismatch')
                self.words[pc] |= (target >> 2) & 0x3ffffff


def write_program(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    p = Program()
    p.const(20, 0xa0001000)  # sequential checked results
    p.const(22, 0xa000ff00)  # finish / IRQ request / IRQ acknowledgment
    p.const(23, 0xa0008000)  # exception report, separate from results
    p.addiu(8, 0, 0)
    p.addiu(9, 0, 10)
    p.addiu(10, 0, 0)
    p.label('loop')
    p.emit(9 << 21 | 8 << 16 | 8 << 11 | 0x21)  # addu r8,r9,r8
    p.addiu(9, 9, -1)
    p.branch(0x14000000, 9, 0, 'loop')
    p.addiu(10, 10, 1)  # taken and not-taken branch delay slots
    p.check(8, 55, 'cached loop sum')
    p.check(10, 10, 'taken and not-taken delay slots')
    p.jump('function', link=True)
    p.addiu(10, 0, 1)
    p.check(10, 0x31, 'JAL / JR and function delay slot')
    # Load the register jump target just before JALR; one load delay is needed.
    p.const(21, 0xa0008100)
    p.const(8, 0x80011000)
    p.sw(8, 21)
    p.lw(11, 21)
    p.nop()
    p.emit(11 << 21 | 31 << 11 | 9)  # jalr r31,r11
    p.addiu(10, 0, 2)
    p.check(10, 0x32, 'load-to-JALR and index conflict')
    fill_functions = ((0x80012000, 0), (0x80014000, 4),
                      (0x80015000, 8), (0x80016000, 12))
    for base, start in fill_functions:
        p.const(11, base + start)
        p.addiu(10, 0, 0)
        p.emit(11 << 21 | 31 << 11 | 9)
        p.nop()
        p.check(10, 4-start//4, f'fill starting word {start//4}')
    p.const(11, 0x00012000)  # same physical RAM, different virtual tag
    p.addiu(10, 0, 0)
    p.emit(11 << 21 | 31 << 11 | 9)
    p.nop()
    p.check(10, 4, 'KUSEG versus KSEG0 tag conflict')
    p.jump('mutable', link=True)
    p.nop()
    p.check(10, 0x11, 'initial cached mutable function')
    p.const(21, 0xa0013000)
    p.const(8, 0x240a0022)  # addiu r10,r0,0x22
    p.sw(8, 21)  # external write does not invalidate the instruction cache
    p.jump('mutable', link=True)
    p.nop()
    p.check(10, 0x11, 'uncached code store retains instruction cache')
    p.const(8, 0x00010000)  # IsC
    p.mtc0(8, 12)
    p.nop(6)
    p.const(21, 0x80013000)
    p.sw(0, 21)  # invalidate the tag, do not overwrite external memory
    p.mtc0(0, 12)
    p.nop(6)
    p.jump('mutable', link=True)
    p.nop()
    p.check(10, 0x22, 'isolated invalidation refills changed code')
    p.const(21, 0xa0013000)
    p.lw(8, 21)
    p.nop()
    p.check(8, 0x240a0022, 'isolated store did not overwrite external RAM')
    # Cached exception vector, IRQ followed by RFE and normal cached execution.
    p.const(8, 0x401)
    p.mtc0(8, 12)
    p.nop(6)
    p.sw(8, 22, 4)
    p.label('wait_irq')
    p.lw(8, 23, 8)
    p.nop()
    p.branch(0x10000000, 8, 0, 'wait_irq')
    p.nop()
    p.check(8, 1, 'IRQ handler / RFE returned')
    p.lw(8, 23)
    p.nop()
    p.emit(0x3108007c)  # andi r8,r8,0x7c
    p.check(8, 0, 'IRQ exception code')
    p.mtc0(0, 12)
    p.nop(6)
    p.label('syscall')
    p.emit(0x0000000c)
    p.lw(8, 23)
    p.nop()
    p.emit(0x3108007c)
    p.check(8, 0x20, 'SYSCALL exception code')
    p.lw(8, 23, 4)
    p.nop()
    syscall_check = len(p.expected)
    p.check(8, 0, 'SYSCALL EPC')  # resolved below
    p.label('bd_branch')
    p.branch(0x10000000, 0, 0, 'after_bd')
    p.emit(0x0000000c)  # SYSCALL in taken branch delay slot
    p.label('after_bd')
    p.lw(8, 23)
    p.nop()
    p.emit(8 << 16 | 8 << 11 | 31 << 6 | 2)  # srl r8,r8,31
    p.check(8, 1, 'branch-delay exception BD flag')
    p.lw(8, 23, 4)
    p.nop()
    bd_check = len(p.expected)
    p.check(8, 0, 'branch-delay exception EPC')
    p.const(8, 0x600df00d)
    p.sw(8, 22)
    p.label('halt')
    p.branch(0x10000000, 0, 0, 'halt')
    p.nop()

    p.at(0x80011000)
    p.label('function')
    p.addiu(10, 10, 0x10)
    p.jr(31)
    p.addiu(10, 10, 0x20)
    for base, _ in fill_functions:
        p.at(base)
        for _ in range(4):
            p.addiu(10, 10, 1)
        p.jr(31)
        p.nop()
    p.at(0x80013000)
    p.label('mutable')
    p.addiu(10, 0, 0x11)
    p.jr(31)
    p.nop()
    p.at(0x80000080)
    p.emit(0x401a6800)  # mfc0 k0,Cause
    p.emit(0x401b7000)  # mfc0 k1,EPC
    p.nop()
    p.sw(26, 23)
    p.sw(27, 23, 4)
    p.emit(0x3358007c)  # andi r24,k0,0x7c
    p.branch(0x10000000, 24, 0, 'irq_return')
    p.nop()
    p.branch(0x04000000, 26, 0, 'bd_return')  # bltz k0
    p.nop()
    p.addiu(27, 27, 4)
    p.branch(0x10000000, 0, 0, 'return')
    p.nop()
    p.label('bd_return')
    p.addiu(27, 27, 8)
    p.branch(0x10000000, 0, 0, 'return')
    p.nop()
    p.label('irq_return')
    p.addiu(24, 0, 1)
    p.sw(24, 23, 8)
    p.sw(0, 22, 8)
    p.label('return')
    p.jr(27)
    p.emit(0x42000010)  # RFE in return delay slot
    p.expected[syscall_check] = p.labels['syscall']
    p.expected[bd_check] = p.labels['bd_branch']
    p.finish_fixups()
    # CPU reset does NOT clear cache RAM. Like BIOS initialization, an uncached
    # bootstrap explicitly invalidates all 256 lines before entering RAM. This
    # also makes aborting a fill safe: data words may have overwritten a line
    # whose old tag survived CPU reset.
    bootstrap = [0x3c1a0001, 0x409a6000] + [0]*6
    bootstrap += [0x3c088000, 0x24090100]
    bootstrap += [0xad000000, 0x25080010, 0x2529ffff, 0x1520fffc, 0]
    bootstrap += [0x40806000] + [0]*6 + [0x3c1a8001, 0x03400008, 0]
    ram = directory/'ram.hex'
    ram.write_text(''.join(f'{address & 0x1fffffff:08x} {word:08x}\n'
                           for address, word in sorted(p.words.items())))
    rom = directory/'rom.hex'
    rom.write_text(''.join(f'{word:08x}\n' for word in bootstrap))
    expected = directory/'expected.hex'
    expected.write_text(''.join(f'{word:08x}\n' for word in p.expected))
    return {'ram': str(ram), 'rom': str(rom), 'expected': str(expected),
            'instructions': len(p.words)+len(bootstrap), 'checks': p.checks,
            'expected_checks': len(p.expected), 'labels': p.labels}
