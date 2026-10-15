"""Measure a bounded APF interface shell from the pinned Pocket N64 reference."""
import json
from pathlib import Path
import re
import subprocess

if __package__:
    from .prepare_resource_targets import digest, write_project
    from .tool_paths import ROOT
else:
    from prepare_resource_targets import digest, write_project
    from tool_paths import ROOT

UPSTREAM = ROOT/'references/MIRLO64'
PIN = 'c0712c0a24a20ff71c39a2ae25d1b1229ae12fb5'
PLATFORM = UPSTREAM/'platform/pocket'
CORE = UPSTREAM/'target/pocket'
SAVESTATE_FIXED = {
    'savestate_supported':"1'b0", 'savestate_addr':"32'b0",
    'savestate_size':"32'b0", 'savestate_maxloadsize':"32'b0",
    **{name:"1'b0" for name in ('savestate_start_ack','savestate_start_busy',
        'savestate_start_ok','savestate_start_err','savestate_load_ack',
        'savestate_load_busy','savestate_load_ok','savestate_load_err')},
    'savestate_start':'', 'savestate_load':'',
}


def declarations(path):
    """Parse only the literal ANSI port declarations in these pinned modules."""
    text=path.read_text().split(');',1)[0]
    found=re.findall(r'^\s*(input|output|inout)\s+(?:wire|reg)\s*'
                     r'(\[\d+:\d+\])?\s*(\w+)\b',text,re.M)
    expected=re.findall(r'^\s*(?:input|output|inout)\b',text,re.M)
    if not found or len(found)!=len(expected):
        raise ValueError(f'Unsupported platform port declaration: {path}')
    return [(name,direction,width or '') for direction,width,name in found]


def prepare_platform(output_dir):
    actual=subprocess.check_output(['git','-C',str(UPSTREAM),'rev-parse','HEAD'],text=True).strip()
    if actual!=PIN or subprocess.run(['git','-C',str(UPSTREAM),'diff','--quiet','HEAD']).returncode:
        raise ValueError('Pocket platform reference differs from its recorded pin')
    output_dir=output_dir.resolve(); directory=output_dir/'platform'
    directory.mkdir(parents=True,exist_ok=True)
    top='psx_pocket_platform_resource'
    clocks=('clk_74a','clk_74b','clk_sys','video_rgb_clock','video_rgb_clock_90')
    external=[(name,'input','') for name in clocks]
    external += [('bridge_'+name,'inout','') for name in ('spimosi','spimiso','spiclk','1wire')]
    external += [('bridge_spiss','input',''),('bridge_endian_little','input',''),
        ('bridge_addr','output','[31:0]'),('bridge_addr_valid','output',''),
        ('bridge_rd','output',''),('bridge_wr','output',''),('bridge_wr_data','output','[31:0]'),
        ('core_bridge_rd_data','input','[31:0]'),
        ('video_rgb','input','[23:0]'),('video_control','input','[3:0]'),
        ('scal_vid','inout','[11:0]'),('scal_vs','output',''),('scal_hs','output',''),
        ('scal_de','output',''),('scal_skip','output',''),('scal_clk','output',''),
        ('audio_reset','input',''),('audio_bus_in','input','[31:0]'),('audio_bus_wr','input',''),
        ('audio_playback_en','input',''),('audio_flush','input',''),
        ('audio_buffer_fill','output','[11:0]'),('scal_audmclk','output',''),
        ('scal_audlrck','output',''),('scal_auddac','output','')]
    cmd_fixed={**SAVESTATE_FIXED,'clk':'clk_74a','bridge_rd_data':'cmd_bridge_rd_data',
        'bridge_endian_little':'bridge_endian_little','bridge_addr':'bridge_addr',
        'bridge_rd':'bridge_rd','bridge_wr':'bridge_wr','bridge_wr_data':'bridge_wr_data'}
    cmd_ports=declarations(CORE/'core_bridge_cmd.v')
    if set(cmd_fixed)-{name for name,_,_ in cmd_ports}:
        raise ValueError('Pinned APF command interface changed')
    external += [(name,direction,width) for name,direction,width in cmd_ports if name not in cmd_fixed]
    pad_ports=declarations(PLATFORM/'io_pad_controller.v')
    external += [(name,direction,width) for name,direction,width in pad_ports
                 if name not in ('clk','reset_n','pad_1wire')]
    if len(external)!=len({name for name,_,_ in external}):
        raise ValueError('Duplicate platform interface port')
    text=('// Resource experiment only; no PSX, memory fabric or deployable core.\n'
          '// Instances retain the reference APF, MIT and Intel source notices.\n'
          '`default_nettype none\n'+f'module {top}(\n')
    text+=',\n'.join(f'    {direction} wire {width} {name}' for name,direction,width in external)+'\n);\n'
    text+='''
    reg [24:0] count = 0;
    reg power_reset_n = 0;
    always @(posedge clk_74a) begin
        count <= count + 1'b1;
        if (count[15]) power_reset_n <= 1'b1;
    end
    wire [31:0] cmd_bridge_rd_data;
    wire [31:0] bridge_rd_data = bridge_addr[31:24] == 8'hF8 ?
                                cmd_bridge_rd_data : core_bridge_rd_data;
    io_bridge_peripheral bridge (
        .clk(clk_74a), .reset_n(power_reset_n), .endian_little(bridge_endian_little),
        .pmp_addr(bridge_addr), .pmp_addr_valid(bridge_addr_valid), .pmp_rd(bridge_rd),
        .pmp_rd_data(bridge_rd_data), .pmp_wr(bridge_wr), .pmp_wr_data(bridge_wr_data),
        .phy_spimosi(bridge_spimosi), .phy_spimiso(bridge_spimiso),
        .phy_spiclk(bridge_spiclk), .phy_spiss(bridge_spiss));
    core_bridge_cmd commands (
'''
    text+=',\n'.join(f'        .{name}({cmd_fixed.get(name,name)})' for name,_,_ in cmd_ports)+'\n    );\n'
    pad_fixed={'clk':'clk_74a','reset_n':'power_reset_n','pad_1wire':'bridge_1wire'}
    text+='    io_pad_controller pads (\n'
    text+=',\n'.join(f'        .{name}({pad_fixed.get(name,name)})' for name,_,_ in pad_ports)+'\n    );\n'
    text+='''
    mf_ddio_bidir_12 video_data (
        .oe(1'b1), .datain_h(video_rgb[23:12]), .datain_l(video_rgb[11:0]),
        .inclock(1'b0), .outclock(video_rgb_clock), .padio(scal_vid));
    wire [11:0] controls, forwarded_clock;
    mf_ddio_bidir_12 video_controls (
        .oe(1'b1), .datain_h({8'b0,video_control}), .datain_l({8'b0,video_control}),
        .inclock(1'b0), .outclock(video_rgb_clock), .padio(controls));
    assign {scal_vs,scal_hs,scal_de,scal_skip} = controls[3:0];
    mf_ddio_bidir_12 video_clock (
        .oe(1'b1), .datain_h(12'b1), .datain_l(12'b0),
        .inclock(1'b0), .outclock(video_rgb_clock_90), .padio(forwarded_clock));
    assign scal_clk = forwarded_clock[0];
    audio audio_output (
        .clk_74b(clk_74b), .clk_sys(clk_sys), .reset(audio_reset),
        .audio_bus_in(audio_bus_in), .audio_bus_wr(audio_bus_wr),
        .audio_playback_en(audio_playback_en), .audio_flush(audio_flush),
        .audio_buffer_fill(audio_buffer_fill), .audio_mclk(scal_audmclk),
        .audio_lrck(scal_audlrck), .audio_dac(scal_auddac));
endmodule
`default_nettype wire
'''
    wrapper=directory/(top+'.sv'); wrapper.write_text(text)
    init=directory/'apf/build_id.mif'; init.parent.mkdir(exist_ok=True)
    init.write_text('WIDTH=32;\nDEPTH=256;\nADDRESS_RADIX=HEX;\nDATA_RADIX=HEX;\n'
                    'CONTENT BEGIN\n[00..FF] : 00000000;\nEND;\n')
    sources=[]
    for path in [PLATFORM/name for name in ('common.v','io_bridge_peripheral.v','io_pad_controller.v',
                                           'mf_ddio_bidir_12.v','mf_datatable.v')]+[
            CORE/'core_bridge_cmd.v',CORE/'sound_i2s.sv',CORE/'audio.sv',
            CORE/'mf_audio_pll.v',CORE/'mf_audio_pll/mf_audio_pll_0002.v',wrapper,init]:
        assignment={'.v':'VERILOG_FILE','.sv':'SYSTEMVERILOG_FILE','.mif':'MIF_FILE'}[path.suffix]
        sources.append({'path':str(path),'library':'work','assignment':assignment,'sha256':digest(path)})
    physical=set(clocks)|{name for name,_,_ in external if name.startswith(('scal_','bridge_spi'))}|{'bridge_1wire'}
    virtual=[]
    for name,direction,width in external:
        if name in physical: continue
        kind='std_logic' if not width else 'std_logic_vector('+width[1:-1].replace(':',' downto ')+')'
        virtual.append((name,direction,kind))
    sdc=['# Platform area target only; no complete board timing constraints.',
        'create_clock -name clk_74a -period 13.468 [get_ports clk_74a]',
        'create_clock -name clk_74b -period 13.468 [get_ports clk_74b]',
        'create_clock -name cpu_clk -period 29.5251 [get_ports clk_sys]',
        'create_clock -name video_clk -period 18.62434 [get_ports video_rgb_clock]',
        'create_generated_clock -name video_clk90 -source [get_ports video_rgb_clock] -phase 90 [get_ports video_rgb_clock_90]',
        'derive_pll_clocks','derive_clock_uncertainty']
    hashes=write_project(directory,top,virtual,clocks,sources,sdc)
    # Use only the relevant platform pin/I/O assignments; do not run N64 build scripts.
    constraints=PLATFORM/'pocket.tcl'
    lines=[]
    for line in constraints.read_text().splitlines():
        match=re.search(r' -to (\w+)(?:\[\d+\])?$',line)
        if match and match[1] in physical and match[1] not in ('clk_sys','video_rgb_clock','video_rgb_clock_90'):
            lines.append(line)
    qsf=directory/(top+'.qsf')
    with qsf.open('a') as output: output.write('\n'.join(lines)+'\n')
    hashes['.qsf']=digest(qsf)
    limitations=['No PSX blocks, external-memory controller, sector loader or save persistence',
        'No video/core PLL; three experimental clock inputs use fitter-selected locations',
        'Original 48 kHz audio FIFO/I2S path; PSX 44.1 kHz resampling not implemented',
        'Data table initialized to zeros for area measurement; no release identity',
        'Virtual core interfaces; incomplete external timing and CDC constraints']
    result={'device':'5CEBA4F23C8','target_games':['Final Fantasy VII','Final Fantasy VIII','Final Fantasy IX'],
        'upstream_project':'MIRLO64/Pocket APF','upstream_pin':PIN,'full_core':False,'hardware_fit_verified':False,
        'excluded_components':['PSX CPU/GTE/GPU/SPU/MDEC/CD/DMA/timers/controllers/cards',
            'external memory fabric','sector loader','save persistence','video/core PLL','PSX audio resampler'],
        'platform_constraints_source':{'path':str(constraints),'sha256':digest(constraints)},
        'targets':{'platform':{'top':top,'directory':str(directory),'quartus_sources':sources,
            'project_sha256':hashes,'fixed_ports':SAVESTATE_FIXED,'physical_clock_ports':list(clocks),
            'physical_pins':sorted(physical),'included_components':['APF bridge','APF command/data table',
                'four APF controllers','DDR video output','48 kHz FIFO/I2S/audio PLL'],
            'components_connected':True,'limitations':limitations}}}
    (output_dir/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
