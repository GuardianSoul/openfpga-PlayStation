"""Generate bounded, licensed CE/read-port adaptations of the pinned Laxer GTE.

Keep arithmetic and microcode unchanged. Every synchronous stage holds during
disabled cycles; independent read and write addresses retain MiSTer's overlap.
The generated copies stay outside the reference checkout and outside Git.
"""
import json
import re
import shutil
from pathlib import Path
import subprocess

if __package__:
    from .prepare_laxer_gte_target import UPSTREAM, PIN
    from .prepare_resource_targets import UPSTREAM as MISTER, digest
    from .tool_paths import ROOT
else:
    from prepare_laxer_gte_target import UPSTREAM, PIN
    from prepare_resource_targets import UPSTREAM as MISTER, digest
    from tool_paths import ROOT

# Counts include the commented-out ROM implementation in the original source.
CLOCKED = {'GTEEngine.sv': (2,3), 'GTERegs.sv': (6,0),
           'GTEComputePath.sv': (5,1), 'GTEFastDiv.v': (3,0),
           'GTEMicroCode.sv': (2,0)}


def adapter_sources(directory):
    actual=subprocess.check_output(['git','-C',str(UPSTREAM),'rev-parse','HEAD'],text=True).strip()
    if actual!=PIN or subprocess.run(['git','-C',str(UPSTREAM),'diff','--quiet','HEAD']).returncode:
        raise ValueError('GTE reference differs from the recorded pin')
    directory=Path(directory).resolve(); directory.mkdir(parents=True,exist_ok=True)
    shutil.copy2(MISTER/'LICENSE',directory/'COPYING.GPL2')
    sources=[]
    for original in sorted((UPSTREAM/'rtl').iterdir()):
        if original.suffix not in ('.v','.sv','.hv','.inl'): continue
        path=original; item={}
        if original.name in CLOCKED:
            text=original.read_text(); clocks,connections=CLOCKED[original.name]
            text,count=re.subn(r'(input\s+i_clk\s*,)',r'\1\n    input i_ce,',text)
            if count!=1: raise ValueError('Clock port changed: '+original.name)
            text,count=re.subn(r'(always\s*@\s*\(\s*posedge\s+i_clk\s*\))',r'\1 if (i_ce)',text)
            if count!=clocks: raise ValueError('Synchronous stages changed: '+original.name)
            text,count=re.subn(r'(\.i_clk\s*\(i_clk\)\s*,)',r'\1\n    .i_ce(i_ce),',text)
            if count!=connections: raise ValueError('Clocked instances changed: '+original.name)
            for name in CLOCKED:
                module=Path(name).stem
                text=re.sub(r'\b'+module+r'\b','Pocket'+module,text)
            if original.name in ('GTEEngine.sv','GTERegs.sv'):
                text,count=re.subn(r'(input\s+E_REG\s+i_regID\s*,)',r'\1\n    input E_REG i_readRegID,',text)
                if count!=1: raise ValueError('Register port changed: '+original.name)
                if original.name=='GTEEngine.sv':
                    text,count=re.subn(r'(\.i_regID\s*\(i_regID\)\s*,)',r'\1\n    .i_readRegID(i_readRegID),',text)
                else:
                    text,count=re.subn(r'(wire \[5:0\] pRegID = )i_regID;',r'\1i_readRegID;',text)
                if count!=1: raise ValueError('Register read connection changed: '+original.name)
            path=directory/original.name
            path.write_text('// Generated CE/read-address adaptation; original license follows.\n'+text)
            item={'original':str(original),'original_sha256':digest(original),
                  'transform':'Add synchronous CE to all clocked stages and instances; separate CPU read address',
                  'clocked_stage_count':clocks,'clocked_instance_count':connections}
        sources.append({'path':str(path),'library':'work','sha256':digest(path),
                        'assignment':'SYSTEMVERILOG_FILE' if path.suffix in ('.v','.sv') else 'MISC_FILE',**item})
    original_adapter=ROOT/'rtl/gte/laxer_mister_adapter.sv'
    adapter=directory/original_adapter.name; adapter.write_bytes(original_adapter.read_bytes())
    sources.append({'path':str(adapter),'library':'work','sha256':digest(adapter),'assignment':'SYSTEMVERILOG_FILE',
        'original':str(original_adapter),'original_sha256':digest(original_adapter),'transform':'Unchanged adapter snapshot'})
    (directory/'manifest.json').write_text(json.dumps({'upstream_pin':PIN,'sources':sources,
        'arithmetic_and_microcode_unchanged':True,'full_gte_verified':False,'cpu_execution_verified':False,
        'hardware_verified':False},indent=2)+'\n')
    return sources


def system_bridge(directory):
    """Retain the MiSTer entity/port contract with a mixed-language component."""
    original=MISTER/'rtl/gte.vhd'; text=original.read_text().split('architecture arch of gte is')[0]
    text+='''architecture pocket_candidate of gte is
    component psx_laxer_mister_adapter is
        port (
            clk2x, clk2xIndex, ce, reset : in std_logic;
            gte_busy : out std_logic;
            gte_readAddr : in std_logic_vector(5 downto 0);
            gte_readData : out std_logic_vector(31 downto 0);
            gte_readEna : in std_logic;
            gte_writeAddr_in : in std_logic_vector(5 downto 0);
            gte_writeData_in : in std_logic_vector(31 downto 0);
            gte_writeEna_in : in std_logic;
            gte_cmdData : in std_logic_vector(31 downto 0);
            gte_cmdEna : in std_logic
        );
    end component;
    signal read_data : std_logic_vector(31 downto 0);
    signal busy, first_seen : std_logic;
begin
    engine : psx_laxer_mister_adapter port map (
        clk2x=>clk2x, clk2xIndex=>clk2xIndex, ce=>ce, reset=>reset,
        gte_busy=>busy, gte_readAddr=>std_logic_vector(gte_readAddr),
        gte_readData=>read_data, gte_readEna=>gte_readEna,
        gte_writeAddr_in=>std_logic_vector(gte_writeAddr_in),
        gte_writeData_in=>std_logic_vector(gte_writeData_in), gte_writeEna_in=>gte_writeEna_in,
        gte_cmdData=>std_logic_vector(gte_cmdData), gte_cmdEna=>gte_cmdEna
    );
    gte_readData <= unsigned(read_data);
    gte_busy <= busy;
    SS_DataRead <= (others=>'0');
    process(clk1x) begin
        if rising_edge(clk1x) then
            SS_idle <= '0';
            if busy='0' and gte_cmdEna='0' and gte_readEna='0' and gte_writeEna_in='0' then
                SS_idle <= '1';
            end if;
        end if;
    end process;
    process(clk2x) begin
        if rising_edge(clk2x) then
            if reset='1' then
                first_seen <= '0';
                debug_firstGTE <= '0';
            elsif ce='1' and clk2xIndex='1' and gte_cmdEna='1' and busy='0' then
                if first_seen='0' then debug_firstGTE <= '1'; end if;
                first_seen <= '1';
            end if;
        end if;
    end process;
    -- synthesis translate_off
    assert loading_savestate='0' and SS_wren='0' and SS_rden='0'
        report "Candidate GTE does not implement savestate transfers" severity failure;
    assert WIDESCREEN="00" and TURBO='0'
        report "Candidate GTE requires normal geometry and timing modes" severity failure;
    -- synthesis translate_on
end architecture;
'''
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    path=directory/'gte.vhd'; path.write_text(text)
    return {'path':str(path.resolve()),'library':'work','sha256':digest(path),
            'original':str(original),'original_sha256':digest(original),
            'transform':'MiSTer entity retained; Laxer SV component bridge; savestate/widescreen/turbo disabled'}


def apply_gte_adapter(manifest):
    if set(manifest['targets']) not in ({'psx_top'},{'gte'}):
        raise ValueError('GTE adaptation requires the connected system or GTE target alone')
    name=next(iter(manifest['targets'])); target=manifest['targets'][name]
    if 'gte_adapter' in target: raise ValueError('GTE adaptation already applied')
    required={'WIDESCREEN':'"00"','TURBO_COMP' if name=='psx_top' else 'TURBO':"'0'"}
    if name=='psx_top': required.update({key:"'0'" for key in ('save_state','load_state','rewind_on','rewind_active')})
    else: required.update({key:"'0'" for key in ('loading_savestate','SS_wren','SS_rden')})
    for port,value in required.items():
        # --turbo-setting: psx_top's TURBO_COMP may be the live setting; its igte port is tied '0' (prepare_turbo_setting.py)
        if target['fixed_ports'].get(port)!=value and not (name=='psx_top' and port=='TURBO_COMP' and target['fixed_ports'].get(port)=='turbo_en'):
            raise ValueError('Candidate GTE control contract not satisfied: '+port)
    directory=Path(target['directory']); generated=directory/'gte-adapter'
    sources=adapter_sources(generated)
    bridge=system_bridge(generated)
    qsf=directory/(target['top']+'.qsf'); text=qsf.read_text()
    changes=0
    for index,source in enumerate(target['quartus_sources']):
        if source['path']==str(MISTER/'rtl/gte.vhd'):
            target['quartus_sources'][index]=bridge
            text=text.replace(source['path'],bridge['path']); changes+=1
    if changes!=1: raise ValueError('Expected one upstream GTE source')
    # Uninstantiated original GTE helpers remain listed but are not synthesized.
    target['quartus_sources']+=sources
    for source in sources:
        text+=f'set_global_assignment -name {source["assignment"]} "{source["path"]}"\n'
    text+=f'set_global_assignment -name SEARCH_PATH "{UPSTREAM / "rtl"}"\n'
    qsf.write_text(text); target['project_sha256']['.qsf']=digest(qsf)
    sdc=directory/(target['top']+'.sdc')
    with sdc.open('a') as output:
        output.write('''
# Engine registers/ROM outputs advance only on ce && clk2xIndex=1.
# No relaxation of CPU input paths, registered CPU reads or busy outputs.
set gte_state [get_registers {*|PocketGTEEngine:engine|*}]
if {[get_collection_size $gte_state] == 0} { error "GTE CE register collection is empty" }
puts "POCKET_GTE_CE_REGISTERS=[get_collection_size $gte_state]"
set_multicycle_path -from $gte_state -to $gte_state -setup 2
set_multicycle_path -from $gte_state -to $gte_state -hold 1
''')
    target['project_sha256']['.sdc']=digest(sdc)
    target['gte_adapter']={'upstream_pin':PIN,'clock':'clk2x with synchronous CE on index=1',
        'internal_state_multicycle':{'setup':2,'hold':1,'scope':'Engine register-to-register only'},
        'independent_read_write_addresses':True,'savestate_transfers':False,
        'full_gte_verified':False,'cpu_execution_verified':False,'hardware_verified':False}
    manifest['gte_adapter']=target['gte_adapter']
    (directory.parent/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
