"""Measure the unchanged Laxer3a GTE pinned by the PS-FPGA reference."""
import json
from pathlib import Path
import subprocess

if __package__:
    from .prepare_resource_targets import digest, write_project
    from .tool_paths import ROOT
else:
    from prepare_resource_targets import digest, write_project
    from tool_paths import ROOT

UPSTREAM=ROOT/'references/PS-FPGA/rtl/gte'
PIN='289bcf316966d75dfc60f34d347451c492e02365'


def prepare_laxer_gte(output_dir):
    actual=subprocess.check_output(['git','-C',str(UPSTREAM),'rev-parse','HEAD'],text=True).strip()
    if actual!=PIN or subprocess.run(['git','-C',str(UPSTREAM),'diff','--quiet','HEAD']).returncode:
        raise ValueError('Laxer3a GTE reference differs from its recorded pin')
    output_dir=Path(output_dir).resolve(); directory=output_dir/'laxer_gte'
    directory.mkdir(parents=True,exist_ok=True); top='psx_laxer_gte_resource'
    external=[('clk1x','in','std_logic'),('reset_n','in','std_logic'),
        ('reg_addr','in','std_logic_vector(5 downto 0)'),('reg_write','in','std_logic'),
        ('reg_data','in','std_logic_vector(31 downto 0)'),('reg_read_data','out','std_logic_vector(31 downto 0)'),
        ('command','in','std_logic_vector(24 downto 0)'),('command_enable','in','std_logic'),('busy','out','std_logic')]
    wrapper=directory/(top+'.sv')
    wrapper.write_text('''// GPL-2.0 component resource wrapper; preserve upstream notices.
// No CPU adapter, clock-enable integration or functional execution proof.
`include "GTEDefine.hv"
module psx_laxer_gte_resource(
    input wire clk1x, input wire reset_n,
    input wire [5:0] reg_addr, input wire reg_write, input wire [31:0] reg_data,
    output wire [31:0] reg_read_data,
    input wire [24:0] command, input wire command_enable, output wire busy
);
    GTEEngine component_core(
        .i_clk(clk1x), .i_nRst(reset_n), .i_regID(E_REG'(reg_addr)),
        .i_WritReg(reg_write), .i_DIP_USEFASTGTE(1'b0), .i_DIP_FIXWIDE(1'b0),
        .i_dataIn(reg_data), .o_dataOut(reg_read_data),
        .i_Instruction(command), .i_run(command_enable), .o_executing(busy));
endmodule
''')
    files=sorted((UPSTREAM/'rtl').iterdir())
    sources=[]
    for path in [*files,wrapper]:
        if path.suffix not in ('.v','.sv','.hv','.inl'):continue
        sources.append({'path':str(path),'library':'work','sha256':digest(path),
            'assignment':'SYSTEMVERILOG_FILE' if path.suffix in ('.v','.sv') else 'MISC_FILE'})
    sdc=['# Standalone candidate clock only; no Pocket timing proof.',
         'create_clock -name gte_clk1x -period 29.5251 [get_ports clk1x]','derive_clock_uncertainty']
    hashes=write_project(directory,top,external,('clk1x',),sources,sdc)
    qsf=directory/(top+'.qsf')
    with qsf.open('a') as output:
        output.write(f'set_global_assignment -name SEARCH_PATH "{UPSTREAM / "rtl"}"\n')
    hashes['.qsf']=digest(qsf)
    result={'device':'5CEBA4F23C8','upstream_project':'PS-FPGA/Laxer3a GTE','upstream_pin':PIN,
        'target_games':['Final Fantasy VII','Final Fantasy VIII','Final Fantasy IX'],
        'full_core':False,'hardware_fit_verified':False,
        'excluded_components':['PSX CPU and all other devices','CPU/GTE adapter and clock enable','Pocket platform'],
        'targets':{'laxer_gte':{'top':top,'directory':str(directory),'quartus_sources':sources,
            'project_sha256':hashes,'fixed_ports':{'i_DIP_USEFASTGTE':0,'i_DIP_FIXWIDE':0},
            'physical_clock_ports':['clk1x'],'included_components':['Laxer3a GTE'],
            'components_connected':False,'limitations':['Area/timing experiment only; no functional execution',
                'Original core lacks MiSTer clock-enable and uses a different CPU/register/busy contract']}}}
    (output_dir/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
