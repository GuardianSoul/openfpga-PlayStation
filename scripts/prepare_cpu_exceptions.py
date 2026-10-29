#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""CPU exceptions a PS1 raises and the pinned MiSTer CPU does not.

ps1-tests cpu/cop (real PS1 17/17, core 13/17): COP1 and COP3 operations and
LWC0/1/3 / SWC0/1/3 decode as no-ops. On the R3000A each raises Coprocessor
Unusable (code 0x0B, CAUSE.CE = coprocessor number) while its SR.CU bit is
clear, and is a no-op while it is set. SWC0 checks CU0 even in kernel mode
(the COP0 operations keep their kernel-or-CU0 rule).

ps1-tests cpu/code-in-io: an instruction fetch outside main RAM and the BIOS
(scratchpad, I/O, EXP1/2/3) sent a code request the memory mux cannot serve,
so the CPU hung waiting for it. Such a fetch now completes at once with a NOP
flagged as a fetch error; the flag follows the instruction through decode,
and in execute it raises Instruction Bus Error (code 6, EPC = its PC) on the
normal execute-exception path, so older instructions (a jump's delay slot)
finish first. A PS1 runs code fetched from some I/O registers (SPU, DMA0,
DPCR); here those also raise IBE, so that test reaches 4/7 instead of hanging.

Guest-visible: only code that executes these instructions with CU clear, or
jumps outside RAM/BIOS, behaves differently; both used to be a no-op or a hang.
Usage: prepare_cpu_exceptions.py PROJECT  (post-prepare step; patches every
cpu.vhd the project's apf_top.qsf compiles)
"""
import re
import sys
from pathlib import Path

TAG = 'prepare_cpu_exceptions'

# opcode -> (SR bit of the coprocessor it needs, name)
COP_OPS = {0x11: (29, 'COP1'), 0x13: (31, 'COP3'), 0x30: (28, 'LWC0'), 0x31: (29, 'LWC1'),
           0x33: (31, 'LWC3'), 0x38: (28, 'SWC0'), 0x39: (29, 'SWC1'), 0x3B: (31, 'SWC3')}

BAD_FETCH = 'FetchAddr(31 downto 30) /= "11" and FetchAddr(28 downto 23) /= "000000" and FetchAddr(28 downto 19) /= "1111111000"'
FETCH_OLD = re.compile(r'            case \(to_integer\(FetchAddr\(31 downto 29\)\)\) is\n *\n               when 0 \| 4 => -- cache\n')
FETCH_GUARD = f'''            -- {TAG}: a fetch outside main RAM and the BIOS completes at once
            -- as a NOP flagged as a bus error (the memory mux serves code nowhere else)
            if ({BAD_FETCH}) then
               fetchErrNow       <= '1';
               PCnext            <= FetchAddr + 4;
               fetchReadyNext    <= '1';
               fetchReadyNow     <= '1';
               opcodeNext        <= (others => '0');
            else
'''
FETCH_END_OLD = re.compile(r'               when others =>\n                  -- todo\n *\n            end case;\n')
EDITS = (  # (what, old, new) exact, each once
    ('signals', '   signal opcodeNext                   : unsigned(31 downto 0) := (others => \'0\');\n',
     '   signal opcodeNext                   : unsigned(31 downto 0) := (others => \'0\');\n'
     f"   signal fetchErrNow                  : std_logic := '0';  -- {TAG}: this fetch is a bus error\n"
     "   signal fetchErr0                    : std_logic := '0';  -- opcode0 came from a bus-error fetch\n"
     "   signal decodeFetchErr               : std_logic := '0';  -- the decoded instruction did\n"),
    ('fetch defaults', '      opcodeNext      <= opcode0;\n', '      opcodeNext      <= opcode0;\n      fetchErrNow     <= \'0\';\n'),
    ('fetch register', "            if (fetchReadyNow = '1') then\n               opcode0        <= opcodeNext; \n               PCold0         <= PC;\n            end if;\n",
     "            if (fetchReadyNow = '1') then\n               opcode0        <= opcodeNext; \n               PCold0         <= PC;\n"
     "               fetchErr0      <= fetchErrNow;\n               if (fetchErrNow = '1') then PCold0 <= FetchAddr; end if;\n            end if;\n"),
    ('decode flush', '                  decodeJumpTarget <= (others => \'0\');\n               \n               else\n',
     "                  decodeJumpTarget <= (others => '0');\n                  decodeFetchErr   <= '0';\n               \n               else\n"),
    ('decode', '                  opcode1 <= opcodeCacheMuxed;\n',
     "                  opcode1 <= opcodeCacheMuxed;\n                  decodeFetchErr <= fetchErr0 and not cacheHit and not cacheHitLast;\n"),
    ('execute sensitivity', '   process (decodeImmData, decodeTarget,', '   process (decodeFetchErr, decodeImmData, decodeTarget,'),
    ('execute', '      elsif (exception(4 downto 2) = 0 and stall = 0) then\n             \n         case (to_integer(decodeOP)) is\n',
     "      elsif (exception(4 downto 2) = 0 and stall = 0) then\n             \n"
     f"         if (decodeFetchErr = '1') then -- {TAG}: Instruction Bus Error\n"
     "            exceptionNew3   <= '1';\n            exceptionCode_3 <= x\"6\";\n         end if;\n"
     "         case (to_integer(decodeOP)) is\n"),
)


def cop_arm(op, bit, name):
    return (f'            when 16#{op:02X}# => -- {name}: Coprocessor Unusable while SR.CU{bit - 28} is clear, else no operation ({TAG})\n'
            f"               if (cop0_SR({bit}) = '0') then\n"
            "                  exceptionNew3   <= '1';\n"
            '                  exceptionCode_3 <= x"B";\n'
            '               end if;\n')


def one(text, old, new, what):
    if isinstance(old, str):
        old = re.compile(re.escape(old))
    found = old.findall(text)
    if len(found) != 1:
        raise ValueError(f'{what}: expected exactly one match, found {len(found)}')
    return old.sub(lambda m: new(m.group(0)), text)


def transform(text):
    if TAG in text:
        return text
    for op, (bit, name) in COP_OPS.items():
        pattern = re.compile(rf'            when 16#{op:02X}# => -- {name} -> NOP *\n +null; *\n')
        found = pattern.findall(text)
        if len(found) != 1:
            raise ValueError(f'{name} no-op arm: expected exactly one match, found {len(found)}')
        text = pattern.sub(lambda m: cop_arm(op, bit, name), text)
    text = one(text, FETCH_OLD, lambda old: FETCH_GUARD + old, 'fetch case')
    text = one(text, FETCH_END_OLD, lambda old: old + '            end if;\n', 'fetch case end')
    for what, old, new in EDITS:
        text = one(text, old, lambda _, new=new: new, what)
    return text


def apply(project):
    project = Path(project)
    qsf = (project / 'apf_top.qsf').read_text()
    cpus = sorted({Path(p) for p in re.findall(r'VHDL_FILE "([^"]*/cpu\.vhd)"', qsf)})
    if not cpus:
        raise SystemExit(f'{TAG}: the project compiles no cpu.vhd')
    for cpu in cpus:
        try:
            cpu.write_text(transform(cpu.read_text()))
        except ValueError as error:
            raise SystemExit(f'{TAG}: {cpu}: {error}')
    return cpus


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.split('Usage: ')[1])
    for p in apply(sys.argv[1]):
        print('patched', p)
