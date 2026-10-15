"""Private release boot path, keeping the BIOS diagnostic as a separate build."""
from pathlib import Path
import json
import re

from build_pocket import source
from prepare_pocket_audio_stream import replace_once
from prepare_resource_targets import digest
from tool_paths import ROOT


def validate_boot_contract(path,apf=False):
    path=Path(path).resolve();report=json.loads(path.read_text())
    if not report.get('passed') or not report.get('source_identity_verified'):
        raise ValueError('complete release boot contract required')
    if any(digest(Path(p))!=sha for p,sha in report['source_sha256'].items()):
        raise ValueError('boot contract input changed since testing')
    required=[ROOT/'rtl/pocket/psx_bios_loader.sv',ROOT/'rtl/pocket/psx_boot_gate.sv']
    required += [ROOT/'scripts/run_apf_bios_boot.py',ROOT/'sim/pocket/tb_apf_bios_boot.sv',
        ROOT/'references/MIRLO64/target/pocket/core_bridge_cmd.v'] if apf else [
        ROOT/'scripts/run_pocket_bios_loader.py',ROOT/'sim/pocket/tb_bios_loader.sv']
    if any(report['source_sha256'].get(str(p))!=digest(p) for p in required):
        raise ValueError('boot report does not bind the selected sources')
    if apf:
        if [r['name'] for r in report['runs']]!=['original','early-setup','wrong-endian'] or not all(
                r['passed'] for r in report['runs']):
            raise ValueError('APF bridge contract/fault coverage missing')
        clean=[report['runs'][0]]
    else:
        if [r['case'] for r in report['runs']]!=list(range(14)) or not all(r['passed'] for r in report['runs']):
            raise ValueError('release loader coverage missing')
        if [f['name'] for f in report['faults']]!=['early-ready','no-freeze','byte-swap','wrong-address'] or not all(
                f['rejected'] for f in report['faults']):
            raise ValueError('release loader fault coverage missing')
        clean=report['runs']
    if any(r['exit_code']!=0 or re.search(r'%Fatal|%Error|Assertion failed',r['stdout']) for r in clean):
        raise ValueError('boot positive run contains a failure')
    return {'path':str(path),'sha256':digest(path)}


def apply_boot_loader(manifest,directory,contract_path,apf_contract_path):
    directory=Path(directory).resolve()
    contracts=[validate_boot_contract(contract_path),validate_boot_contract(apf_contract_path,True)]
    if manifest.get('kind')!='physical-psx-budget' or 'release_boot_loader' in manifest:
        raise ValueError('release loader needs a fresh physical budget build')
    if any(digest(Path(s['path']))!=s['sha256'] for s in manifest['sources']):
        raise ValueError('boot preparation source identity changed')
    if any(digest(directory/n)!=h for n,h in manifest['project_hashes'].items()):
        raise ValueError('boot preparation project identity changed')
    core=directory/'core_top.sv';text=core.read_text()
    text=replace_once(text,'    assign bridge_endian_little=0;','    assign bridge_endian_little=1;')
    text=replace_once(text,'.status_boot_done(power_reset_n)', '.status_boot_done(mem_ready74[1])')
    text=replace_once(text,'.status_setup_done(power_reset_n)', '.status_setup_done(passed)')
    text=replace_once(text,'.status_running(reset_n)', '.status_running(reset_n && passed)')
    text=replace_once(text,'    wire dataslot_allcomplete;', '''    wire dataslot_allcomplete;
    wire bios_slot_request, bios_slot_ack, bios_slot_ok;
    wire [15:0] bios_slot_id;
    wire [31:0] bios_slot_size;
    wire bios_freeze_toggle, bios_freeze_ack, bios_loader_busy, cpu_boot_reset;
    wire [17:0] bios_received_words;''')
    for name,value in (('dataslot_requestwrite','bios_slot_request'),('dataslot_requestwrite_id','bios_slot_id'),
                       ('dataslot_requestwrite_size','bios_slot_size')):
        text=replace_once(text,f'.{name}()',f'.{name}({value})')
    text=replace_once(text,'.dataslot_requestwrite_ack(mem_ready74[1])',
        '.dataslot_requestwrite_ack(bios_slot_id==0 ? bios_slot_ack : mem_ready74[1])')
    text=replace_once(text,".dataslot_requestwrite_ok(1'b1)",
        ".dataslot_requestwrite_ok(bios_slot_id==0 ? bios_slot_ok : 1'b1)")
    pattern=r'    psx_bios_probe bios_probe \(.*?\);'
    match=re.search(pattern,text,re.S)
    if not match:raise ValueError('budget BIOS probe anchor changed')
    block='''    // CRC/readback remains in the separately packaged diagnostic.
    assign checking=bios_loader_busy, checked_words=bios_received_words;
    assign write_crc=0, read_crc=0;
    psx_bios_loader bios_loader (
        .clk(clk_74a),.reset(!power_reset_n),.memory_ready(mem_ready74[1]),
        .slot_request(bios_slot_request),.slot_id(bios_slot_id),.slot_size(bios_slot_size),
        .slot_ack(bios_slot_ack),.slot_ok(bios_slot_ok),.allcomplete(dataslot_allcomplete),
        .bridge_wr(bridge_wr),.bridge_addr(bridge_addr),.bridge_data(bridge_wr_data),
        .freeze_toggle(bios_freeze_toggle),.freeze_ack(bios_freeze_ack),
        .req_valid(load_valid),.req_ready(load_ready),.req_write(load_write),.req_addr(load_addr),
        .req_data(load_data),.req_be(load_be),.rsp_valid(load_rsp),.rsp_ready(load_rsp_ready),
        .rsp_error(load_error),.received_words(bios_received_words),.committed_words(loaded_words),
        .ready(passed),.failed(failed),.busy(bios_loader_busy));
    psx_boot_gate boot_gate (
        .host_clk(clk_74a),.cpu_clk(clk1x),.reset_async(!power_reset_n),.cpu_locked(cpu_locked),
        .host_run(reset_n),.bios_ready(passed),.freeze_toggle(bios_freeze_toggle),
        .cpu_reset(cpu_boot_reset),.freeze_ack(bios_freeze_ack));
    // The new freeze token cannot return until the CPU has also observed this
    // ready bit low. Core clear/guest writes take ownership only after the
    // final loader response, before the synchronized reset release.
    (* async_reg="true", preserve, dont_merge *) reg bios_ready_meta=0, bios_ready_cpu=0;
    always @(posedge clk1x or negedge cpu_locked) begin
        if(!cpu_locked) begin bios_ready_meta<=0;bios_ready_cpu<=0;end
        else begin bios_ready_meta<=passed;bios_ready_cpu<=bios_ready_meta;end
    end
'''
    text=text[:match.start()]+block+text[match.end():]
    text=replace_once(text,'assign reset=!reset_n || !passed || !cpu_locked;',
        'assign reset=cpu_boot_reset;')
    for old,new in (
        ('!passed ? loader_address','!bios_ready_cpu ? loader_address'),
        ('!passed ? loader_data','!bios_ready_cpu ? loader_data')):
        text=replace_once(text,old,new)
    # An unused CRC address must not display ffffffff as a pretend result.
    text=replace_once(text,"32'h1000000c: bridge_rd_data=write_crc ^ 32'hffffffff;",
        "32'h1000000c: bridge_rd_data=0;")
    text=replace_once(text,"32'h10000010: bridge_rd_data=read_crc ^ 32'hffffffff;",
        "32'h10000010: bridge_rd_data=0;")
    core.write_text(text)
    output=directory/'release-boot';output.mkdir(exist_ok=False)
    added=[]
    for name in ('psx_bios_loader.sv','psx_boot_gate.sv'):
        path=output/name;path.write_bytes((ROOT/'rtl/pocket'/name).read_bytes());added.append(source(path))
    previous=[s for s in manifest['sources'] if Path(s['path']).name=='psx_bios_probe.sv']
    if len(previous)!=1:raise ValueError('expected one diagnostic BIOS source')
    qsf=directory/'apf_top.qsf'
    lines=[line for line in qsf.read_text().splitlines() if previous[0]['path'] not in line]
    lines += [f'set_global_assignment -name SYSTEMVERILOG_FILE "{s["path"]}" -library work' for s in added]
    qsf.write_text('\n'.join(lines)+'\n')
    manifest['sources']=[s for s in manifest['sources'] if s is not previous[0]]+added
    for s in manifest['sources']:
        if s['path']==str(core):s['sha256']=digest(core)
    manifest['project_hashes']['apf_top.qsf']=digest(qsf)
    sdc=directory/'pocket.sdc'
    sdc.write_text(sdc.read_text()+'''
# Release boot single-bit synchronizers. Local chains and CPU reset output
# consumers stay timed. Only asynchronous origins into the reset pipe are cut.
set_false_path -to [get_registers {*|*boot_gate*|freeze_meta *|*boot_gate*|ack_meta *|*boot_gate*|lock_meta *|bios_ready_meta}]
set_false_path -from [get_registers {*|*bios_loader*|ready *|*commands*|reset_n *|power_reset_n}] -to [get_registers {*|*boot_gate*|reset_pipe[*]}]
''')
    manifest['project_hashes']['pocket.sdc']=digest(sdc)
    manifest['release_boot_loader']={'sources':added,'replaced_diagnostic':previous[0],
        'preparer_sha256':digest(Path(__file__)),'contracts':contracts,
        'bridge_endian_little':True,'BIOS_slot_id':0,
        'required_BIOS_bytes':524288,'FIFO_words':256,'FIFO_width':32,
        'CPU_freeze_ack_required':True,'reset_release_CPU_clocks':3,
        'diagnostic_readback_preserved_separately':True,
        'remaining_host_sector_card_services':True,'hardware_verified':False}
    manifest['limitations'].append('Release loader private integration only: little-endian BIOS writes and CPU freeze; full APF/game/board validation pending')
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
