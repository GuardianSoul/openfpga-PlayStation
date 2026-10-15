"""Source-bound Pocket SDRAM PHY matching the successful hardware sweep.

Only the module name changes. The retained rising-edge input pipeline requires
the measured 62.5/125 MHz clocks and 4 ns forwarded SDRAM clock phase. A CRC
pass on one Pocket is scoped hardware evidence, not all-corner timing closure.
"""
from pathlib import Path
from prepare_resource_targets import digest
from tool_paths import ROOT


def prepare(directory):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    original=ROOT/'references/MIRLO64/rtl/soc/sdr_phy.sv'
    text=original.read_text()
    if text.count('module sdr_phy (')!=1:raise ValueError('Pinned MIRLO PHY module declaration changed')
    path=directory/'psx_sdr_phy.sv'
    path.write_text('// MIRLO64 c0712c0 hardware baseline: module name only.\n'+
        text.replace('module sdr_phy (','module psx_sdr_phy ('))
    return dict(path=str(path),sha256=digest(path),original=str(original),original_sha256=digest(original),
        library='work',assignment='SYSTEMVERILOG_FILE',transform='module name only',
        required_clocks_MHz={'sys':62.5,'sys2x':125,'SDRAM':125},SDRAM_clock_phase_ps=4000,
        hardware_evidence='docs/pocket-sweep-probe.md, commit 214f76d: full BIOS CRC on the test Pocket',
        new_project_hardware_verified=False,all_corner_timing_verified=False)
