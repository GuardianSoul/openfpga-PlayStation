#!/usr/bin/env python3
"""Check prepare_main_hold_cells.py on a prepared project's two sources.

psx_hold_cell is one identity LUT (lut_mask AAAA..., combout = dataa), so a
transform that only routes signals through it is equivalent when removing
the cells gives back the original text exactly. This checks that inverse for
gpu_pixelpipeline.vhd and psx_core_memory.sv, that every cell input/output
pairs the same bit, that the leaf's mask is the identity, and that GHDL and
Icarus elaborate the transformed sources with a behavioural cell (o = i).
The same is checked for the 16 texture cells of --tex (stage3_tex_r/g/b/alpha
-> stage4_tex_*): removing them gives back the colour-cell text exactly.
Usage: run_main_hold_cells.py PROJECT   (a project before prepare_main_hold_cells)
       run_main_hold_cells.py --composed PROJECT [--celled FILE]
--composed takes a frozen project that already has the colour cells and later
steps on top (D2: the tag look-ahead). It checks on that project's own
gpu_pixelpipeline.vhd that removing the 16 texture cells from the top-up
output gives back the project's file byte for byte, the bit pairing, two
planted faults, and a GHDL analysis with a behavioural cell. With --celled
(the pixel pipeline as prepare_main_hold_cells.py left it, before the tag
look-ahead) it also checks that the recipe order gives the same file:
lookahead(tex(celled)) == tex(lookahead(celled)) == the top-up output.
What this proves: the new file is the fitted file plus identity functions on
16 nets. It does not prove any hold margin; only a fit does.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from prepare_main_hold_cells import COMPONENT, TEX_BLOCK, TEX_DECL, TEX_USES, memory_text, pixel_tex_text, pixel_text
from prepare_pocket_config_cdc import assignments
from run_sdram_ctrl import ALTDDIO
from tool_paths import ROOT, find_tool


def pixel_inverse(text):
    text = text.replace('   signal pocket_hold_c3, pocket_hold_c4 : std_logic_vector(23 downto 0);\n'
                        '   signal pocket_hold_cr, pocket_hold_cg, pocket_hold_cb : unsigned(7 downto 0);\n'
                        + COMPONENT, '')
    text = re.sub(r'\n   -- prepare_main_hold_cells\.py:.*?pocket_hold_cb <= unsigned\(pocket_hold_c4\(7 downto 0\)\);\n',
                  '\n', text, flags=re.S)
    text = text.replace('\nbegin \n\n', '\nbegin \n', 1)
    return re.sub(r'(stage4_c[rgb]\s*<= )pocket_hold_(c[rgb])\b', r'\1stage3_\2', text)


def pixel_tex_inverse(text):
    """Remove the 16 texture cells; every removed piece must be there exactly once."""
    for piece in (TEX_DECL, TEX_BLOCK):
        assert text.count(piece) == 1, 'texture cell block changed'
        text = text.replace(piece, '')
    for old, new in TEX_USES:
        assert text.count(new) == 1, 'texture cell consumer changed'
        text = text.replace(new, old)
    return text


TEX_FAULTS = {   # planted in the transformed text; the inverse or the pairing check must object
    'tex bits swapped': ('pocket_hold_tex_g <= unsigned(pocket_hold_t4(9 downto 5));',
                         'pocket_hold_tex_g <= unsigned(pocket_hold_t4(14 downto 10));'),
    'alpha takes a colour bit': ('pocket_hold_tex_alpha <= pocket_hold_t4(15);', 'pocket_hold_tex_alpha <= pocket_hold_t4(14);'),
    'cell output crossed': ('i => pocket_hold_t3(b), o => pocket_hold_t4(b)', 'i => pocket_hold_t3(b), o => pocket_hold_t4(15 - b)'),
}


def check_tex(before, after):
    """after = before + 16 texture cells, nothing else."""
    assert pixel_tex_inverse(after) == before, 'texture transform is not an exact inverse'
    assert 'i => pocket_hold_t3(b), o => pocket_hold_t4(b)' in after and 'for b in 0 to 15 generate' in after
    assert len(re.findall(r'stage4_tex_[rgb]\s*<= pocket_hold_tex_[rgb] & "000";', after)) == 3
    assert len(re.findall(r'stage4_tex_alpha\s*<= pocket_hold_tex_alpha;', after)) == 1
    assert not re.findall(r'stage4_tex_\w+\s*<= stage3_tex_', after), 'a stage 4 texture register bypasses its cell'
    for name, (old, new) in TEX_FAULTS.items():
        assert after.count(old) == 1
        bad = after.replace(old, new)
        try:
            caught = pixel_tex_inverse(bad) != before
        except AssertionError:
            caught = True
        assert caught, f'planted fault not caught: {name}'
    return len(TEX_FAULTS)


def analyse_pixel(tmp, pick, text):
    (tmp / 'hold.vhd').write_text('library ieee; use ieee.std_logic_1164.all;\n'
                                  'entity psx_hold_cell is port (i : in std_logic; o : out std_logic); end;\n'
                                  'architecture sim of psx_hold_cell is begin o <= i; end;\n')
    (tmp / 'gpu_pixelpipeline.vhd').write_text(text)
    ghdl, quartus = find_tool('ghdl'), find_tool('quartus_sh')
    if not ghdl or not quartus:
        raise SystemExit('GHDL and the Quartus simulation library required')
    lib = Path(quartus).parents[1] / 'eda/sim_lib'
    opts = ['--std=08', '-frelaxed', '-fsynopsys']
    for d in ('altlib', 'memlib', 'work'):
        (tmp / d).mkdir()

    def analyse(work, workdir, *files):
        r = subprocess.run([ghdl, '-a', *opts, f'--work={work}', f'--workdir={workdir}',
                            '-Paltlib', '-Pmemlib', *map(str, files)], cwd=tmp, capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f'GHDL analysis failed:\n{r.stdout[-3000:]}{r.stderr[-3000:]}')
    analyse('altera_mf', 'altlib', lib / 'altera_mf_components.vhd', lib / 'altera_mf.vhd')
    analyse('mem', 'memlib', pick('RamMLAB.vhd'))
    analyse('work', 'work', pick('pGPU.vhd'), pick('dpram.vhd'), pick('pocket_dpram_sdp.vhd'),
            tmp / 'hold.vhd', tmp / 'gpu_pixelpipeline.vhd')


def composed(project, celled):
    import json
    rows = json.loads((project / 'fit-inputs.json').read_text())['quartus_sources']
    pick = lambda n: next(Path(r['path']) for r in rows if Path(r['path']).name == n)
    text = pick('gpu_pixelpipeline.vhd').read_text()
    new = pixel_tex_text(text)
    faults = check_tex(text, new)
    print(f'PASS inverse on {pick("gpu_pixelpipeline.vhd")}: 16 texture cells removed give the project file exactly; '
          f'{faults} planted faults caught')
    if celled:
        from prepare_gpu_tag_lookahead import lookahead_text
        base = Path(celled).read_text()
        assert lookahead_text(base) == text, '--celled is not what this project was built from'
        assert lookahead_text(pixel_tex_text(base)) == new, 'texture cells and tag look-ahead do not commute'
        assert pixel_text(pixel_inverse(base), tex=True) == pixel_tex_text(base)
        print('PASS recipe order: lookahead(tex(celled)) == tex(lookahead(celled)) == top-up output; '
              'pixel_text(tex=True) on the cell-free text gives the same celled text')
    with tempfile.TemporaryDirectory() as tmp:
        analyse_pixel(Path(tmp), pick, new)
    print('PASS GHDL analysis of the top-up gpu_pixelpipeline with its dependencies')
    print('PASS main hold cells (composed, texture)')


def memory_inverse(text):
    text = re.sub(r'    // prepare_main_hold_cells\.py:.*?    end endgenerate\n', '', text, flags=re.S)
    return text.replace('.dma_data(pocket_hold_dma_raw),', '.dma_data(dma_data),')


def main():
    if sys.argv[1] == '--composed':
        celled = sys.argv[sys.argv.index('--celled') + 1] if '--celled' in sys.argv else None
        return composed(Path(sys.argv[2]).resolve(), celled)
    project = Path(sys.argv[1]).resolve()
    selected = assignments(project, (project / 'apf_top.qsf').read_text())
    pick = lambda n: next(p for _, p in selected if p.name == n)
    pixel, memory = pick('gpu_pixelpipeline.vhd'), pick('psx_core_memory.sv')
    p_text, m_text = pixel.read_text(), memory.read_text()
    p_new, m_new = pixel_text(p_text), memory_text(m_text)
    assert pixel_inverse(p_new) == p_text, 'pixel transform is not an exact inverse'
    assert memory_inverse(m_new) == m_text, 'memory transform is not an exact inverse'
    assert len(re.findall(r'stage4_c[rgb]\s*<= pocket_hold_c[rgb]\b', p_new)) == 6
    assert 'i => pocket_hold_c3(b), o => pocket_hold_c4(b)' in p_new
    assert '.i(pocket_hold_dma_raw[pocket_hold_bit]), .o(dma_data[pocket_hold_bit])' in m_new
    leaf = (ROOT / 'rtl/pocket/psx_hold_cell.sv').read_text()
    assert ".lut_mask(64'hAAAAAAAAAAAAAAAA)" in leaf and '.dataa(i), .combout(o)' in leaf
    # one negative: a cell that swaps two bits must break the inverse
    bad = p_new.replace('pocket_hold_cb <= unsigned(pocket_hold_c4(7 downto 0));',
                        'pocket_hold_cb <= unsigned(pocket_hold_c4(15 downto 8));')
    assert pixel_inverse(bad) != p_text, 'swapped-bit fault not caught'
    print('PASS inverse: 24 colour cells, 32 DMA data cells, identity leaf; swapped-bit fault caught')
    p_tex = pixel_text(p_text, tex=True)
    assert p_tex == pixel_tex_text(p_new)
    faults = check_tex(p_new, p_tex)
    assert pixel_inverse(pixel_tex_inverse(p_tex)) == p_text
    print(f'PASS inverse with --tex: 16 texture cells on top of the colour cells; {faults} planted faults caught')

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'hold.vhd').write_text('library ieee; use ieee.std_logic_1164.all;\n'
                                      'entity psx_hold_cell is port (i : in std_logic; o : out std_logic); end;\n'
                                      'architecture sim of psx_hold_cell is begin o <= i; end;\n')
        (tmp / 'gpu_pixelpipeline.vhd').write_text(p_new)
        ghdl, quartus = find_tool('ghdl'), find_tool('quartus_sh')
        if not ghdl or not quartus:
            raise SystemExit('GHDL and the Quartus simulation library required')
        lib = Path(quartus).parents[1] / 'eda/sim_lib'
        opts = ['--std=08', '-frelaxed', '-fsynopsys']
        for d in ('altlib', 'memlib', 'work'):
            (tmp / d).mkdir()

        def analyse(work, workdir, *files):
            r = subprocess.run([ghdl, '-a', *opts, f'--work={work}', f'--workdir={workdir}',
                                '-Paltlib', '-Pmemlib', *map(str, files)], cwd=tmp, capture_output=True, text=True)
            if r.returncode:
                raise SystemExit(f'GHDL analysis failed:\n{r.stdout[-3000:]}{r.stderr[-3000:]}')
        analyse('altera_mf', 'altlib', lib / 'altera_mf_components.vhd', lib / 'altera_mf.vhd')
        analyse('mem', 'memlib', pick('RamMLAB.vhd'))
        analyse('work', 'work', pick('pGPU.vhd'), pick('dpram.vhd'), pick('pocket_dpram_sdp.vhd'),
                tmp / 'hold.vhd', tmp / 'gpu_pixelpipeline.vhd')
        print('PASS GHDL analysis of the transformed gpu_pixelpipeline with its dependencies')
        (tmp / 'hold.sv').write_text('module psx_hold_cell(input wire i, output wire o); assign o = i; endmodule\n')
        (tmp / 'psx_core_memory.sv').write_text(m_new)
        iverilog = find_tool('iverilog')
        if not iverilog:
            raise SystemExit('Icarus Verilog required')
        (tmp / 'altddio.v').write_text(ALTDDIO)   # behavioural vendor DDIO, as run_sdram_ctrl.py
        files = [tmp / 'hold.sv', tmp / 'altddio.v', pick('psx_sdram_ctrl.sv'), tmp / 'psx_core_memory.sv']
        for _ in range(8):   # add each selected module Icarus asks for, by file stem
            r = subprocess.run([iverilog, '-g2012', '-s', 'psx_core_memory', '-o', str(tmp / 'mem.vvp'),
                                *map(str, files)], capture_output=True, text=True)
            missing = re.findall(r'Unknown module type: (\w+)', r.stderr)
            extra = [p for _, p in selected if p.stem in missing and p not in files]
            if r.returncode == 0 or not extra:
                break
            files += extra
        if r.returncode:
            raise SystemExit(f'Icarus elaboration failed:\n{r.stderr[-3000:]}')
        print('PASS Icarus elaboration of psx_core_memory with the controller and', len(files) - 4, 'more modules')
    print('PASS main hold cells')


if __name__ == '__main__':
    main()
