#!/usr/bin/env python3
"""MDEC lookup tables in logic instead of a RAM block of their own.

Quartus turns two small MDEC tables into ROMs: the zigzag order (64 x 6,
Mux32) and an 8 x 1 decode (Mux33). Each takes a whole M10K, or once the
M10K are full (save states, ss058e #883) a whole MLAB LAB, for a few LUTs of
logic. This step switches ROM recognition off for the mdec entity only, so
both stay logic (the zigzag is six 6-input functions). Nothing else changes:
the tables keep the same registered reads.

Edits the project's own mdec.vhd copy (prepare_mdec_beetle.py output) in
place; run after that step. Usage: prepare_mdec_rom_logic.py PROJECT
"""
import re
import sys
from pathlib import Path

MARK = 'mdec-rom-logic'
ATTR = ('   -- ' + MARK + ': tables as logic, not a RAM block each (prepare_mdec_rom_logic.py)\n'
        '   attribute altera_attribute : string;\n'
        '   attribute altera_attribute of arch : architecture is "-name AUTO_ROM_RECOGNITION OFF";\n')


def rom_logic_text(text):
    if MARK in text: raise ValueError(f'{MARK}: already applied')
    if len(re.findall(r'^architecture arch of mdec is\n', text, re.M)) != 1: raise ValueError(f'{MARK}: mdec architecture not found')
    if 'constant zigzag' not in text: raise ValueError(f'{MARK}: zigzag table not found')
    head = text.index('architecture arch of mdec is\n')
    begins = [m.start() for m in re.finditer(r'^begin[ \t]*\n', text, re.M) if m.start() > head]
    if not begins: raise ValueError(f'{MARK}: architecture begin not found')
    return text[:begins[0]] + ATTR + '\n' + text[begins[0]:]


def main():
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1].strip())
    project = Path(sys.argv[1]).resolve()
    q = (project/'apf_top.qsf').read_text()
    hits = sorted(set(re.findall(r'"([^"]*/mdec\.vhd)"', q)))
    if len(hits) != 1: raise SystemExit(f'{MARK}: expected one mdec.vhd in the QSF, found {hits}')
    path = Path(hits[0])
    if project not in path.resolve().parents: raise SystemExit(f'{MARK}: {path} is not a project copy (run prepare_mdec_beetle.py first)')
    path.write_text(rom_logic_text(path.read_text()))
    print(MARK, '->', path)


if __name__ == '__main__':
    main()
