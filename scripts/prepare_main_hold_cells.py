#!/usr/bin/env python3
"""Opt-in identity LUTs on the two 0.5.5 main-recipe hold paths (#209).

Jenkins #209 (main recipe + pad-table, gpu-recip, CD response mux, diffmux;
seed 13) missed the retained 150 ps CPU hold reserve on two fast-corner
paths only:
  -0.040 ns gpu_pixelpipeline stage3_cb[6] -> stage4_cb[6]   (clk2x)
  -0.033 ns psx_sdram_ctrl dma_data[0] -> DMA SyncFifo M10K   (clk3x)
This puts rtl/pocket/psx_hold_cell.sv (one dont_touch identity LUT) on every
bit of both buses: the 24 stage3 colour bits where stage 4 takes them, and
the 32 SDRAM DMA data bits between the controller and psx_native_memory's
output. Placement moves with every fit, so the whole bus gets a cell, not
only the bit that failed. No register, state, guest cycle, clock or timing
exception is added; the 150 ps reserve stays assigned. Run after the
DualShock fit helper and the other source helpers, before map/fit; fresh
full fitting and every ordinary timing check remain required.
"""
import argparse
import json
from pathlib import Path
import re
import shutil

from measure_pocket_seeds import inputs
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest
from tool_paths import ROOT

LEAF = 'a5533e51cf97214e5f113dab5ab7c26f907e48f4fa435af1ba8bb61884b2f46d'
COMPONENT = ('   component psx_hold_cell is\n'
             '      port (i : in std_logic; o : out std_logic);\n'
             '   end component;\n')


def replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError(f'main hold-cell anchor changed ({text.count(old)}x): ' + old.strip()[:70])
    return text.replace(old, new)


def pixel_text(text):
    """stage4_cr/cg/cb take stage3_* through identity LUTs (both branches)."""
    if 'pocket_hold_' in text:
        raise ValueError('gpu_pixelpipeline already has hold cells')
    text = replace(text, '   signal stage4_cb           : unsigned(7 downto 0) := (others => \'0\');\n',
                   '   signal stage4_cb           : unsigned(7 downto 0) := (others => \'0\');\n'
                   '   signal pocket_hold_c3, pocket_hold_c4 : std_logic_vector(23 downto 0);\n'
                   '   signal pocket_hold_cr, pocket_hold_cg, pocket_hold_cb : unsigned(7 downto 0);\n'
                   + COMPONENT)
    text = replace(text, '\nbegin \n',
                   '\nbegin \n\n'
                   '   -- prepare_main_hold_cells.py: identity LUTs on stage3 -> stage4 colour\n'
                   '   pocket_hold_c3 <= std_logic_vector(stage3_cr) & std_logic_vector(stage3_cg) & std_logic_vector(stage3_cb);\n'
                   '   pocket_hold_colour : for b in 0 to 23 generate\n'
                   '      hold_cell : psx_hold_cell port map (i => pocket_hold_c3(b), o => pocket_hold_c4(b));\n'
                   '   end generate;\n'
                   '   pocket_hold_cr <= unsigned(pocket_hold_c4(23 downto 16));\n'
                   '   pocket_hold_cg <= unsigned(pocket_hold_c4(15 downto 8));\n'
                   '   pocket_hold_cb <= unsigned(pocket_hold_c4(7 downto 0));\n')
    for c in ('cr', 'cg', 'cb'):
        text = re.sub(rf'(stage4_{c}\s*<= )stage3_{c}\b', rf'\1pocket_hold_{c}', text)
        if len(re.findall(rf'stage4_{c}\s*<= pocket_hold_{c}\b', text)) != 2:
            raise ValueError(f'stage4_{c} consumers changed')
    return text


def memory_text(text):
    """psx_native_memory's dma_data output through identity LUTs."""
    if 'pocket_hold_' in text:
        raise ValueError('psx_native_memory already has hold cells')
    text = replace(text, '.dma_data(dma_data),', '.dma_data(pocket_hold_dma_raw),')
    at = text.index('    psx_sdram_ctrl #(')
    block = ('    // prepare_main_hold_cells.py: identity LUTs on the SDRAM DMA data\n'
             '    wire [31:0] pocket_hold_dma_raw;\n'
             '    genvar pocket_hold_bit;\n'
             '    generate for (pocket_hold_bit = 0; pocket_hold_bit < 32; pocket_hold_bit = pocket_hold_bit + 1) begin : dma_data_hold\n'
             '        psx_hold_cell hold_cell(.i(pocket_hold_dma_raw[pocket_hold_bit]), .o(dma_data[pocket_hold_bit]));\n'
             '    end endgenerate\n')
    if text.count('    psx_sdram_ctrl #(') != 1:
        raise ValueError('psx_native_memory controller instance changed')
    return text[:at] + block + text[at:]


def prepare_project(directory):
    directory = Path(directory).resolve()
    work = directory / 'main-hold-cells'
    if any((directory / n).exists() for n in ('db', 'incremental_db', 'output_files', work.name)):
        raise ValueError('Main hold cells require a fresh project before map/fit')
    leaf = ROOT / 'rtl/pocket/psx_hold_cell.sv'
    if digest(leaf) != LEAF:
        raise ValueError('Reviewed identity LUT required')
    qsf = directory / 'apf_top.qsf'
    q = qsf.read_text()
    selected = assignments(directory, q)
    if any(p.name == 'psx_hold_cell.sv' for _, p in selected):
        raise ValueError('Project already assigns psx_hold_cell.sv')
    reserves = []
    before = inputs(directory)
    for row in before:
        if row['assignment'] in ('SDC_FILE', 'SDC_SOURCE', 'PROJECT'):
            reserves += re.findall(r'^\s*set pocket_cpu_hold_ns\s+(\S+)\s*$', Path(row['path']).read_text(), re.M)
    if len(reserves) != 1 or float(reserves[0]) != .15:
        raise ValueError('Expected the retained 150 ps CPU reserve')
    rows = {}
    for name, fn in (('gpu_pixelpipeline.vhd', pixel_text), ('psx_native_memory.sv', memory_text)):
        paths = {p for _, p in selected if p.name == name}
        if len(paths) != 1:
            raise ValueError('Expected one selected ' + name)
        source = paths.pop()
        token = '"' + str(source) + '"'
        if q.count(token) != 1:
            raise ValueError('Expected one quoted assignment of ' + name)
        rows[name] = (source, fn(source.read_text()))
    work.mkdir()
    added = []
    for name, (source, text) in rows.items():
        out = work / name
        out.write_text(text)
        q = q.replace('"' + str(source) + '"', '"' + str(out) + '"')
        added.append(dict(name=name, source=str(source), source_sha256=digest(source),
                          path=str(out), sha256=digest(out)))
    cell = work / leaf.name
    shutil.copyfile(leaf, cell)
    if any(digest(Path(r['path'])) != r['sha256'] for r in before):
        raise ValueError('Inputs changed during preparation')
    qsf.write_text(q + f'\nset_global_assignment -name SYSTEMVERILOG_FILE "{cell}"\n')
    result = dict(added=added, leaf_sha256=LEAF, cells=dict(pixel_colour=24, dma_data=32),
                  additional_registers=0, additional_guest_cycles=0, CPU_hold_reserve_ns=.15,
                  original_clocks_and_all_timing_constraints_retained=True,
                  assumed_fixed_cell_delay_ns=None, preparer_sha256=digest(Path(__file__)),
                  production_selected=False, fit_verified=False, hardware_verified=False)
    (work / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    print(json.dumps(prepare_project(parser.parse_args().project), indent=2))
