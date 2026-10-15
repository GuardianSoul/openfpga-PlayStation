"""Localize the budget VRAM return enable without changing a client cycle.

The CDC announces the edge on which its held response will enter RESPONSE.
Eight preserved local enables capture that announcement, then capture the
already registered response on the original adapter edge. Payload CDC and its
timing bounds are unchanged. Original modules are never edited.
"""
from pathlib import Path

from prepare_resource_targets import digest
from tool_paths import ROOT


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('VRAM response contract changed: ' + before[:100])
    return text.replace(before, after)


def prepare_components(directory, cdc_source=None):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    cdc_original = Path(cdc_source or ROOT / 'rtl/memory/psx_mem_cdc.sv')
    adapter_original = ROOT / 'rtl/pocket/psx_vram_adapter.sv'
    cdc = replace_once(cdc_original.read_text(), 'module psx_mem_cdc (',
                       'module psx_mem_cdc_response_enable (')
    cdc = replace_once(cdc, '    output wire s_rsp_valid,',
                       '    output wire s_rsp_valid, s_rsp_advance,')
    cdc = replace_once(cdc, '    assign s_rsp_valid = !src_reset && src_state == RESPONSE;',
                       '''    assign s_rsp_valid = !src_reset && src_state == RESPONSE;
    // This is a same-source-clock prediction, NOT an early payload transfer.
    assign s_rsp_advance = !src_reset && src_state == WAIT_RESPONSE &&
                           response_sync[1] == request_toggle;''')
    adapter = replace_once(adapter_original.read_text(), 'module psx_vram_adapter (',
                           'module psx_vram_response_adapter (')
    adapter = replace_once(adapter, '    input wire rsp_valid, rsp_error,',
                           '    input wire rsp_valid, rsp_advance, rsp_error,')
    adapter = replace_once(adapter,
        '                if (!write) begin ready<=1; dout<=rsp_data[63:0]; end',
        '                if (!write) begin ready<=1; end')
    adapter = replace_once(adapter, '    always @(posedge clk) begin\n',
        '''    // Prevent synthesis from merging the local enables back into the
    // high-fanout state decode that failed timing in the physical budget top.
    genvar lane;
    generate for (lane=0; lane<8; lane=lane+1) begin: response_lane
        (* preserve, dont_merge *) reg bank_enable=0;
        always @(posedge clk) begin
            if (reset) bank_enable<=0;
            else bank_enable<=state==WAIT && !write && rsp_advance;
            // On this edge the CDC payload has already been registered and
            // rsp_valid is high, exactly as in the original adapter.
            if (!reset && bank_enable)
                dout[lane*8 +: 8]<=rsp_data[lane*8 +: 8];
        end
    end endgenerate
    always @(posedge clk) begin
''')
    records = []
    for name, text, original in (
        ('psx_mem_cdc_response_enable.sv', cdc, cdc_original),
        ('psx_vram_response_adapter.sv', adapter, adapter_original)):
        path = directory / name
        path.write_text(text)
        records.append({'path': str(path), 'sha256': digest(path),
                        'original': str(original), 'original_sha256': digest(original),
                        'assignment': 'SYSTEMVERILOG_FILE', 'library': 'work'})
    return records


def apply_response_enable(manifest, directory):
    """Apply only to a freshly prepared physical budget top, before compiling."""
    directory = Path(directory).resolve()
    sources = manifest['sources']
    core = directory / 'core_top.sv'
    if digest(core) != next(s['sha256'] for s in sources if Path(s['path']) == core):
        raise ValueError('Core source identity changed')
    cdc = next(s for s in sources if Path(s['path']).name == 'psx_mem_cdc.sv')
    components = prepare_components(directory / 'vram-response-enable', cdc['path'])
    text = replace_once(core.read_text(), '    psx_vram_adapter vram_commands ',
                        '    psx_vram_response_adapter vram_commands ')
    text = replace_once(text, '    psx_mem_cdc vram_cdc ',
                        '    psx_mem_cdc_response_enable vram_cdc ')
    text = replace_once(text, '    wire v_valid,v_ready,v_write,v_rsp,v_rsp_ready,v_error;',
                        '    wire v_valid,v_ready,v_write,v_rsp,v_rsp_ready,v_error,v_advance;')
    text = replace_once(text, '.rsp_data(v_read),.rsp_error(v_error),.fault(vram_fault));',
                        '.rsp_data(v_read),.rsp_error(v_error),.rsp_advance(v_advance),.fault(vram_fault));')
    text = replace_once(text, '.s_rsp_valid(v_rsp),.s_rsp_ready(v_rsp_ready),',
                        '.s_rsp_valid(v_rsp),.s_rsp_advance(v_advance),.s_rsp_ready(v_rsp_ready),')
    core.write_text(text)
    sources = [s for s in sources if Path(s['path']).name != 'psx_vram_adapter.sv']
    for s in sources:
        if Path(s['path']) == core:
            s['sha256'] = digest(core)
    sources += components
    qsf = directory / 'apf_top.qsf'
    lines = [line for line in qsf.read_text().splitlines()
             if not ('-name SYSTEMVERILOG_FILE ' in line and 'psx_vram_adapter.sv' in line)]
    lines += [f'set_global_assignment -name SYSTEMVERILOG_FILE "{s["path"]}" -library work'
              for s in components]
    # The first vector form coalesced to one register. Scalar attributes plus
    # explicit scoped assignments must pass the post-map eight-register gate.
    lines += [f'set_instance_assignment -name {name} ON -to {{*|vram_commands|*bank_enable*}}'
              for name in ('DONT_MERGE_REGISTER','PRESERVE_REGISTER')]
    qsf.write_text('\n'.join(lines) + '\n')
    manifest['sources'] = sources
    manifest['project_hashes']['apf_top.qsf'] = digest(qsf)
    manifest['vram_response_enable'] = {'components': components,
        'added_registers': 8, 'added_client_cycles': 0,
        'bundled_payload_constraints_changed': False,
        'timing_verified': False, 'hardware_verified': False}
    return manifest
