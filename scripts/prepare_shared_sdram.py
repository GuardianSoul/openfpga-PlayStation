"""Original MIRLO SDRAM PHY plus an observation-only transport drain port."""
from pathlib import Path

from prepare_resource_targets import digest
from tool_paths import ROOT


def prepare(directory):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    original=ROOT/'references/MIRLO64/rtl/soc/sdr_phy.sv'
    text=original.read_text()
    before='    output wire         sdram_clock\n'
    if text.count(before)!=1 or text.count('module sdr_phy (')!=1:
        raise ValueError('MIRLO PHY drain interface anchor changed')
    text=text.replace('module sdr_phy (','module psx_sdr_phy_status (')
    text=text.replace(before,'    output wire         sdram_clock,\n    output wire         traffic_idle\n')
    before='\nendmodule\n'
    if text.count(before)!=1:raise ValueError('MIRLO PHY module end changed')
    text=text.replace(before,'''
// Observation only: no new state or change to any original output.
assign traffic_idle = !r_wrdata_en && !wr_en_d && !oe_reg && !r_rddata_en &&
    rden_sr == 0 && !f_rdvalid && !rddata_valid;
endmodule
''')
    path=directory/'psx_sdr_phy_status.sv';path.write_text(text)
    return dict(path=str(path),sha256=digest(path),library='work',assignment='SYSTEMVERILOG_FILE',
        original=str(original),original_sha256=digest(original),
        transform='Module rename and combinational observation-only traffic_idle port',
        original_output_behavior_changed=False,hardware_verified=False,timing_verified=False)
