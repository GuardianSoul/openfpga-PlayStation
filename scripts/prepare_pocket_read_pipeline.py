"""Retiming experiment: full-cycle IO-to-fabric SDRAM read transfer.

For the existing aligned 2:1 clocks, a falling-edge f_rddata capture sees the
same in2 value that the earlier rising-edge capture saw. Both precede the next
client rising edge. The client data pair and valid pipeline stay unchanged.
"""
from pathlib import Path
from prepare_resource_targets import digest
from tool_paths import ROOT


def prepare(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    original=ROOT/'rtl/memory/psx_sdr_phy.sv';text=original.read_text()
    marker="""always_ff @(negedge clk2x)
    if (rst2x) in2 <= '0;
    else in2 <= in1;"""
    if text.count(marker)!=1:raise ValueError('SDRAM input capture changed')
    text=text.replace(marker,"""always_ff @(negedge clk2x)
    if (rst2x) begin in2 <= '0; f_rddata <= '0; end
    else begin in2 <= in1; f_rddata <= in2; end""")
    marker="oe_reg <= 0; f_rddata <= '0; rden_sr"
    if text.count(marker)!=1:raise ValueError('SDRAM fabric reset changed')
    text=text.replace(marker,"oe_reg <= 0; rden_sr")
    marker='        f_rddata <= in2;\n'
    if text.count(marker)!=1:raise ValueError('SDRAM fabric read capture changed')
    text=text.replace(marker,'')
    text='// Generated read-pipeline retiming experiment; original attribution follows.\n'+text
    path=directory/'psx_sdr_phy.sv';path.write_text(text)
    return {'path':str(path.resolve()),'sha256':digest(path),'original':str(original),
        'original_sha256':digest(original),'library':'work','assignment':'SYSTEMVERILOG_FILE',
        'transform':'Capture f_rddata on falling clk2x; retain in2, rddata_d, client pair and valid timing',
        'required_clocks':'clk2x aligned at twice clk_sys; existing SDRAM forwarded-clock phase',
        'cycle_equivalence_verified':False,'timing_verified':False,'hardware_verified':False}
