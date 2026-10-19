#!/usr/bin/env python3
"""A small APF Chip32 VM model for testing support/chip32/psx_loader.asm.

Instruction decoding and flag rules follow agg23/openfpga-chip32-sim
(c358d70, src/cpu.rs), which documents Analogue's VM. The APF side is a
model: slots map to local files (None: no file), pmpr returns values from a
dictionary, and loadf/pmpw/core/host are recorded in order as events.
Only the instructions the loader can emit are implemented; anything else
raises, so a program change cannot silently pass through unmodelled code.
"""
import argparse
import json
from pathlib import Path


class Halt(Exception):
    pass


class Chip32:
    def __init__(self, program, slots, pmp_reads=None, selected_slot=0, regs=None, soft_open=(0x10,)):
        self.ram = bytearray(0x2000)
        self.ram[:len(program)] = program
        self.slots = dict(slots)             # id -> Path or None
        self.soft_open = set(soft_open)      # slots whose empty open does not fault
        self.pmp_reads = dict(pmp_reads or {})
        self.r = list(regs) if regs else [0]*16
        self.r[0] = selected_slot
        self.pc, self.zero, self.carry, self.stack = 2, False, False, []
        self.file = None                     # (slot, data, offset)
        self.events, self.output, self.result = [], [], None

    # helpers
    def word(self):
        value = self.ram[self.pc] | self.ram[self.pc+1] << 8
        self.pc += 2
        return value

    def rd(self, address, size):
        return int.from_bytes(self.ram[address:address+size], 'little')

    def string(self, address):
        end = self.ram.index(0, address)
        return self.ram[address:end].decode('ascii')

    def set(self, index, value):
        self.r[index] = value & 0xffffffff

    def run(self, limit=200000):
        for _ in range(limit):
            if self.result is not None: return self.result
            self.step()
        raise RuntimeError('Chip32 program did not halt')

    def fault(self, why):
        self.events.append(('fault', why))
        self.error_pc = self.pc
        self.pc = 0

    def step(self):
        if self.pc >= 0x2000: raise RuntimeError('Ran off the end of memory')
        inst = self.word()
        op, low = inst >> 8, inst & 0xff
        x, y = low & 0xf, low >> 4
        r = self.r
        if op == 0x00: return
        if op in (0x02, 0x04, 0x06, 0x32, 0x34, 0x36):          # ld.s Rx,(n|Ry)
            size = {2: 1, 4: 2, 6: 4}[op & 0xf]
            address = self.word() if op < 0x30 else r[y] & 0xffff
            value = self.rd(address, size); self.set(x, value); self.zero = value == 0
            return
        if op in (0x03, 0x05, 0x07, 0x33, 0x35, 0x37):          # ld.s (n|Ry),Rx
            size = {3: 1, 5: 2, 7: 4}[op & 0xf]
            address = self.word() if op < 0x30 else r[y] & 0xffff
            self.ram[address:address+size] = (r[x] & ((1 << 8*size)-1)).to_bytes(size, 'little')
            return
        if 0x08 <= op <= 0x0f or 0x18 <= op <= 0x1f or 0x28 <= op <= 0x2f:
            if op >= 0x28: value, dest = r[y], x
            elif op >= 0x18: value, dest = self.word() | self.word() << 16, low
            else: value, dest = self.word(), low
            kind, a = op & 0xf, r[dest]
            carry = None
            if kind == 8: out = value
            elif kind == 9: out = a & value
            elif kind == 0xa: out = a | value
            elif kind == 0xb: out = a ^ value
            elif kind in (0xc,):
                out = a + value; carry = out > 0xffffffff
            elif kind in (0xd, 0xe):
                out = a - value; carry = out < 0
            elif kind == 0xf: out = a & value
            out &= 0xffffffff
            if kind not in (0xe, 0xf): self.set(dest, out)
            if carry is not None: self.carry = carry
            self.zero = out == 0
            return
        if op == 0x24:                                         # asl Rx,#n
            out = r[x] << (y+1); self.carry = out > 0xffffffff
            self.set(x, out); self.zero = r[x] == 0
            return
        if op == 0x39:                                         # test Rx,Ry
            a, b = r[x] & 0x1fff, r[y] & 0x1fff
            while True:
                va, vb = self.ram[a], self.ram[b]
                if va == 0 and vb == 0: self.zero, self.carry = True, False; return
                if vb == 0: self.zero, self.carry = False, True; return
                if va != vb: self.zero, self.carry = False, False; return
                a += 1; b += 1
        if op == 0x3a:                                         # pmpw Rx,Ry
            self.events.append(('pmpw', r[x], r[y])); return
        if op == 0x3b:                                         # pmpr Rx,Ry
            self.events.append(('pmpr', r[x]))
            if r[x] in self.pmp_reads: self.set(y, self.pmp_reads[r[x]])
            return
        if op == 0x3e:                                         # div Rx,Ry
            if r[y] == 0: return self.fault('div by zero')
            q, m = divmod(r[x], r[y]); self.set(x, q); self.set(y, m)
            self.zero, self.carry = q == 0, m == 0
            return
        if op == 0x40:                                         # printf Rx
            self.output.append(self.string(r[x] & 0xffff)); return
        if op == 0x41:                                         # hex.w Rx
            self.output.append(f'{r[x] & 0xffff:04X}'); return
        if op == 0x42:                                         # ret (cc)
            if [True, not self.zero, self.zero, not self.carry, self.carry][x]:
                if not self.stack: return self.fault('stack underflow')
                self.pc = self.stack.pop()
            return
        if op == 0x45:                                         # err Rx,Ry
            self.set(x, 1); self.set(y, getattr(self, 'error_pc', 0)); return
        if op == 0x46:                                         # exit n
            self.result = x; self.events.append(('exit', x)); return
        if op == 0x53:                                         # loadf Rx
            path = self.slots.get(r[x])
            self.zero = path is not None and Path(path).is_file()
            self.events.append(('loadf', r[x])); return
        if op == 0x55:                                         # getname Rx,Ry
            path = self.slots.get(r[x])
            name = Path(path).name.encode() if path else b''
            at = r[y] & 0xffff
            self.ram[at:at+len(name)+1] = name+b'\0'
            return
        if op == 0x56:                                         # open Rx,Ry
            if self.file: return self.fault('file already open')
            path = self.slots.get(r[x])
            if path is not None and Path(path).is_file():
                data = Path(path).read_bytes()
                self.file = [r[x], data, 0]; self.set(y, len(data)); self.zero = True
            elif r[x] in self.slots and r[x] not in self.soft_open:
                # On the Pocket, opening a declared slot with no file faults
                # (docs/pocket-bios-check.md; a BIOS slot at 0x016C on
                # hardware). A nonvolatile save slot opens without its file:
                # the loader's card open boots games with no .sav yet.
                self.events.append(('open', r[x], False))
                return self.fault('open of a slot with no file')
            else:
                self.set(y, 0); self.zero = False
            self.events.append(('open', r[x], self.zero))
            return
        if op == 0x57:                                         # close
            if not self.file: return self.fault('close without open file')
            self.file = None; return
        if op == 0x58:                                         # seek Rx
            if not self.file: return self.fault('seek without open file')
            self.zero = r[x] <= len(self.file[1])
            if self.zero: self.file[2] = r[x]
            return
        if op == 0x59:                                         # read Rx,Ry
            if not self.file: return self.fault('read without open file')
            n, data, at = r[y], self.file[1], self.file[2]
            self.zero = n <= 4096 and at+n <= len(data)
            if self.zero:
                self.ram[r[x]:r[x]+n] = data[at:at+n]; self.file[2] += n
            return
        if op == 0x5d:                                         # queryslot Rx
            path = self.slots.get(r[x])
            self.zero = path is not None and Path(path).is_file()
            self.events.append(('queryslot', r[x])); return
        if op == 0x5b:                                         # core Rx
            self.events.append(('core', r[x])); return
        if op == 0x5c:                                         # host Rx,Ry
            self.events.append(('host', r[x], r[y])); return
        upper = op >> 4
        if 6 <= upper <= 0xf:                                  # jp/call (cc),n
            target = (((op & 0xf) << 8) | low) * 2
            cond = upper - 6 if upper <= 0xa else upper - 0xb
            take = [True, not self.zero, self.zero, not self.carry, self.carry][cond]
            if take:
                if upper >= 0xb:
                    if len(self.stack) >= 32: return self.fault('stack overflow')
                    self.stack.append(self.pc)
                self.pc = target
            return
        raise NotImplementedError(f'Chip32 opcode {inst:04x} at {self.pc-2:#x} is not modelled')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('program', type=Path)
    parser.add_argument('--slot', action='append', default=[], help='id=path (path "-" for no file)')
    parser.add_argument('--pmpr', action='append', default=[], help='address=value')
    args = parser.parse_args()
    slots = {int(k, 0): (None if v == '-' else Path(v)) for k, v in (s.split('=', 1) for s in args.slot)}
    reads = {int(k, 0): int(v, 0) for k, v in (s.split('=', 1) for s in args.pmpr)}
    vm = Chip32(args.program.read_bytes(), slots, reads)
    vm.run()
    print(json.dumps({'result': vm.result, 'events': vm.events, 'output': vm.output}, indent=1))


if __name__ == '__main__': main()
