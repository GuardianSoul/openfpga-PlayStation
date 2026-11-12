#!/usr/bin/env python3
"""Opt-in identity LUTs on the two 0.5.5 main-recipe hold paths (#209).

Jenkins #209 (main recipe + pad-table, gpu-recip, CD response mux, diffmux;
seed 13) missed the retained 150 ps CPU hold reserve on two fast-corner
paths only:
  -0.040 ns gpu_pixelpipeline stage3_cb[6] -> stage4_cb[6]   (clk2x)
  -0.033 ns psx_sdram_ctrl dma_data[0] -> DMA SyncFifo M10K   (clk3x)
This puts rtl/pocket/psx_hold_cell.sv (one dont_touch identity LUT) on every
bit of both buses: the 24 stage3 colour bits where stage 4 takes them, and
the 32 SDRAM DMA data bits between the controller and psx_core_memory's
output. Placement moves with every fit, so the whole bus gets a cell, not
only the bit that failed. No register, state, guest cycle, clock or timing
exception is added; the 150 ps reserve stays assigned.

Fit #1188/#1192 (save-states build, seed 12) then missed hold by up to
0.33 ns on the clk3x -> clk1x crossing psx_sdram_ctrl ch3_dout[*] -> SPU
cache spu_q[*]. That crossing is now fixed in psx_core_memory itself (a
falling-edge clk1x register on the ch3 read data), so it needs no cells. Run after the
DualShock fit helper and the other source helpers, before map/fit; fresh
full fitting and every ordinary timing check remain required.

Sign-off #1205 (candidate D2, seed 6) then missed fast-0C hold by 0.001 ns
on gpu_pixelpipeline stage3_tex_r[0] -> stage4_tex_r[3]; the next rows
(+0.039, +0.088, +0.114, +0.141 ns) are the same bus. --tex adds one cell on
each of the 15 stage3_tex_r/g/b bits and on stage3_tex_alpha where stage 4
takes them (16 cells). Without --tex the output is what it was. For a
project that already has the colour cells (D2: the tag look-ahead ran after
this step) use --top-up SOURCE --output OUT: a relocated snapshot of the
frozen project whose gpu_pixelpipeline.vhd gets only the 16 texture cells.
Check: scripts/run_main_hold_cells.py (--tex, --composed).
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


def pixel_text(text, tex=False):
    """stage4_cr/cg/cb take stage3_* through identity LUTs (both branches); with tex also
    stage4_tex_r/g/b/alpha (pixel_tex_text)."""
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
    return pixel_tex_text(text) if tex else text


TEX_DECL = ('   signal pocket_hold_t3, pocket_hold_t4 : std_logic_vector(15 downto 0);\n'
            '   signal pocket_hold_tex_r, pocket_hold_tex_g, pocket_hold_tex_b : unsigned(4 downto 0);\n'
            '   signal pocket_hold_tex_alpha : std_logic;\n')
TEX_AFTER = '   pocket_hold_cb <= unsigned(pocket_hold_c4(7 downto 0));\n'
TEX_BLOCK = ('   -- prepare_main_hold_cells.py --tex: identity LUTs on stage3 -> stage4 texture colour\n'
             '   pocket_hold_t3 <= stage3_tex_alpha & std_logic_vector(stage3_tex_r) & std_logic_vector(stage3_tex_g) & std_logic_vector(stage3_tex_b);\n'
             '   pocket_hold_texture : for b in 0 to 15 generate\n'
             '      hold_cell : psx_hold_cell port map (i => pocket_hold_t3(b), o => pocket_hold_t4(b));\n'
             '   end generate;\n'
             '   pocket_hold_tex_alpha <= pocket_hold_t4(15);\n'
             '   pocket_hold_tex_r <= unsigned(pocket_hold_t4(14 downto 10));\n'
             '   pocket_hold_tex_g <= unsigned(pocket_hold_t4(9 downto 5));\n'
             '   pocket_hold_tex_b <= unsigned(pocket_hold_t4(4 downto 0));\n')
TEX_USES = [(f'stage4_tex_{c}       <= stage3_tex_{c} & "000";', f'stage4_tex_{c}       <= pocket_hold_tex_{c} & "000";')
            for c in 'rgb'] + [('stage4_tex_alpha   <= stage3_tex_alpha;', 'stage4_tex_alpha   <= pocket_hold_tex_alpha;')]
COLOUR_DECL_END = '   signal pocket_hold_cr, pocket_hold_cg, pocket_hold_cb : unsigned(7 downto 0);\n'


def pixel_tex_text(text):
    """stage4_tex_r/g/b/alpha take stage3_tex_* through identity LUTs. The input already
    has the colour cells (pixel_text); nothing else changes."""
    if 'pocket_hold_c3' not in text:
        raise ValueError('gpu_pixelpipeline has no colour hold cells yet')
    if 'pocket_hold_t3' in text:
        raise ValueError('gpu_pixelpipeline already has texture hold cells')
    # Stage 4 is the only reader of these registers besides stage 3's own writes.
    for name, count in (('stage3_tex_r', 3), ('stage3_tex_g', 3), ('stage3_tex_b', 3), ('stage3_tex_alpha', 4)):
        if len(re.findall(rf'\b{name}\b', text)) != count:
            raise ValueError(f'{name} readers changed')
    text = replace(text, COLOUR_DECL_END, COLOUR_DECL_END + TEX_DECL)
    text = replace(text, TEX_AFTER, TEX_AFTER + TEX_BLOCK)
    for old, new in TEX_USES:
        text = replace(text, old, new)
    return text


def memory_text(text):
    """psx_core_memory's dma_data output through identity LUTs."""
    if 'pocket_hold_' in text:
        raise ValueError('psx_core_memory already has hold cells')
    text = replace(text, '.dma_data(dma_data),', '.dma_data(pocket_hold_dma_raw),')
    at = text.index('    psx_sdram_ctrl #(')
    block = ('    // prepare_main_hold_cells.py: identity LUTs on the SDRAM DMA data\n'
             '    wire [31:0] pocket_hold_dma_raw;\n'
             '    genvar pocket_hold_bit;\n'
             '    generate for (pocket_hold_bit = 0; pocket_hold_bit < 32; pocket_hold_bit = pocket_hold_bit + 1) begin : dma_data_hold\n'
             '        psx_hold_cell hold_cell(.i(pocket_hold_dma_raw[pocket_hold_bit]), .o(dma_data[pocket_hold_bit]));\n'
             '    end endgenerate\n')
    if text.count('    psx_sdram_ctrl #(') != 1:
        raise ValueError('psx_core_memory controller instance changed')
    return text[:at] + block + text[at:]


def prepare_project(directory, tex=False):
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
    for name, fn in (('gpu_pixelpipeline.vhd', lambda t: pixel_text(t, tex)), ('psx_core_memory.sv', memory_text)):
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
    result = dict(added=added, leaf_sha256=LEAF,
                  cells=dict(pixel_colour=24, dma_data=32, **(dict(pixel_tex=16) if tex else {})),
                  additional_registers=0, additional_guest_cycles=0, CPU_hold_reserve_ns=.15,
                  original_clocks_and_all_timing_constraints_retained=True,
                  assumed_fixed_cell_delay_ns=None, preparer_sha256=digest(Path(__file__)),
                  production_selected=False, fit_verified=False, hardware_verified=False)
    (work / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def top_up(source, output, seed=None, baseline=False):
    """A relocated snapshot of a frozen project that already has the colour cells; its
    gpu_pixelpipeline.vhd gets the 16 texture cells (not with baseline). SOURCE is not modified."""
    import hashlib
    from run_frozen_pocket_fit import hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    manifest = load_source(source, source / 'fit-inputs.json')
    rows = [r for r in manifest['quartus_sources'] if Path(r['path']).name == 'gpu_pixelpipeline.vhd']
    cells = [r for r in manifest['quartus_sources'] if Path(r['path']).name == 'psx_hold_cell.sv']
    if len(rows) != 1 or len(cells) != 1 or cells[0]['sha256'] != LEAF:
        raise ValueError('Expected one gpu_pixelpipeline.vhd and the reviewed identity LUT in the project')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    pixel = next(Path(r['path']) for r in before if Path(r['path']).name == 'gpu_pixelpipeline.vhd')
    old = pixel.read_text()
    if hashlib.sha256(old.encode()).hexdigest() != rows[0]['sha256']:
        raise ValueError('Snapshot changed gpu_pixelpipeline.vhd')
    if not baseline:
        pixel.write_text(pixel_tex_text(old))
        after = inputs(output)
        a, b = ({r['path']: r for r in rs} for rs in (after, before))
        if a.keys() != b.keys() or {p for p in b if a[p] != b[p]} != {str(pixel)}:
            raise ValueError('Expected only gpu_pixelpipeline.vhd to change')
        hold_guard(output, after)
        manifest['quartus_sources'] = after
        (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
        load_source(output, output / 'fit-inputs.json')
    if digest(Path(rows[0]['path'])) != rows[0]['sha256']:
        raise ValueError('The source project changed')
    return dict(snapshot=str(output), seed=seed, baseline=baseline, source_sha256=rows[0]['sha256'],
                path=str(pixel), sha256=digest(pixel), cells=dict(pixel_tex=0 if baseline else 16),
                additional_registers=0, additional_guest_cycles=0, fit_verified=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('project', type=Path)
    parser.add_argument('--tex', action='store_true', help='also 16 cells on stage3_tex -> stage4_tex')
    parser.add_argument('--top-up', action='store_true',
                        help='PROJECT is a frozen project with the colour cells: snapshot it to --output and add the texture cells')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--baseline', action='store_true', help='with --top-up: snapshot only')
    args = parser.parse_args()
    if args.top_up:
        if not args.output:
            parser.error('--output required with --top-up')
        print(json.dumps(top_up(args.project, args.output, args.seed, args.baseline), indent=2))
    else:
        print(json.dumps(prepare_project(args.project, args.tex), indent=2))
