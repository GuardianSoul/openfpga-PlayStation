"""Quiescent architectural save transfers for the existing Laxer CE adapter.

Retain the arithmetic and guest register interface. Restore raw register fields
without SXY FIFO pushes or IRGB writes; derived read aliases are recomputed.
The 64-word image is specific to this adapter, not portable to native MiSTer.
"""
import json
from pathlib import Path
import re
from prepare_gte_adapter import adapter_sources, system_bridge, UPSTREAM, PIN, MISTER
from prepare_resource_targets import digest


def save_adapter_sources(directory):
    sources=adapter_sources(directory)
    directory=Path(directory)
    fields={0:('cpu','{VY0,VX0}'),1:('cpu','VZ0'),2:('cpu','{VY1,VX1}'),3:('cpu','VZ1'),
        4:('cpu','{VY2,VX2}'),5:('cpu','VZ2'),7:('math','OTZ'),
        **{8+i:('math',f'IR{i}') for i in range(4)},
        **{12+i:('fifo',f'{{SY{i},SX{i}}}') for i in range(3)},
        **{16+i:('fifo',f'SZ{i}') for i in range(4)},23:('cpu','RES1'),
        **{24+i:('math',f'MAC{i}') for i in range(4)},30:('cpu','REG_lzcs'),
        32:('cpu','{R12,R11}'),33:('cpu','{R21,R13}'),34:('cpu','{R23,R22}'),35:('cpu','{R32,R31}'),36:('cpu','R33'),
        37:('cpu','TRX'),38:('cpu','TRY'),39:('cpu','TRZ'),
        40:('cpu','{L12,L11}'),41:('cpu','{L21,L13}'),42:('cpu','{L23,L22}'),43:('cpu','{L32,L31}'),44:('cpu','L33'),
        45:('cpu','RBK'),46:('cpu','GBK'),47:('cpu','BBK'),
        48:('cpu','{LR2,LR1}'),49:('cpu','{LG1,LR3}'),50:('cpu','{LG3,LG2}'),51:('cpu','{LB2,LB1}'),52:('cpu','LB3'),
        53:('cpu','RFC'),54:('cpu','GFC'),55:('cpu','BFC'),56:('cpu','OFX'),57:('cpu','OFY'),
        58:('cpu','H'),59:('cpu','DQA'),60:('cpu','DQB'),61:('cpu','ZSF3'),62:('cpu','ZSF4'),63:('math','FLAGS')}
    for addr,reg in ((6,'CRGB'),(20,'CRGB0'),(21,'CRGB1'),(22,'CRGB2')):
        fields[addr]=('color',f'{{{reg}.c,{reg}.b,{reg}.g,{reg}.r}}')
    for name in ('GTEEngine.sv','GTERegs.sv'):
        path=directory/name; text=path.read_text()
        extra='\n    input [5:0] i_ss_addr, input i_ss_write, input [31:0] i_ss_data,\n    output '+('reg ' if name=='GTERegs.sv' else 'wire ')+'[31:0] o_ss_data,'
        marker='    input E_REG i_readRegID,'
        if text.count(marker)!=1:raise ValueError('GTE read-port adaptation changed')
        text=text.replace(marker,marker+extra)
        if name=='GTEEngine.sv':
            marker='    .i_readRegID(i_readRegID),'
            if text.count(marker)!=1:raise ValueError('GTE register instance changed')
            text=text.replace(marker,marker+'\n    .i_ss_addr(i_ss_addr), .i_ss_write(i_ss_write), .i_ss_data(i_ss_data), .o_ss_data(o_ss_data),')
        else:
            # Each field remains driven by its original register process.
            pattern=r'always @\(posedge i_clk\) if \(i_ce\)\s*begin\s*if \(i_nRst'
            matches=list(re.finditer(pattern,text))
            if len(matches)!=4:raise ValueError('GTE register processes changed')
            for match,group in reversed(list(zip(matches,('color','fifo','math','cpu')))):
                cases=[]
                for addr,(owner,field) in sorted(fields.items()):
                    if owner!=group:continue
                    value='i_ss_data[30:12]' if field=='FLAGS' else 'i_ss_data'
                    cases.append(f"            6'd{addr}: {field} <= {value};")
                replacement='always @(posedge i_clk)\n    if (i_ss_write) begin\n        case(i_ss_addr)\n'+'\n'.join(cases)+'\n            default: ;\n        endcase\n    end else if (i_ce)\nbegin\n\tif (i_nRst'
                text=text[:match.start()]+replacement+text[match.end():]
            marker='always @(posedge i_clk) if (i_ce)\n\tp_wb <= i_wb;'
            if text.count(marker)!=1:raise ValueError('GTE writeback register changed')
            text=text.replace(marker,"always @(posedge i_clk)\n    if (i_ss_write) p_wb <= '0;\n    else if (i_ce) p_wb <= i_wb;")
            read=text[text.index('always @(*)'):text.index('assign o_dataOut = vOut;')]
            read=read.replace('pRegID','i_ss_addr').replace('vOut','o_ss_data')
            read=read.replace('DR_SXYP   : o_ss_data = { SY2, SX2 };',"DR_SXYP   : o_ss_data = 32'd0;")
            if "DR_SXYP   : o_ss_data = 32'd0;" not in read:raise ValueError('GTE FIFO alias changed')
            text=text.replace('assign o_dataOut = vOut;',read+'\nassign o_dataOut = vOut;')
        path.write_text(text)
    path=directory/'laxer_mister_adapter.sv'; text=path.read_text()
    text=text.replace('input wire gte_cmdEna\n','input wire gte_cmdEna,\n    input wire loading_savestate, input wire [5:0] SS_Adr,\n    input wire SS_wren, input wire [31:0] SS_DataWrite, output wire [31:0] SS_raw_data\n')
    if 'input wire loading_savestate' not in text:raise ValueError('GTE adapter ports changed')
    # MiSTer's LOAD ends with reset asserted while loading_savestate remains
    # high. Native gte.vhd deliberately preserves the restored registers there.
    text=text.replace('wire engine_enable = reset ||', 'wire guest_reset = reset && !loading_savestate;\n    wire engine_enable = guest_reset ||')
    text=text.replace('reset || (ce && clk2xIndex)','reset || (ce && clk2xIndex && !loading_savestate)')
    text=text.replace('guest_reset || (ce && clk2xIndex);','guest_reset || (ce && clk2xIndex && !loading_savestate);')
    text=text.replace('.i_clk(clk2x), .i_ce(engine_enable), .i_nRst(!reset),',
        '.i_clk(clk2x), .i_ce(engine_enable), .i_nRst(!guest_reset),\n        .i_ss_addr(SS_Adr), .i_ss_write(loading_savestate && SS_wren), .i_ss_data(SS_DataWrite), .o_ss_data(SS_raw_data),')
    text=text.replace('if (!reset && ce && gte_readEna)','if (!reset && ce && !loading_savestate && gte_readEna)')
    text=text.replace('else if (ce) begin','else if (ce && !loading_savestate) begin')
    text=text.replace("if (reset) gte_busy <= 1'b0;", "if (guest_reset) gte_busy <= 1'b0;")
    text=text.replace('endmodule','''    // synthesis translate_off
    always @(posedge clk2x)
        if (loading_savestate && SS_wren && (engine_busy || gte_busy))
            $fatal(1,"GTE restore requires quiescent engine");
    // synthesis translate_on
endmodule''')
    path.write_text(text)
    for item in sources:
        item['sha256']=digest(Path(item['path']))
        if Path(item['path']).name in ('GTEEngine.sv','GTERegs.sv','laxer_mister_adapter.sv'):
            item['transform']='CE/read-address adaptation plus quiescent raw architectural save/restore; arithmetic unchanged'
    return sources


def save_bridge(directory):
    item=system_bridge(directory); path=Path(item['path']); text=path.read_text()
    text=text.replace('gte_cmdEna : in std_logic\n','gte_cmdEna : in std_logic;\n            loading_savestate : in std_logic;\n            SS_Adr : in std_logic_vector(5 downto 0); SS_wren : in std_logic;\n            SS_DataWrite : in std_logic_vector(31 downto 0); SS_raw_data : out std_logic_vector(31 downto 0)\n')
    text=text.replace('signal busy, first_seen : std_logic;','signal busy, first_seen, SSrden : std_logic;\n    signal raw_data, SS_readData : std_logic_vector(31 downto 0);\n    signal SSreadAddr : unsigned(5 downto 0);')
    text=text.replace('gte_cmdEna=>gte_cmdEna\n','gte_cmdEna=>gte_cmdEna,\n        loading_savestate=>loading_savestate, SS_Adr=>std_logic_vector(SSreadAddr),\n        SS_wren=>SS_wren, SS_DataWrite=>SS_DataWrite, SS_raw_data=>raw_data\n')
    # Restore address must be current; the native save-read address alone has
    # one 2x pipeline stage. Select current address during restore.
    text=text.replace('std_logic_vector(SSreadAddr),','std_logic_vector(SS_Adr) when loading_savestate=\'1\' else std_logic_vector(SSreadAddr),')
    # VHDL component actual expressions are not accepted by all mixed-language
    # compilers. Use a named signal for the address mux.
    text=text.replace('signal SSreadAddr : unsigned(5 downto 0);','signal SSreadAddr : unsigned(5 downto 0);\n    signal engine_ss_addr : std_logic_vector(5 downto 0);')
    text=text.replace('begin\n    engine :','begin\n    engine_ss_addr <= std_logic_vector(SS_Adr) when loading_savestate=\'1\' else std_logic_vector(SSreadAddr);\n    engine :')
    text=text.replace("SS_Adr=>std_logic_vector(SS_Adr) when loading_savestate='1' else std_logic_vector(SSreadAddr),",'SS_Adr=>engine_ss_addr,')
    text=text.replace("    SS_DataRead <= (others=>'0');",'''    process(clk2x) begin
        if rising_edge(clk2x) then
            SSreadAddr <= SS_Adr;
            SS_readData <= raw_data;
        end if;
    end process;
    process(clk1x) begin
        if rising_edge(clk1x) then
            SSrden <= SS_rden;
            if SSrden='1' then SS_DataRead <= SS_readData; end if;
        end if;
    end process;''')
    text=re.sub(r"    assert loading_savestate='0' and SS_wren='0' and SS_rden='0'\n        report \"Candidate GTE does not implement savestate transfers\" severity failure;\n",'',text)
    path.write_text(text); item.update(sha256=digest(path),transform='Quiescent architectural save transfers with native save-read pipeline')
    return item


def apply_gte_save_adapter(manifest):
    if set(manifest['targets'])!={'psx_top'}:raise ValueError('Save adapter requires connected psx_top')
    target=manifest['targets']['psx_top']
    if target['fixed_ports'].get('WIDESCREEN')!='"00"' or target['fixed_ports'].get('TURBO_COMP') not in ("'0'", 'turbo_en'):
        raise ValueError('GTE save adapter requires normal geometry and timing')
    directory=Path(target['directory']); generated=directory/'gte-save-adapter'
    sources=save_adapter_sources(generated); bridge=save_bridge(generated)
    qsf=directory/(target['top']+'.qsf'); text=qsf.read_text(); changed=0
    for i,item in enumerate(target['quartus_sources']):
        if item['path']==str(MISTER/'rtl/gte.vhd'):
            target['quartus_sources'][i]=bridge; text=text.replace(item['path'],bridge['path']); changed+=1
    if changed!=1:raise ValueError('Expected exactly one original GTE')
    target['quartus_sources']+=sources
    for item in sources:text+=f'set_global_assignment -name {item["assignment"]} "{item["path"]}"\n'
    text+=f'set_global_assignment -name SEARCH_PATH "{UPSTREAM/"rtl"}"\n'
    qsf.write_text(text); target['project_sha256']['.qsf']=digest(qsf)
    target['gte_adapter']={'upstream_pin':PIN,'savestate_transfers':True,
        'save_image':'64-word adapter architectural image, derived aliases recomputed',
        'native_mister_image_compatible':False,'restore_requires_quiescent_engine':True,
        'arithmetic_and_guest_register_logic_unchanged':True,'game_verified':False,'hardware_verified':False}
    manifest['gte_adapter']=target['gte_adapter']
    (directory.parent/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
