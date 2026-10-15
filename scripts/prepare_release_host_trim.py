"""Opt-in release bridge cleanup, preserving CD/card/region service transport."""
from pathlib import Path
import re

from build_pocket import source
from prepare_resource_targets import digest
from tool_paths import ROOT


BUTTON_BITS={'Triangle':6,'Circle':4,'Cross':5,'Square':7,'Select':14,'Start':15,
    'Right':3,'Left':2,'Up':0,'Down':1,'R1':9,'R2':11,'R3':13,'L1':8,'L2':10,'L3':12}
SERVICE_READS={'isPaused','region_out','resetFromCD','cd_hps_req','cd_hps_lba','cd_hps_lba_sim',
    'memcard_changed','saving_memcard','memcard1_rd','memcard1_wr','memcard1_lba','memcard1_dataOut',
    'memcard2_rd','memcard2_wr','memcard2_lba','memcard2_dataOut'}


def trimmed_text(text,registers):
    removed=[]
    for button,bit in BUTTON_BITS.items():
        name='joypad1_Key'+button
        matches=[r for r in registers if r['name']==name]
        if len(matches)!=1 or matches[0]['width']!=1:raise ValueError('Expected one digital pad input: '+name)
        declaration=f'    reg {name}=0;'
        if text.count(declaration)!=1:raise ValueError('Pad declaration changed: '+name)
        text=text.replace(declaration,f'    wire {name}=pocket_pad_buttons[{bit}];')
        assignment=f"        if(bridge_wr && bridge_addr==32'h{matches[0]['address']:08x}) {name}<=bridge_wr_data[0:0];\n"
        if text.count(assignment)!=1:raise ValueError('Pad bridge writer changed: '+name)
        text=text.replace(assignment,'');removed.append(matches[0])
    anchor='    wire [31:0] cmd_bridge_rd_data;'
    if text.count(anchor)!=1:raise ValueError('Bridge source marker changed')
    text=text.replace(anchor,'''    wire [15:0] pocket_pad_buttons;
    psx_pocket_pad pad_input (.clk1x(clk1x),.reset_n(cpu_locked),
        .apf_keys(cont1_key[15:0]),.buttons(pocket_pad_buttons));
'''+anchor)
    pattern=r'    reg \[31:0\] diagnostic_read;\n    always @\* begin\n        diagnostic_read=0;\n        case\(bridge_addr\)\n(.*?)        endcase\n    end\n'
    match=re.search(pattern,text,re.S)
    if not match:raise ValueError('Diagnostic readback block changed')
    retained=[];discarded=[]
    for line in match[1].splitlines():
        row=re.fullmatch(r"        32'h([0-9a-f]{8}): diagnostic_read=(.*);",line)
        if not row:raise ValueError('Unknown diagnostic expression')
        expression=row[2]
        if expression in SERVICE_READS or row[1]=='13000000':retained.append(line)
        else:discarded.append(dict(address=int(row[1],16),expression=expression))
    expressions={re.search(r'=(.*);',line)[1] for line in retained}
    if not SERVICE_READS.issubset(expressions):raise ValueError('Missing required CD/card service output')
    replacement=match[0][:match[0].index('        case(bridge_addr)')]+\
        '        case(bridge_addr)\n'+'\n'.join(retained)+'\n        endcase\n    end\n'
    text=text[:match.start()]+replacement+text[match.end():]
    text=text.replace('diagnostic_read','service_read')
    # Loader status stays; its word-count/CRC readbacks belong to the separate
    # diagnostic. Counters required by the loader itself are not altered.
    for address in [0x10000004,0x10000008,0x1000000c,0x10000010]:
        pattern=rf"            32'h{address:08x}: bridge_rd_data=.*;\n"
        text,n=re.subn(pattern,'',text)
        if n!=1:raise ValueError('Boot counter readback changed')
        discarded.append(dict(address=address,expression='boot diagnostic counter/CRC'))
    return text,removed,discarded


def apply_release_host_trim(manifest,directory):
    directory=Path(directory).resolve()
    if manifest.get('kind')!='physical-psx-budget' or not manifest.get('reset_only') or \
        not manifest.get('release_boot_loader') or not manifest.get('pocket_audio_stream'):
        raise ValueError('Host trim requires reset-only release boot and required audio')
    if 'release_host_trim' in manifest:raise ValueError('Host trim already applied')
    if not manifest['variant_flags'].get('pocket-controls') or manifest['extra_variant_flags'].get('pocket-dualshock'):
        raise ValueError('Host trim currently requires the explicit one-digital-pad budget')
    if any(digest(Path(s['path']))!=s['sha256'] for s in manifest['sources']) or \
        any(digest(directory/n)!=h for n,h in manifest['project_hashes'].items()):
        raise ValueError('Host trim input identity changed')
    core=directory/'core_top.sv';text,removed,discarded=trimmed_text(core.read_text(),manifest['diagnostic_input_registers'])
    core.write_text(text)
    out=directory/'release-host';out.mkdir(exist_ok=False)
    pad=out/'psx_pocket_pad.sv';pad.write_bytes((ROOT/'rtl/pocket/psx_pocket_pad.sv').read_bytes());added=source(pad)
    qsf=directory/'apf_top.qsf';qsf.write_text(qsf.read_text()+f'\nset_global_assignment -name SYSTEMVERILOG_FILE "{pad}" -library work\n')
    sdc=directory/'pocket.sdc';sdc.write_text(sdc.read_text()+'''
# Independent digital levels: only the first per-button sampling stage is
# asynchronous. The second CPU stage remains timed. No numeric/service bus
# crosses through this exception.
set_false_path -to [get_registers {*|pad_input|keys_meta[*]}]
''')
    for record in manifest['sources']:
        if record['path']==str(core):record['sha256']=digest(core)
    manifest['sources'].append(added)
    for name in ['apf_top.qsf','pocket.sdc']:manifest['project_hashes'][name]=digest(directory/name)
    names={r['name'] for r in removed}
    manifest['diagnostic_input_registers']=[r for r in manifest['diagnostic_input_registers'] if r['name'] not in names]
    manifest['release_host_trim']=dict(preparer_sha256=digest(Path(__file__)),pad_source=added,
        removed_button_bridge_registers=removed,removed_readbacks=discarded,
        retained_service_inputs=manifest['diagnostic_input_registers'],retained_service_outputs=sorted(SERVICE_READS),
        APF_button_mapping=BUTTON_BITS,one_digital_pad=True,button_sync_CPU_clocks=2,
        input_levels_independent=True,service_transport_CDC_verified=False,hardware_verified=False)
    manifest['limitations'].append('Opt-in host cleanup preserves legacy CD/card service transport; its raw CDC and real APF file services still require integration')
    return manifest
