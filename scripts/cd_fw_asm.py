#!/usr/bin/env python3
"""Assembler for the cd_subcpu firmware (cd-rewrite-061; ISA in rtl/cd/cd_subcpu.vhd).

usage: cd_fw_asm.py SOURCE.s [--hex OUT.hex] [--vhdl OUT.vhd] [--depth 2048] [--list OUT.lst]

Syntax (one statement per line, ';' comments):
  label:                      .equ NAME expr        .word expr[, expr...]   (20-bit data words for ldt)
  add rA, rB, rC   sub/and/or/xor likewise          addi rA, rB, s7   andi/ori rA, rB, u7
  shl1/shr1/shl4/shr4 rA, rB  mov rA, rB (= addi rA, rB, 0)
  li rA, expr     (1..3 words: limm + lsh)          cmp rB, rC   cmpi rB, u7   btst rB, bit
  bset rA, rB, bit   bclr rA, rB, bit                b/beq/bne/blo/bhs/bmi/bpl/bhi/bls label
  call label   ret   in rA, port   out port, rB   ld rA, [rB+u7]   st [rB+u7], rA   ldt rA, [rB+u7]
  wait rA, rC
Structured control (expands to compare + branch):
  if COND / elif COND / else / endif      while COND / endwhile      loop / endloop / break
  COND:  rB == rC|imm   rB != ..   rB < ..   rB >= ..   rB > ..   rB <= ..   (unsigned; imm 0..127)
         bit rB, n      !bit rB, n
Expressions are Python integer expressions over .equ names.
"""
import argparse, re, sys

OPS = {'nop': 0, 'add': 1, 'sub': 2, 'and': 3, 'or': 4, 'xor': 5, 'addi': 6, 'andi': 7, 'ori': 8, 'shf': 9,
       'limm': 10, 'lsh': 11, 'cmp': 12, 'cmpi': 13, 'btst': 14, 'bset': 15, 'bclr': 16, 'br': 17, 'call': 18,
       'ret': 19, 'in': 20, 'out': 21, 'ld': 22, 'st': 23, 'ldt': 24, 'wait': 25}
COND = {'b': 0, 'beq': 1, 'bne': 2, 'blo': 3, 'bhs': 4, 'bmi': 5, 'bhi': 6, 'bls': 7}
SHF = {'shl1': 0, 'shr1': 1, 'shl4': 2, 'shr4': 3}
INV = {'beq': 'bne', 'bne': 'beq', 'blo': 'bhs', 'bhs': 'blo', 'bhi': 'bls', 'bls': 'bhi'}


class AsmError(Exception):
    pass


def reg(t):
    m = re.fullmatch(r'r(\d+)', t.strip())
    if not m or int(m.group(1)) > 15:
        raise AsmError(f'bad register {t!r}')
    return int(m.group(1))


def word(op, a=0, b=0, imm=0):
    return (op << 15) | (a << 11) | (b << 7) | (imm & 0x7F)


class Asm:
    def __init__(self):
        self.equ = {}
        self.labels = {}
        self.out = []          # (addr, kind, payload, line)
        self.uid = 0
        self.fwd = set()

    def ev(self, e, line):
        try:
            return int(eval(e, {'__builtins__': {}}, dict(self.equ)))
        except Exception as ex:
            raise AsmError(f'line {line}: cannot evaluate {e!r}: {ex}')

    def new(self, p):
        self.uid += 1
        return f'.{p}{self.uid}'

    # ---------------------------------------------------------------- pass 1: expand macros to primitive statements
    def expand(self, lines):
        prims = []
        stack = []

        def cond_branch(cond, target, line, negate):
            """branch to target when COND is false (negate=True) or true"""
            m = re.fullmatch(r'(!?)bit\s+(r\d+)\s*,\s*(.+)', cond)
            if m:
                prims.append((line, 'btst', [m.group(2), m.group(3)]))
                is_set_true = m.group(1) == ''
                # btst: Z=1 when the bit is clear
                br_when_true = 'bne' if is_set_true else 'beq'
                br = INV[br_when_true] if negate else br_when_true
                prims.append((line, br, [target]))
                return
            m = re.fullmatch(r'(r\d+)\s*(==|!=|<=|>=|<|>)\s*(.+)', cond)
            if not m:
                raise AsmError(f'line {line}: bad condition {cond!r}')
            x, op, y = m.groups()
            if re.fullmatch(r'r\d+', y.strip()):
                prims.append((line, 'cmp', [x, y.strip()]))
            else:
                prims.append((line, 'cmpi', [x, y.strip()]))
            br_true = {'==': 'beq', '!=': 'bne', '<': 'blo', '>=': 'bhs', '>': 'bhi', '<=': 'bls'}[op]
            prims.append((line, INV[br_true] if negate else br_true, [target]))

        for n, raw in enumerate(lines, 1):
            s = raw.split(';', 1)[0].strip()
            if not s:
                continue
            m = re.fullmatch(r'([A-Za-z_.][\w.]*):\s*(.*)', s)
            if m:
                prims.append((n, 'label', [m.group(1)]))
                s = m.group(2).strip()
                if not s:
                    continue
            parts = s.split(None, 1)
            mn, rest = parts[0].lower(), (parts[1] if len(parts) > 1 else '')
            if mn == 'if':
                f_else, f_end = self.new('else'), self.new('endif')
                stack.append(['if', f_else, f_end, False])
                cond_branch(rest.strip(), f_else, n, True)
            elif mn == 'elif':
                t = stack[-1]
                if t[0] != 'if' or t[3]: raise AsmError(f'line {n}: elif without if')
                prims.append((n, 'b', [t[2]]))
                prims.append((n, 'label', [t[1]]))
                t[1] = self.new('else')
                cond_branch(rest.strip(), t[1], n, True)
            elif mn == 'else':
                t = stack[-1]
                if t[0] != 'if' or t[3]: raise AsmError(f'line {n}: else without if')
                prims.append((n, 'b', [t[2]]))
                prims.append((n, 'label', [t[1]]))
                t[3] = True
            elif mn == 'endif':
                t = stack.pop()
                if t[0] != 'if': raise AsmError(f'line {n}: endif without if')
                if not t[3]:
                    prims.append((n, 'label', [t[1]]))
                prims.append((n, 'label', [t[2]]))
            elif mn == 'while':
                top, end = self.new('wtop'), self.new('wend')
                stack.append(['while', top, end])
                prims.append((n, 'label', [top]))
                cond_branch(rest.strip(), end, n, True)
            elif mn == 'endwhile':
                t = stack.pop()
                if t[0] != 'while': raise AsmError(f'line {n}: endwhile without while')
                prims.append((n, 'b', [t[1]]))
                prims.append((n, 'label', [t[2]]))
            elif mn == 'loop':
                top, end = self.new('ltop'), self.new('lend')
                stack.append(['loop', top, end])
                prims.append((n, 'label', [top]))
            elif mn == 'endloop':
                t = stack.pop()
                if t[0] != 'loop': raise AsmError(f'line {n}: endloop without loop')
                prims.append((n, 'b', [t[1]]))
                prims.append((n, 'label', [t[2]]))
            elif mn == 'break':
                t = next((x for x in reversed(stack) if x[0] in ('loop', 'while')), None)
                if t is None: raise AsmError(f'line {n}: break outside a loop')
                prims.append((n, 'b', [t[2]]))
            else:
                args = [a.strip() for a in re.split(r',(?![^\[]*\])', rest)] if rest else []
                prims.append((n, mn, args))
        if stack:
            raise AsmError(f'unclosed {stack[-1][0]}')
        return prims

    def size(self, mn, args, line):
        if mn in ('label', '.equ'):
            return 0
        if mn == '.word':
            return len(args)
        if mn == 'li':
            try:
                v = self.ev(args[1], line) & 0xFFFFFFFF
            except AsmError:
                if args[1].strip() in self.prev:   # forward label: size from the previous iteration
                    v = self.prev[args[1].strip()]
                else:
                    self.fwd.add(line)
                    return 1
            return 1 if v < 2048 else (2 if v < 2**22 else 3)
        return 1

    def assemble(self, text):
        self.prev = {}
        for it in range(6):
            self.equ, self.labels, self.fwd, self.uid = {}, {}, set(), 0
            try:
                words = self.assemble1(text)
                return words
            except AsmError as e:
                if 'forward label' not in str(e) or not self.labels:
                    raise
                if self.prev == self.labels:
                    raise
                self.prev = dict(self.labels)
        raise AsmError('label sizes do not converge')

    def assemble1(self, text):
        prims = self.expand(text.split('\n'))
        # .equ first (in order), labels by sizes
        pc = 0
        for line, mn, args in prims:
            if mn == '.equ':
                name, expr = args[0].split(None, 1) if len(args) == 1 else (args[0], args[1])
                self.equ[name] = self.ev(expr, line)
            elif mn == 'label':
                if args[0] in self.labels:
                    raise AsmError(f'line {line}: duplicate label {args[0]}')
                self.labels[args[0]] = pc
                self.equ[args[0]] = pc
            elif mn == '.org':
                pc = self.ev(args[0], line)
            else:
                pc += self.size(mn, args, line)
        # pass 2
        words = {}
        pc = 0
        for line, mn, args in prims:
            if mn in ('label', '.equ'):
                continue
            if mn == '.org':
                pc = self.ev(args[0], line); continue
            for w in self.encode(mn, args, line):
                if pc in words:
                    raise AsmError(f'line {line}: overlap at {pc}')
                words[pc] = w & 0xFFFFF
                pc += 1
        return words

    def encode(self, mn, args, line):
        try:
            if mn == '.word':
                return [self.ev(a, line) & 0xFFFFF for a in args]
            if mn == 'nop':
                return [0]
            if mn in ('add', 'sub', 'and', 'or', 'xor'):
                return [word(OPS[mn], reg(args[0]), reg(args[1]), reg(args[2]))]
            if mn in ('addi', 'andi', 'ori'):
                v = self.ev(args[2], line)
                if mn == 'addi' and not -64 <= v <= 63: raise AsmError(f'addi immediate {v} out of range')
                if mn != 'addi' and not 0 <= v <= 127: raise AsmError(f'{mn} immediate {v} out of range')
                return [word(OPS[mn], reg(args[0]), reg(args[1]), v)]
            if mn == 'mov':
                return [word(OPS['addi'], reg(args[0]), reg(args[1]), 0)]
            if mn in SHF:
                return [word(OPS['shf'], reg(args[0]), reg(args[1]), SHF[mn])]
            if mn == 'li':
                a = reg(args[0]); v = self.ev(args[1], line) & 0xFFFFFFFF
                if a == 0: raise AsmError('r0 is constant zero')
                chunks = []
                while True:
                    chunks.append(v & 0x7FF); v >>= 11
                    if v == 0: break
                chunks.reverse()
                if line in self.fwd and len(chunks) != 1: raise AsmError('forward label value >= 2048')
                out = [(OPS['limm'] << 15) | (a << 11) | chunks[0]]
                out += [(OPS['lsh'] << 15) | (a << 11) | c for c in chunks[1:]]
                return out
            if mn == 'cmp':
                return [word(OPS['cmp'], 0, reg(args[0]), reg(args[1]))]
            if mn == 'cmpi':
                v = self.ev(args[1], line)
                if not 0 <= v <= 127: raise AsmError(f'cmpi immediate {v} out of range (use li + cmp)')
                return [word(OPS['cmpi'], 0, reg(args[0]), v)]
            if mn == 'btst':
                return [word(OPS['btst'], 0, reg(args[0]), self.ev(args[1], line) & 31)]
            if mn in ('bset', 'bclr'):
                return [word(OPS[mn], reg(args[0]), reg(args[1]), self.ev(args[2], line) & 31)]
            if mn in COND:
                t = self.labels.get(args[0])
                if t is None: raise AsmError(f'unknown label {args[0]}')
                return [(OPS['br'] << 15) | (COND[mn] << 12) | t]
            if mn == 'call':
                t = self.labels.get(args[0])
                if t is None: raise AsmError(f'unknown label {args[0]}')
                return [(OPS['call'] << 15) | t]
            if mn == 'ret':
                return [OPS['ret'] << 15]
            if mn == 'in':
                return [word(OPS['in'], reg(args[0]), 0, self.ev(args[1], line))]
            if mn == 'out':
                return [word(OPS['out'], 0, reg(args[1]), self.ev(args[0], line))]
            if mn in ('ld', 'ldt'):
                m = re.fullmatch(r'\[\s*(r\d+)\s*(?:\+\s*(.+))?\]', args[1])
                return [word(OPS[mn], reg(args[0]), reg(m.group(1)), self.ev(m.group(2) or '0', line))]
            if mn == 'st':
                m = re.fullmatch(r'\[\s*(r\d+)\s*(?:\+\s*(.+))?\]', args[0])
                return [word(OPS['st'], reg(args[1]), reg(m.group(1)), self.ev(m.group(2) or '0', line))]
            if mn == 'wait':
                return [word(OPS['wait'], reg(args[0]), 0, reg(args[1]))]
        except AsmError as e:
            raise AsmError(f'line {line}: {e}')
        raise AsmError(f'line {line}: unknown mnemonic {mn}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source')
    ap.add_argument('--hex'); ap.add_argument('--vhdl'); ap.add_argument('--list')
    ap.add_argument('--depth', type=int, default=2048)
    a = ap.parse_args()
    asm = Asm()
    def load(path, depth=0):
        import os
        out = []
        for line in open(path).read().split('\n'):
            m = re.match(r'\s*\.include\s+"([^"]+)"', line)
            if m:
                if depth > 8: sys.exit('include depth')
                out += load(os.path.join(os.path.dirname(path), m.group(1)), depth + 1)
            else:
                out.append(line)
        return out
    try:
        words = asm.assemble('\n'.join(load(a.source)))
    except AsmError as e:
        sys.exit(f'{a.source}: {e}')
    top = max(words) + 1 if words else 0
    if top > a.depth:
        sys.exit(f'program {top} words > depth {a.depth}')
    print(f'{a.source}: {len(words)} words, top {top} of {a.depth}')
    if a.hex:
        with open(a.hex, 'w') as f:
            for i in range(a.depth):
                f.write(f'{words.get(i, 0):05x}\n')
    if a.vhdl:
        with open(a.vhdl, 'w') as f:
            f.write('-- generated by scripts/cd_fw_asm.py from ' + a.source + '\n')
            f.write('(\n' + ',\n'.join(f'      {i} => x"{w:05x}"' for i, w in sorted(words.items())) +
                    ',\n      others => x"00000")\n')
    if a.list:
        with open(a.list, 'w') as f:
            for name, adr in sorted(asm.labels.items(), key=lambda x: x[1]):
                f.write(f'{adr:4d} {name}\n')


if __name__ == '__main__':
    main()
