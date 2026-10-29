#!/usr/bin/env python3
"""CPU execute stage: one result-source select instead of ~40 result assignments.

Opt-in. Proof: run_cpu_result_select.py (paired CPU lockstep, every result
source, 3 planted faults). Wrapper map (save build): CPU own ALUTs
2,448 -> 2,391 (-57).

MiSTer's execute process assigns EXEresultData at about 45 points inside its
case over decodeOP/decodeFunct (plus COP0's case over decodeRD), from about
20 distinct expressions; Quartus builds a 120:1-plus selector per bit from
that. Here every such assignment records which expression it chose in a
process variable (exe_src) and one case at the end of the process makes the
single assignment. In a process the last executed signal assignment wins, so
this is the same value whenever the expression evaluates the same at the end
of the process: signals do not change within a run; the variables used
(sharedShift, sharedSum) are assigned once before the case, and calcResult is
always sharedSum(31 downto 0) where it is used, so it is replaced by that.
Usage: prepare_cpu_result_select.py PROJECT (after prepare_pocket_early.py)
"""
from pathlib import Path
import re
import sys

ASSIGN = re.compile(r'EXEresultData(\s*)<= ([^;]+);')
LOGIC = ['value1 and value2', 'value1 or value2', 'value1 xor value2', 'value1 nor value2',
         'x"0000" & (value1(15 downto 0) and decodeImmData)', 'value1(31 downto 16) & (value1(15 downto 0) or decodeImmData)',
         'value1(31 downto 16) & (value1(15 downto 0) xor decodeImmData)']
LOGIC_UNIT = '''      if (decodeOP = 0) then exe_lb := value2; exe_lop := decodeFunct(1 downto 0);
      else exe_lb := x"0000" & decodeImmData; exe_lop := decodeOP(1 downto 0); end if;
      case (exe_lop) is
         when "00" => exe_logic := value1 and exe_lb;
         when "01" => exe_logic := value1 or exe_lb;
         when "10" => exe_logic := value1 xor exe_lb;
         when others => exe_logic := value1 nor exe_lb;
      end case;
'''
PROCESS_HEAD = '   process (decodeImmData, decodeTarget, decodeJumpTarget,'


def result_select_text(text, merge_logic=True):
    if 'exe_src' in text: raise ValueError('cpu-result-select already applied')
    if text.count(PROCESS_HEAD) != 1: raise ValueError('cpu-result-select: execute process anchor changed')
    start = text.index(PROCESS_HEAD)
    begin = text.index('\n   begin\n', start)
    end = text.index('\n   end process;', begin)
    body = text[begin:end]
    # calcResult: assigned only as sharedSum(31 downto 0)
    assigns = set(re.findall(r'calcResult\s*:=\s*([^;]+);', body))
    if assigns != {'sharedSum(31 downto 0)'}: raise ValueError(f'cpu-result-select: calcResult assigned from {assigns}')
    for var in ('sharedShift', 'sharedSum'):
        if len(re.findall(rf'\b{var}\s*:=', body)) != 1: raise ValueError(f'cpu-result-select: {var} assigned more than once')
    sources = []; seen_logic = set()

    def code(m):
        expr = ' '.join(m.group(2).split())
        if expr == 'calcResult': expr = 'sharedSum(31 downto 0)'
        if merge_logic and expr in LOGIC: seen_logic.add(expr); expr = 'exe_logic'
        if expr not in sources: sources.append(expr)
        return f'exe_src := {sources.index(expr)};'
    body, n = ASSIGN.subn(code, body)
    if n < 40 or 'resultData' not in sources: raise ValueError(f'cpu-result-select: {n} result assignments found')
    # the variables a source reads must be final by the end of the process
    for expr in sources:
        for var in ('calcResult', 'calcMemAddr', 'executeShamt', 'sharedRight', 'sharedSub', 'sharedLess'):
            if re.search(rf'\b{var}\b', expr): raise ValueError(f'cpu-result-select: source reads variable {var}')
    # AND/OR/XOR/NOR and ANDI/ORI/XORI: one logic unit. Its op is the low two
    # bits of decodeFunct (special) or decodeOP (0x0C-0x0E), operand B value2
    # or the zero-extended immediate; for each of the seven the same value.
    if merge_logic and seen_logic != set(LOGIC): raise ValueError(f'cpu-result-select: logic sources {seen_logic}')
    lines = [f'         when {i} => EXEresultData <= {e};\n' for i, e in enumerate(sources)]
    final = ('\n      -- cpu-result-select: the one result assignment\n' + (LOGIC_UNIT if merge_logic else '') +
             '      case (exe_src) is\n' + ''.join(lines) +
             '         when others => EXEresultData <= resultData;\n      end case;')
    decl = text[start:begin]
    decl = decl + f'\n      variable exe_src : integer range 0 to {len(sources) - 1} := 0;   -- cpu-result-select'
    if merge_logic: decl += '\n      variable exe_lb, exe_logic : unsigned(31 downto 0);\n      variable exe_lop : unsigned(1 downto 0);'
    return text[:start] + decl + body + final + text[end:], sources


def prepare_project(project):
    project = Path(project).resolve(); qsf = project/'apf_top.qsf'; q = qsf.read_text()
    hits = re.findall(r'"([^"]*/cpu\.vhd)"', q)
    if len(hits) != 1: raise ValueError('cpu-result-select: expected one cpu.vhd in the project')
    out = project/'cpu-result-select'/'cpu.vhd'; out.parent.mkdir(exist_ok=True)
    out.write_text(result_select_text(Path(hits[0]).read_text())[0])
    qsf.write_text(q.replace(f'"{hits[0]}"', f'"{out}"'))
    return out


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1])
    print(prepare_project(sys.argv[1]))
