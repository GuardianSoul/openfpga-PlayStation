#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""SAVE_MODE reaches the SDRAM address through memorymux's clk1x register (a frozen project step).

psx_top ANDed the CPU's RAM address bits 22:21 with save_mode (a clk2x register in
psx_save_exp1) behind memorymux's address register, so the level went straight into the
clk3x SDRAM controller: an untimed path (about -3.4 ns at sign-off). The AND now sits in
front of memorymux's clk1x ram_Adr register at the two places a CPU address is taken
(instruction fetch and data access). The level is then timed clk2x -> clk1x like any other
register, and the address the controller sees only changes through the timed clk1x register.
Behaviour is the same as long as save_mode does not change between the edge an address is
registered and the end of that RAM access; a simulation-only check in memorymux fails the
run if it ever does (the save routine's CPU benches run through it).
The EXE loader's two address lines keep their bits unmasked; loadExe is tied to '0' in
psx_mister_psx_top_resource, so they are never used.
Usage: prepare_save_mode_clk1x.py SOURCE --output OUT
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
MARK = 'save-mode-clk1x'
CHECK = '''   -- synthesis translate_off
   -- save-mode-clk1x: the masked address equals the old (address AND save_mode) behind the
   -- register only while save_mode holds from the edge that registered the address to the
   -- end of the access (ram_done, at most 24 clk1x here)
   save_mode_check : process (clk1x)
      variable pending : integer range 0 to 24 := 0;
      variable level, prev : std_logic := '0';
   begin
      if rising_edge(clk1x) then
         if ram_ena = '1' then pending := 24; level := prev; end if;
         if pending /= 0 then
            assert save_mode = level report "save-mode-clk1x: save_mode changed during a RAM access" severity failure;
            if ram_done = '1' then pending := 0; else pending := pending - 1; end if;
         end if;
         prev := save_mode;
      end if;
   end process;
   -- synthesis translate_on
'''


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'expected one match, found {text.count(old)}: {old[:80]!r}')
    return text.replace(old, new)


def memorymux(t, mask='(save_mode & save_mode)'):
    t = once(t, "      region_in            : in  std_logic_vector(1 downto 0);\n",
             "      region_in            : in  std_logic_vector(1 downto 0);\n"
             "      save_mode            : in  std_logic := '0';   -- save-mode-clk1x\n")
    for kind in ('Instr', 'Data'):
        t = once(t, f'<= "00" & std_logic_vector(mem_address{kind}(22 downto 2)) & "00";',
                 f'<= "00" & (std_logic_vector(mem_address{kind}(22 downto 21)) and {mask}) & '
                 f'std_logic_vector(mem_address{kind}(20 downto 2)) & "00";   -- save-mode-clk1x')
    return once(t, 'end architecture;', CHECK + 'end architecture;')


def psx_top(t):
    t = once(t, "ram_cpu_Adr(24 downto 23) & (ram_cpu_Adr(22 downto 21) and (save_mode & save_mode)) & ram_cpu_Adr(20 downto 0);",
             "ram_cpu_Adr;   -- save-mode-clk1x: bits 22:21 are masked in memorymux's clk1x register")
    return once(t, "      ram_Adr              => ram_cpu_Adr,  \n", "      ram_Adr              => ram_cpu_Adr,  \n      save_mode            => save_mode,\n")


def compiled(project, name):
    qsf = (project / 'apf_top.qsf').read_text()
    hits = sorted({l.split('"')[1] for l in qsf.splitlines() if l.count('"') >= 2 and l.split('"')[1].endswith('/' + name)})
    if len(hits) != 1 or not Path(hits[0]).is_relative_to(project):
        raise ValueError(f'expected one compiled {name} inside the snapshot, found {hits}')
    return Path(hits[0])


def prepare(source, output):
    from run_frozen_pocket_fit import snapshot, hold_guard
    from measure_pocket_seeds import inputs
    source, output = Path(source).resolve(), Path(output).resolve()
    result = snapshot(source, source / 'fit-inputs.json', output, 2)
    files = [(compiled(output, 'memorymux.vhd'), memorymux), (compiled(output, 'psx_top.vhd'), psx_top)]
    for p, f in files:
        text = p.read_text()
        if MARK in text:
            raise ValueError(f'{p.name} already has {MARK}')
        p.write_text(f(text))
    current = inputs(output)
    changed = {a['path'] for a, b in zip(result['quartus_sources'], current) if a != b}
    if changed != {str(p) for p, _ in files}:
        raise ValueError(f'unexpected input changes: {sorted(changed)}')
    hold_guard(output, current)
    result['quartus_sources'] = current
    (output / 'fit-inputs.json').write_text(json.dumps(result, indent=2) + '\n')
    return dict(source=str(source), changed=sorted(changed))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    print(prepare(a.source, a.output))
