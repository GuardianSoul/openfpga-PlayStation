#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""GTE microcode ROM in two M10K blocks instead of three, exact.

PocketGTEMicroCode's ROM is 256 x 87 bits (MCodeEntry), three M10K in 256 x 40
mode, the third holding 7 bits. Its 87 columns hold only 67 distinct ones, one
of them constant, so a 66-bit ROM plus wiring gives the same entries: two M10K.

Why it matters: the save-state builds need 309 blocks of the 308. Quartus then
packs the GTE divider's 512 x 10 reciprocal ROM into the microcode ROM's
third block (fit report: one location for both), and on hardware every boot
loses the BIOS PlayStation logo, which the GTE draws (0.5.9-test6 ... test8t;
test9r, without the 4-block save ROM, keeps it). With this step the build
needs 308 and nothing has to share a block.

The narrowed module registers the 66-bit word on the same clock and enable as
the original registers its entry; the 87-bit entry is pure wiring after it.
The table is the one Quartus builds (unassigned and don't-care bits as 0):
rtl/gte/gte_microcode_table.hex, elaborated from MicroCode.inl (pinned by its
SHA-256 below) and checked against Quartus's MIF and in lockstep by
scripts/run_gte_microcode_narrow.py.
"""
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / 'rtl/gte/gte_microcode_table.hex'
WIDTH, DEPTH = 87, 256
INL_SHA256 = 'c8e3a5b69af411901d10ad37c80ce88632fafa90dfc87a4212afe1364083f382'   # PS-FPGA pin's rtl/gte/rtl/MicroCode.inl


def read_table(path=TABLE):
    rows = Path(path).read_text().split()
    if len(rows) != DEPTH or any(not re.fullmatch(r'[0-9a-f]{22}', r) or int(r, 16) >> WIDTH for r in rows):
        raise ValueError('GTE microcode table must be 256 words of 87 bits')
    return [int(r, 16) for r in rows]


def plan(words):
    """-> (columns, sources): columns = the original bit index stored at each narrow bit;
    sources[b] = ('rom', narrow index) or ('const', value) for every one of the 87 bits."""
    first, columns, sources = {}, [], []
    for b in range(WIDTH):
        column = tuple((w >> b) & 1 for w in words)
        if len(set(column)) == 1:
            sources.append(('const', column[0]))
        elif column in first:
            sources.append(('rom', first[column]))
        else:
            first[column] = len(columns)
            sources.append(('rom', len(columns)))
            columns.append(b)
    return columns, sources


def narrow_words(words, columns):
    return [sum(((w >> b) & 1) << i for i, b in enumerate(columns)) for w in words]


def expand(narrow, sources):
    return sum((value if kind == 'const' else (narrow >> value) & 1) << b for b, (kind, value) in enumerate(sources))


def narrow_text(text, hex_path, sources, width, module=None):
    """The QSF-selected GTEMicroCode.sv with its ROM narrowed (module renamed if given)."""
    old_decl = '\tMCodeEntry microCodeROM[255:0];\n'
    live = '\t// [Only 206:0] entries in BRAM if FAST ONLY + counter seperate.\n'
    old_read = live + '\talways @(posedge i_clk) if (i_ce) begin\n\t\tcurrentEntry <= microCodeROM[i_PC];\n\tend\n'
    old_init = '\tinitial begin\n\t\t`include "MicroCode.inl"\n\tend\n'
    for anchor in (old_decl, old_read, old_init):
        if text.count(anchor) != 1:
            raise ValueError('gte microcode narrow: anchor changed: ' + anchor.strip().splitlines()[0])
    bits = ', '.join((f"1'b{value}" if kind == 'const' else f'narrowQ[{value}]') for kind, value in reversed(sources))
    text = text.replace(old_decl,
        f'\t// gte-microcode-narrow: the 87-bit entries hold {width} distinct non-constant columns\n'
        f'\t// (prepare_gte_microcode_narrow.py): two M10K, the entry rebuilt by wiring\n'
        f'\t(* ramstyle = "M10K" *) reg [{width - 1}:0] narrowROM[255:0];\n'
        f'\treg [{width - 1}:0] narrowQ;\n', 1)
    text = text.replace(old_read,
        live + '\talways @(posedge i_clk) if (i_ce) begin\n\t\tnarrowQ <= narrowROM[i_PC];\n\tend\n'
        f'\tassign currentEntry = {{{bits}}};\n', 1)
    text = text.replace(old_init, f'\tinitial $readmemh("{hex_path}", narrowROM);\n', 1)
    text = text.replace('\tMCodeEntry currentEntry;\n', '\tMCodeEntry currentEntry;   // gte-microcode-narrow: wiring from narrowQ\n', 1)
    if module:
        text, n = re.subn(r'\bmodule PocketGTEMicroCode\b', 'module ' + module, text)
        if n != 1:
            raise ValueError('gte microcode narrow: module name changed')
    return text


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare_project(directory):
    from prepare_pocket_config_cdc import assignments
    directory = Path(directory).resolve()
    if any((directory / name).exists() for name in ('db', 'incremental_db', 'output_files')):
        raise ValueError('gte microcode narrow must precede map/fit')
    qsf = directory / 'apf_top.qsf'
    qtext = qsf.read_text()
    selected = [p for _, p in assignments(directory, qtext) if p.name == 'GTEMicroCode.sv']
    if len(selected) != 1:
        raise ValueError('gte microcode narrow: expected one selected GTEMicroCode.sv')
    source = selected[0]
    inl = [Path(p) / 'MicroCode.inl' for p in re.findall(r'SEARCH_PATH "([^"]+)"', qtext) if (Path(p) / 'MicroCode.inl').is_file()]
    if len(inl) != 1 or sha256(inl[0]) != INL_SHA256:
        raise ValueError('gte microcode narrow: MicroCode.inl is not the one the table was built from')
    words = read_table()
    columns, sources = plan(words)
    work = directory / 'gte-microcode-narrow'
    work.mkdir()
    hex_path = work / 'gte_microcode_narrow.hex'
    hex_path.write_text(''.join(f'{w:0{(len(columns) + 3) // 4}x}\n' for w in narrow_words(words, columns)))
    out = work / 'GTEMicroCode.sv'
    out.write_text(narrow_text(source.read_text(), hex_path, sources, len(columns)))
    spelled = [s for s in ('"' + str(source) + '"', str(source)) if re.search(' ' + re.escape(s) + r'(\s|$)', qtext)]
    if not spelled:
        raise ValueError('gte microcode narrow: qsf assignment not found')
    qsf.write_text(re.sub(' ' + re.escape(spelled[0]) + r'(\s|$)', lambda m: ' "' + str(out) + '"' + m.group(1), qtext, count=1))
    return {'source': str(source), 'columns': len(columns), 'constant_bits': sum(k == 'const' for k, _ in sources)}


if __name__ == '__main__':
    import argparse, json, sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('project', type=Path)
    print(json.dumps(prepare_project(parser.parse_args().project), indent=2))
