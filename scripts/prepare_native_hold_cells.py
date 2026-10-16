#!/usr/bin/env python3
"""Opt-in identity LUTs on three measured Native synchronous hold paths.

Adds no state, guest cycle, clock or timing exception. This does not select a
production configuration or assert a fixed delay; fresh full fitting and all
ordinary timing/physical-presence checks remain required.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

from measure_pocket_seeds import inputs
from prepare_native_hold_route import REVIEWED
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest
from tool_paths import ROOT


def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Native identity-LUT anchor changed: '+old.strip()[:60])
    return text.replace(old, new)


def dma_text(text):
    if digest_text(text) != REVIEWED['dma.vhd']:
        raise ValueError('Unreviewed Native identity-LUT DMA')
    text = replace(text,
        '   signal nextAddr            : std_logic_vector(23 downto 0);\n',
        '   signal nextAddr            : std_logic_vector(23 downto 0);\n'
        '   signal pocket_hold_next11 : std_logic;\n'
        '   component psx_hold_cell is\n'
        '      port (i : in std_logic; o : out std_logic);\n'
        '   end component;\n')
    text = replace(text, 'begin \n\n   toDevice <=',
        'begin \n\n   pocket_hold_dma11 : psx_hold_cell\n'
        '      port map (i => nextAddr(11), o => pocket_hold_next11);\n\n'
        '   toDevice <=')
    return replace(text, 'dmaSettings.D_MADR <= unsigned(nextAddr);',
        "dmaSettings.D_MADR <= unsigned(std_logic_vector'(nextAddr(23 downto 12) & "
        'pocket_hold_next11 & nextAddr(10 downto 0)));')


def sdram_text(text):
    if digest_text(text) != REVIEWED['psx_sdram_ctrl.sv']:
        raise ValueError('Unreviewed Native identity-LUT SDRAM')
    text = replace(text, '    reg [31:0] wdata = 0;\n',
        '    reg [31:0] wdata = 0;\n'
        '    wire pocket_hold_sdr4, pocket_hold_sdr26;\n'
        '    wire [31:0] pocket_hold_wdata = {wdata[31:27], pocket_hold_sdr26,\n'
        '        wdata[25:5], pocket_hold_sdr4, wdata[3:0]};\n'
        '    psx_hold_cell pocket_hold_sdr4_cell(.i(wdata[4]), .o(pocket_hold_sdr4));\n'
        '    psx_hold_cell pocket_hold_sdr26_cell(.i(wdata[26]), .o(pocket_hold_sdr26));\n')
    if text.count('dq_out <= wdata[') != 3:
        raise ValueError('Native SDRAM write-data consumers changed')
    return text.replace('dq_out <= wdata[', 'dq_out <= pocket_hold_wdata[')


def digest_text(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def prepare_project(directory, expected_top_sha256):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT/'build') or subprocess.run(
            ['git', 'check-ignore', '--quiet', str(directory/'native-hold-cells/manifest.json')],
            cwd=ROOT).returncode:
        raise ValueError('Fresh ignored build project required')
    if any((directory/n).exists() for n in (
            'db', 'incremental_db', 'output_files', 'native-hold-cells', 'native-hold-route')):
        raise ValueError('Native identity LUTs require fresh parent before map/fit')
    qsf, core = directory/'apf_top.qsf', directory/'core_top.sv'
    q = qsf.read_text()
    selected = assignments(directory, q)
    if digest(core) != expected_top_sha256 or [p for k, p in selected
            if k == 'SYSTEMVERILOG_FILE' and p.name == 'core_top.sv'] != [core]:
        raise ValueError('Expected identity-checked canonical top')
    originals = {}
    for name, sha in REVIEWED.items():
        paths = {p for _, p in selected if p.name == name}
        if len(paths) != 1 or digest(next(iter(paths))) != sha:
            raise ValueError('Unreviewed Native identity-LUT source '+name)
        originals[name] = next(iter(paths))
        if q.count('"'+str(originals[name])+'"') != 1:
            raise ValueError('Require one quoted Native source assignment')
    before = inputs(directory)
    reserves = []
    for row in before:
        if row['assignment'] in ('SDC_FILE', 'SDC_SOURCE', 'PROJECT'):
            reserves += re.findall(r'^\s*set pocket_cpu_hold_ns\s+(\S+)\s*$',
                                   Path(row['path']).read_text(), re.M)
    if len(reserves) != 1 or float(reserves[0]) != .15:
        raise ValueError('Expected retained150ps CPU reserve')
    generated = {
        'dma.vhd': dma_text(originals['dma.vhd'].read_text()),
        'psx_sdram_ctrl.sv': sdram_text(originals['psx_sdram_ctrl.sv'].read_text()),
    }
    work = directory/'native-hold-cells'
    work.mkdir()
    for name, text in generated.items():
        path = work/name
        path.write_text(text)
        q = q.replace('"'+str(originals[name])+'"', '"'+str(path)+'"')
    cell = work/'psx_hold_cell.sv'
    shutil.copyfile(ROOT/'rtl/pocket/psx_hold_cell.sv', cell)
    if any(digest(Path(r['path'])) != r['sha256'] for r in before):
        raise ValueError('Native identity-LUT inputs changed during preparation')
    qsf.write_text(q+f'\nset_global_assignment -name SYSTEMVERILOG_FILE "{cell}"\n')
    result = dict(original_inputs=before,
        added=[dict(path=str(work/n), sha256=digest(work/n)) for n in (*generated, cell.name)],
        selected_bits=dict(dma=[11], sdram=[4, 26]), additional_registers=0,
        additional_guest_cycles=0, CPU_hold_reserve_ns=.15,
        original_clocks_and_all_timing_constraints_retained=True,
        assumed_fixed_cell_delay_ns=None, preparer_sha256=digest(Path(__file__)),
        production_selected=False, fit_verified=False, hardware_verified=False)
    (work/'manifest.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--expected-top-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_project(args.project, args.expected_top_sha256), indent=2))
