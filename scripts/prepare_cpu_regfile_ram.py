#!/usr/bin/env python3
"""Private current-CPU GPR bank trial; rising writes, falling M10K reads.

Only the two 32x32 decode-read banks change. The selected CPU already uses
falling-edge tag RAM and the short memory-write output. Require its exact
reviewed bytes and the existing simple-dual-port primitive; no default change.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

from measure_pocket_seeds import inputs
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest


CPU_SHA256 = 'a4bd035801471eb0dff3dd9580821fbeae439e959ab4c97d413390cfff227af0'


def regfile_ram_text(text):
    if hashlib.sha256(text.encode()).hexdigest() != CPU_SHA256:
        raise ValueError('CPU GPR trial requires the reviewed current CPU')
    for bank in (1, 2):
        pattern = rf'   iregisterfile{bank} : entity mem.RamMLAB\n.*?\n\s*\);'
        replacement = f'''   -- GPR trial: unchanged rising write; decode consumes after the falling read.
   iregisterfile{bank} : entity work.pocket_dpram_sdp
   generic map (addr_width => 5, data_width => 32)
   port map (
      clock_a => clk1x, address_a => regs_address_a,
      data_a => regs_data_a, wren_a => regs_wren_a, q_a => open,
      clock_b => not clk1x, address_b => regs{bank}_address_b,
      data_b => (others => '0'), wren_b => '0', q_b => regs{bank}_q_b
   );'''
        text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
        if count != 1:
            raise ValueError('CPU GPR bank geometry changed')
    return text


def prepare_project(directory):
    directory = Path(directory).resolve()
    if (directory/'db').exists():
        raise ValueError('CPU GPR trial requires a fresh pre-map project')
    qsf = directory/'apf_top.qsf'
    qtext = qsf.read_text()
    selected = assignments(directory, qtext)
    cpus = {p for _, p in selected if p.name == 'cpu.vhd'}
    rams = {p for _, p in selected if p.name == 'pocket_dpram_sdp.vhd'}
    if len(cpus) != 1 or len(rams) != 1:
        raise ValueError('Expected one current CPU and SDP RAM primitive')
    cpu, ram = next(iter(cpus)), next(iter(rams))
    # Fingerprint the reviewed DUAL_PORT RAM, not an API-compatible mystery
    # primitive with a different clock, enable or output-register contract.
    from prepare_dpram_sdp import primitive
    from tool_paths import ROOT
    expected_ram = primitive((ROOT/'references/PSX_MiSTer/rtl/dpram.vhd').read_text())
    if ram.read_text() != expected_ram:
        raise ValueError('CPU GPR trial requires the reviewed SDP primitive')
    if not json.loads((directory/'manifest.json').read_text()).get('reset_only'):
        raise ValueError('CPU GPR trial is validated for reset-only builds')
    before = inputs(directory)
    text = regfile_ram_text(cpu.read_text())
    work = directory/'cpu-regfile-ram'
    work.mkdir(exist_ok=False)
    shutil.copy2(ROOT/'references/PSX_MiSTer/LICENSE', work/'COPYING.GPL2')
    output = work/'cpu.vhd'
    output.write_text(text)
    if qtext.count('"'+str(cpu)+'"') != 1:
        raise ValueError('Expected one exact CPU QSF assignment')
    if not all(digest(Path(row['path'])) == row['sha256'] for row in before):
        raise ValueError('CPU GPR trial input changed during preparation')
    qsf.write_text(qtext.replace('"'+str(cpu)+'"', '"'+str(output)+'"'))
    report = dict(original=dict(path=str(cpu), sha256=digest(cpu)),
                  candidate=dict(path=str(output), sha256=digest(output)),
                  ram=dict(path=str(ram), sha256=digest(ram)),
                  preparer_sha256=digest(Path(__file__)), original_inputs=before,
                  scope='Two decode-read GPR banks only; same rising writes and consumer edge',
                  additional_guest_cycles=0, clocks_or_constraints_changed=False,
                  fit_verified=False, timing_verified=False, game_verified=False,
                  hardware_verified=False, production_selected=False)
    (work/'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare_project(args.project), indent=2))
