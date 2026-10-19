#!/usr/bin/env python3
"""Reproduce the experimental Fill/B6 card and SDRAM combinational hold repair.

No registers, clocks, protocol states or guest cycles are added. A prepared
snapshot is not a timing qualification; fit it through the Jenkins queue.
"""
import argparse
import hashlib
import json
from pathlib import Path

from run_frozen_pocket_fit import digest, load_source, require, snapshot, write_json

CARD = 'd45062c6ff2be98c1f6f92822cf172d5351691d5d377d4b2aaf5ebe26280aee1'
SDRAM = '71f8ebae2beadb49023596f09552142fd84a5aaeb1752fd481c3553fc38427e7'
LEAF = 'a5533e51cf97214e5f113dab5ab7c26f907e48f4fa435af1ba8bb61884b2f46d'
CARD_OUT = 'f4299e5b9a02e293aad448d96aa2e25e6de1f0a3a5b782b96a4b4f9a2bc49b03'
SDRAM_OUT = '06207371e81e7c4d64d1e970781e836ec6c1d2917202635adc9417c026176b8e'
PROFILE = {
    'joypad_mem.vhd': CARD,
    'psx_sdram_ctrl.sv': SDRAM,
    'spu.vhd': 'b6bc99e013999b2f210aa4ec8281425d08aa4785d7947b1e71965c02a76aa795',
    'psx_spu_gaussian_mac.sv': '0fa129dbefc39e29a4e2936fdcb7fdc226d8d9f5db8ed993633e328a092851f0',
    'psx_apf_video_wide.sv': 'cd779c30a927f263c80d761fc8db9310de3c061d36feef847ca2051ec304d00a',
    # The audio coefficient filename relocates with the frozen project.
    'core_top.sv': 'b0ac7e6dbc467eaa0613af0373fc09b5fd0bc4ce6caa0fbb8cbc951a779f572c',
    'psx_top.vhd': '8f827d28e7c3349ace77e21204d3ca27acfcb4daeffd28066de41955d07ae6a9',
    'savestates.vhd': 'd6ed2c12c4fa326a92eba2e3f8e9f77dc574e0d5623d9ef1b22b9f3730e03ef7',
}


def profile_sources(target, project=None):
    found = {}
    for name, expected in PROFILE.items():
        rows = [r for r in target['quartus_sources'] if Path(r['path']).name == name]
        observed = rows[0]['sha256'] if len(rows) == 1 else None
        if len(rows) == 1 and project is not None and name == 'core_top.sv':
            observed = hashlib.sha256(Path(rows[0]['path']).read_bytes().replace(
                str(project).encode(), b'<PROJECT>')).hexdigest()
        require(len(rows) == 1 and observed == expected,
                'Exact Fill/B6 source profile required: ' + name)
        found[name] = Path(rows[0]['path'])
    require(not any(Path(r['path']).name == 'psx_hold_cell.sv'
                    for r in target['quartus_sources']), 'Unbuffered parent required')
    return found


def prepare(source, output, seed=3):
    from tool_paths import ROOT
    from measure_pocket_seeds import inputs
    source, output = Path(source).resolve(), Path(output).resolve()
    require(output.is_relative_to(ROOT / 'build'), 'Private build output required')
    parent = load_source(source, source / 'fit-inputs.json')
    profile_sources(parent, source)
    leaf = ROOT / 'rtl/pocket/psx_hold_cell.sv'
    require(digest(leaf) == LEAF, 'Reviewed identity LUT required')
    target = snapshot(source, source / 'fit-inputs.json', output, seed)
    selected = profile_sources(target, output)
    card, sdram = selected['joypad_mem.vhd'], selected['psx_sdram_ctrl.sv']
    text = card.read_text()
    marker = 'begin \n'
    require(text.count(marker) == 1, 'Card architecture anchor differs')
    text = text.replace(marker, '''   signal pocket_hold_card_data:std_logic_vector(63 downto 0);
   component psx_hold_cell is
      port(i:in std_logic;o:out std_logic);
   end component;
begin
   pocket_card_data_hold:for bit_index in 0 to 63 generate
      hold_cell:psx_hold_cell port map(i=>mem_DataInB(bit_index),o=>pocket_hold_card_data(bit_index));
   end generate;
''')
    marker = 'data_b      => mem_DataInB,'
    require(text.count(marker) == 1, 'Card RAM input anchor differs')
    card.write_text(text.replace(marker, 'data_b      => pocket_hold_card_data,'))
    text = sdram.read_text()
    marker = '    reg [31:0] wdata = 0;\n'
    require(text.count(marker) == 1, 'SDRAM write-data anchor differs')
    text = text.replace(marker, marker + '''    wire [31:0] pocket_hold_wdata;
    genvar hold_bit;
    generate for(hold_bit=0;hold_bit<32;hold_bit=hold_bit+1)begin:write_data_hold
        psx_hold_cell hold_cell(.i(wdata[hold_bit]),.o(pocket_hold_wdata[hold_bit]));
    end endgenerate
''')
    require(text.count('dq_out <= wdata[') == 3, 'SDRAM capture anchors differ')
    sdram.write_text(text.replace('dq_out <= wdata[', 'dq_out <= pocket_hold_wdata['))
    require(digest(card) == CARD_OUT and digest(sdram) == SDRAM_OUT,
            'Hold repair output differs from qualified sources')
    (output / leaf.name).write_bytes(leaf.read_bytes())
    qsf = output / 'apf_top.qsf'
    qsf.write_text(qsf.read_text() + '\nset_global_assignment -name SYSTEMVERILOG_FILE "' +
                   str(output / leaf.name) + '"\n')
    target.update(quartus_sources=inputs(output), project_sha256={
        suffix: digest(output / ('apf_top' + suffix)) for suffix in ('.qsf', '.qpf')})
    write_json(output / 'fit-inputs.json', target)
    require(load_source(output, output / 'fit-inputs.json') == target,
            'Repaired snapshot closure differs')
    require(load_source(source, source / 'fit-inputs.json') == parent, 'Original inputs changed')
    report = dict(selected=False, fit_verified=False, timing_verified=False,
                  identity_cells=96, added_registers=0, added_guest_cycles=0,
                  extra_cpu_hold_ns=.15, source_inputs=parent['quartus_sources'],
                  captured_inputs=target['quartus_sources'], preparer_sha256=digest(__file__))
    write_json(output / 'hold-repair.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--seed', type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output, args.seed), indent=2))
