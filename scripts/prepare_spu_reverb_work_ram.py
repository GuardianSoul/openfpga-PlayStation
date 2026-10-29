#!/usr/bin/env python3
"""SPU reverb: the step values in one LUT RAM instead of eleven registers.

The reverb steps keep eleven 16-bit values in flip-flops: the six SPU RAM
reads (reverbReadval1-6), IIR_INPUT_B, IIR_A, IIR_B, MDA and MDB. Each is
written in one known step and read in later steps of the same sample, at
most one write and one read per clk1x cycle. Here they live in a 16-word
asynchronous-read MLAB: every write keeps its clock edge and data (one write
port, fed from the step that wrote the register), and the read address comes
from the reverb state and step, so each reader sees the value the register
held. soundmul1 and ram_dataWrite lose eight and three register sources.
With --last the two reverb outputs (reverbLastLeft/Right, written in the
last PROCIN step and read in REVERB_END, kept across samples) join them as
words 11 and 12; two valid bits reproduce their reset clear.
With --capture the capture volumes of voices 1 and 3 (voice_lastVolume1/3,
written in VOICE_END, read in CAPTURE2/3, no reset) join as words 13 and 14,
and ram_dataWrite loses two more sources.
Run after prepare_spu_reverb_operands.py (its swapped IIASM operands are
expected). Check: scripts/run_spu_reverb_work_ram.py.
Usage: prepare_spu_reverb_work_ram.py PROJECT [--last] [--capture]
"""
from pathlib import Path
import re
import sys

# word of each value in the RAM
INDEX = {'reverbReadval1': 0, 'reverbReadval2': 1, 'reverbReadval3': 2, 'reverbReadval4': 3,
         'reverbReadval5': 4, 'reverbReadval6': 5, 'IIR_INPUT_B': 6, 'IIR_A': 7, 'IIR_B': 8, 'MDA': 9, 'MDB': 10}

# (state, reverb_count) -> value read in that step (every reader of the eleven registers)
READS = {
    'REVERB_PROCOUT': {0: 'reverbReadval1', 2: 'reverbReadval2', 4: 'reverbReadval3', 5: 'IIR_INPUT_B', 6: 'reverbReadval4'},
    'REVERB_WRITE1': {0: 'IIR_A', 1: 'IIR_B'},
    'REVERB_PROCIN': {0: 'reverbReadval1', 1: 'reverbReadval2', 2: 'reverbReadval3', 3: 'reverbReadval4',
                      4: 'reverbReadval5', 6: 'reverbReadval6', 7: 'MDA', 9: 'reverbReadval5', 10: 'MDB',
                      12: 'reverbReadval6'},
    'REVERB_WRITE2': {0: 'MDA', 1: 'MDB'},
}


def _once(text, old, new, what):
    if text.count(old) != 1: raise ValueError(f'spu-reverb-work-ram: {what} anchor found {text.count(old)} times')
    return text.replace(old, new)


LAST = {'reverbLastLeft': 11, 'reverbLastRight': 12}
LAST_READS = {'REVERB_END': {0: 'reverbLastLeft', 1: 'reverbLastRight'}}
CAPTURE = {'voice_lastVolume1': 13, 'voice_lastVolume3': 14}
# state -> value read in that state, whatever the step
CAPTURE_READS = {'CAPTURE2': 'voice_lastVolume1', 'CAPTURE3': 'voice_lastVolume3'}


def read_address(last=False, capture=False):
    index = dict(INDEX, **LAST, **CAPTURE)
    lines = ['   process (state, reverb_count)', '   begin', '      rv_work_raddr <= 0;', '      case (state) is']
    for state, counts in dict(READS, **(LAST_READS if last else {})).items():
        lines += [f'         when {state} =>', '            case (reverb_count) is']
        lines += [f'               when {c} => rv_work_raddr <= {index[v]};' for c, v in counts.items()]
        lines += ['               when others => null;', '            end case;']
    for state, v in (CAPTURE_READS if capture else {}).items():
        lines += [f'         when {state} =>', f'            rv_work_raddr <= {index[v]};']
    lines += ['         when others => null;', '      end case;', '   end process;', '']
    return '\n'.join(lines)


def work_ram_text(text, last=False, capture=False):
    if 'spu-reverb-operands: factors swapped' not in text:
        raise ValueError('spu-reverb-work-ram needs prepare_spu_reverb_operands.py first')
    if 'rv_work_q' in text:
        raise ValueError('spu-reverb-work-ram: already applied')
    if last:
        # (simulation-only sound debug log: its reverb-output record goes)
        text, n = re.subn(r'\n *if \(state = REVERB_END and reverb_count = 0 and \(reverbLastLeft /= 0 or reverbLastRight /= 0\)\) then\n(?:[^\n]*\n){4} *end if;\n',
                          '\n', text)
        if n != 1: raise ValueError('spu-reverb-work-ram: reverb output debug record changed')
        text = _once(text, "            reverbLastLeft       <= (others => '0');\n            reverbLastRight      <= (others => '0');\n",
                     '            rvLastValid          <= "00";\n', 'reverb output reset')
        for name, side in (('reverbLastRight', 1), ('reverbLastLeft', 0)):
            pattern = rf'\b{name}( +)<= ([^;]+);'
            if len(re.findall(pattern, text)) != 1: raise ValueError(f'spu-reverb-work-ram: write of {name} changed')
            text = re.sub(pattern, lambda m, n=name, sd=side: f"rvw_we := '1'; rvw_addr := {LAST[n]}; rvw_data := std_logic_vector({m.group(2)}); rvLastValid({sd}) <= '1';", text)
            if text.count(f'resize({name}, 24)') != 1: raise ValueError(f'spu-reverb-work-ram: read of {name} changed')
            text = text.replace(f'resize({name}, 24)', 'resize(rv_last_q, 24)')
    if capture:
        for name, i in CAPTURE.items():
            voice = name[-1]
            text = _once(text, f'if (index = {voice}) then {name} <= voice_lastVolume; end if;',
                         f"if (index = {voice}) then rvw_we := '1'; rvw_addr := {i}; rvw_data := std_logic_vector(voice_lastVolume); end if;",
                         'capture volume write')
    names = list(INDEX) + (list(LAST) if last else []) + (list(CAPTURE) if capture else [])
    # declarations: the registers go, the RAM comes
    for name in names:
        text, n = re.subn(rf'^   signal {name} +: signed\(15 downto 0\);\n', '', text, flags=re.M)
        if n != 1: raise ValueError(f'spu-reverb-work-ram: declaration of {name} found {n} times')
    marker = '   signal rv_engine_q    : std_logic_vector(15 downto 0);\n'
    text = _once(text, marker, marker + '''   -- spu-reverb-work-ram: the reverb step values (read values, IIR and MD terms)
   type trvWork is array(0 to 15) of std_logic_vector(15 downto 0);
   signal rvWork         : trvWork;
   attribute ramstyle of rvWork : signal is "MLAB, no_rw_check";
   signal rv_work_raddr  : integer range 0 to 15;
   signal rv_work_q      : std_logic_vector(15 downto 0);
''' + ('''   signal rvLastValid    : std_logic_vector(1 downto 0) := "00";
   signal rv_last_q      : signed(15 downto 0);
''' if last else ''), 'reverb table declarations')
    # writes: one write port, on the edge and with the data of the original assignment
    for name, i in INDEX.items():
        pattern = rf'\b{name} <= ([^;]+);'
        found = re.findall(pattern, text)
        if not found: raise ValueError(f'spu-reverb-work-ram: no write of {name}')
        text = re.sub(pattern, lambda m, i=i: f'rvw_we := \'1\'; rvw_addr := {i}; rvw_data := std_logic_vector({m.group(1)});', text)
    # reads: the RAM word the step's read address selects
    for name in names:
        text = re.sub(rf'resize\({name}, (\d+)\)', r'resize(signed(rv_work_q), \1)', text)
        text = text.replace(f'std_logic_vector({name})', 'rv_work_q')
        text = text.replace(f'({name} = -32768)', '(signed(rv_work_q) = -32768)')
        code = '\n'.join(l.split('--')[0] for l in text.splitlines())
        if re.search(rf'\b{name}\b', code): raise ValueError(f'spu-reverb-work-ram: a reader of {name} is left')
    # process variables and the write at the end of the clk1x edge
    text = _once(text, '      variable reverbInputRight_v  : signed(15 downto 0);\n   begin\n      if (rising_edge(clk1x)) then\n',
                 '      variable reverbInputRight_v  : signed(15 downto 0);\n'
                 '      variable rvw_we              : std_logic;\n'
                 '      variable rvw_addr            : integer range 0 to 15;\n'
                 '      variable rvw_data            : std_logic_vector(15 downto 0);\n'
                 '   begin\n      if (rising_edge(clk1x)) then\n\n         rvw_we := \'0\'; rvw_addr := 0; rvw_data := (others => \'0\');\n',
                 'engine process start')
    anchor = 'ram_rnw        <= SS_RAM_rnw;'
    if text.count(anchor) != 1: raise ValueError('spu-reverb-work-ram: engine process end anchor changed')
    at = text.index('\n      end if;\n   end process;\n', text.index(anchor))
    text = text[:at] + "\n\n         if (rvw_we = '1') then\n            rvWork(rvw_addr) <= rvw_data;\n         end if;" + text[at:]
    marker = '   reverb_reqAddr  <= '
    reads = '   rv_work_q <= rvWork(rv_work_raddr);\n'
    if last:
        reads += ("   rv_last_q <= signed(rv_work_q) when ((rv_work_raddr = 11 and rvLastValid(0) = '1') or\n"
                  "                                        (rv_work_raddr = 12 and rvLastValid(1) = '1')) else (others => '0');\n")
    text = _once(text, marker, reads + '\n' + read_address(last, capture) + '\n' + marker, 'reverb address block')
    return text


def apply_project(project, last=False, capture=False):
    """Rewrite the spu.vhd the project's QSF assigns (a project-local copy)."""
    project = Path(project).resolve(); qsf = project / 'apf_top.qsf'; q = qsf.read_text()
    hits = re.findall(r'"([^"]*/spu\.vhd)"', q)
    if len(hits) != 1: raise ValueError('spu-reverb-work-ram: expected one spu.vhd in the project')
    out = project / 'spu-reverb-work-ram' / 'spu.vhd'; out.parent.mkdir(exist_ok=True)
    out.write_text('-- Modified by scripts/prepare_spu_reverb_work_ram.py: see its docstring.\n' + work_ram_text(Path(hits[0]).read_text(), last, capture))
    qsf.write_text(q.replace(f'"{hits[0]}"', f'"{out}"'))
    return out


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args or args[0].startswith('--') or not set(args[1:]) <= {'--last', '--capture'}:
        raise SystemExit('usage: prepare_spu_reverb_work_ram.py PROJECT [--last] [--capture]')
    print(apply_project(args[0], '--last' in args, '--capture' in args))
