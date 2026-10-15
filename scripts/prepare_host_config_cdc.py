#!/usr/bin/env python3
"""Prepare an opt-in coherent region/biosregion bridge for a captured Pocket top.

Only supports the explicit host register/service anchors below. Does not edit
the supplied top, select a production flag, change clocks, or handle event or
command channels. Add both returned RTL files and the scoped SDC to a private
project before measuring it. The destination instance must be core_top:ic.
"""
import argparse
import json
from pathlib import Path
import shutil

from prepare_resource_targets import digest
from tool_paths import ROOT


INSTANCE = '''    // Stable configuration levels only. Intermediate host values may coalesce.
    // This reset is independent of guest warm reset and memory-stall CE.
    (* preserve, dont_merge *) reg config_reset=1;
    always @(posedge clk_74a or negedge cpu_locked)
        if(!cpu_locked) config_reset<=1; else config_reset<=!power_reset_n;
    wire config_current;
    psx_config_cdc #(.WIDTH(4)) host_config (
        .src_clk(clk_74a),.dst_clk(clk1x),.reset_async(config_reset),
        .src_data({biosregion_host,region_host}),
        .src_current(config_current),.dst_data({biosregion,region}));
'''


def adapt_top(text):
    def replace(before, after):
        nonlocal text
        if text.count(before) != 1:
            raise ValueError('Host configuration adaptation anchor changed: '+before[:100])
        text = text.replace(before, after)

    for name, address in [('biosregion', '11000020'), ('region', '1100009c')]:
        replace(f'    reg [1:0] {name}=0;',
            f'    reg [1:0] {name}_host=0;\n    wire [1:0] {name};')
        before = f"if(bridge_wr && bridge_addr==32'h{address}) {name}<=bridge_wr_data[1:0];"
        replace(before, before.replace(f') {name}<=', f') {name}_host<='))
    replace('    wire memory_transport_reset=', INSTANCE+'    wire memory_transport_reset=')
    if "32'h12000158:" in text:
        raise ValueError('Host config status register address already occupied')
    before = "        32'h12000014: service_read=isPaused;"
    replace(before, "        32'h12000158: service_read={31'b0,config_current};\n"+before)
    return text


def prepare(top, directory, expected_sha256):
    top = Path(top).resolve()
    if digest(top) != expected_sha256:
        raise ValueError('Captured host top does not match expected identity')
    text = adapt_top(top.read_text())
    original_sha = digest(top)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    output = directory/'core_top.sv'
    output.write_text(text)
    copies = []
    for original in [ROOT/'rtl/pocket/psx_config_cdc.sv', ROOT/'scripts/pocket_config_scope.tcl']:
        sha = digest(original)
        copy = directory/original.name
        shutil.copyfile(original, copy)
        if digest(copy) != sha:
            raise ValueError('Host config input changed while copying')
        copies.append(dict(path=str(copy), sha256=sha, original=str(original)))
    if digest(top) != original_sha:
        raise ValueError('Captured host top changed during preparation')
    report = dict(top=dict(path=str(output), sha256=digest(output), original=str(top),
        original_sha256=original_sha), added=copies,
        preparer_sha256=digest(Path(__file__)),
        host_addresses=dict(biosregion='0x11000020', region='0x1100009c', current='0x12000158'),
        latest_levels_may_coalesce=True, guest_warm_reset_does_not_reset_channel=True,
        production_selected=False, timing_verified=False, hardware_verified=False,
        limitations=['Stable levels only; other CD/card/host channels remain unchanged',
            'BIOS region is latched by the existing memorymux during guest reset',
            'Do not gate runtime guest reset on current: disc/region changes must not reset the game',
            'Explicitly add the returned source/SDC files to a private project; full fit/timing required'])
    (directory/'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('top', type=Path)
    p.add_argument('output', type=Path, help='Fresh output directory')
    p.add_argument('--expected-sha256', required=True)
    a = p.parse_args()
    print(json.dumps(prepare(a.top, a.output, a.expected_sha256), indent=2))
