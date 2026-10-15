"""Private direct-fill scalar buffer, retaining the proven SPU and CDC.

The scalar adapter emits only one-halfword writes or aligned eight-halfword
reads. Specialize the packet endpoint to those shapes and write each native
read word straight into the buffer. No 128-bit response copy is needed.
"""
from pathlib import Path

from prepare_resource_targets import digest
from prepare_spu_scalar_bram_buffer import replace
from prepare_spu_scalar_dual_buffer import prepare as prepare_dual


BRIDGE = r'''// SPDX-License-Identifier: MIT
module psx_spu_scalar_bridge (
    input wire clk,rst,req_valid,req_write,
    output wire req_ready,input wire [24:0] req_addr,
    input wire [3:0] req_halfwords,input wire [127:0] req_wdata,
    input wire [15:0] req_byte_en,
    output wire rsp_valid,input wire rsp_ready,
    output wire [127:0] rsp_rdata,output reg rsp_error,
    output wire mem_valid,input wire mem_ready,output wire mem_write,
    output wire [23:0] mem_addr,output wire [31:0] mem_wdata,
    output wire [3:0] mem_byte_en,input wire mem_rvalid,
    input wire [31:0] mem_rdata,
    output wire fill_valid,output wire [1:0] fill_word,
    output wire [31:0] fill_data
);
    localparam IDLE=0,READ=1,WRITE=2,RESPONSE=3;
    reg [1:0] state;
    reg [23:0] base;
    reg offset;
    reg [15:0] data;
    reg [1:0] mask;
    reg [2:0] issued,received;
    wire invalid=req_write ? req_halfwords!=1 : req_halfwords!=8 || req_addr[2:0]!=0;
    assign req_ready=!rst && state==IDLE;
    assign rsp_valid=!rst && state==RESPONSE;
    assign rsp_rdata=0;
    assign mem_valid=!rst && (state==WRITE || state==READ && issued<4);
    assign mem_write=state==WRITE;
    // Packet addresses count halfwords; native addresses count 32-bit words.
    assign mem_addr=base+(state==READ ? {21'b0,issued}:24'b0);
    assign mem_wdata=offset ? {data,16'b0}:{16'b0,data};
    assign mem_byte_en=offset ? {mask,2'b0}:{2'b0,mask};
    assign fill_valid=!rst && state==READ && mem_rvalid;
    assign fill_word=received[1:0];
    assign fill_data=mem_rdata;
    always @(posedge clk) begin
        if(rst) begin
            state<=IDLE;base<=0;offset<=0;data<=0;mask<=0;
            issued<=0;received<=0;rsp_error<=0;
        end else case(state)
            IDLE:if(req_valid) begin
                base<=req_addr[24:1];offset<=req_addr[0];
                data<=req_wdata[15:0];mask<=req_byte_en[1:0];
                issued<=0;received<=0;rsp_error<=invalid;
                state<=invalid ? RESPONSE:req_write ? WRITE:READ;
            end
            READ:begin
                if(mem_valid && mem_ready) issued<=issued+1'b1;
                if(mem_rvalid) begin
                    received<=received+1'b1;
                    if(received==3) state<=RESPONSE;
                end
            end
            WRITE:if(mem_ready) state<=RESPONSE;
            RESPONSE:if(rsp_ready) state<=IDLE;
        endcase
    end
    // synthesis translate_off
    always @(posedge clk) if(!rst && mem_rvalid &&
        (state!=READ || received>=4 || received>=issued+{2'b0,mem_valid && mem_ready}))
        $fatal(1,"Scalar stream unexpected native read return");
    // synthesis translate_on
endmodule
'''


def prepare(directory):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    baseline=prepare_dual(directory/'baseline');text=Path(baseline['path']).read_text()
    text=replace(text,'    output wire p_rsp_ready\n',
        '    output wire p_rsp_ready,\n    input wire fill_valid,input wire [1:0] fill_word,input wire [31:0] fill_data\n')
    text=replace(text,'    reg [1:0] response_word=0;\n','')
    text=replace(text,'    wire response_complete=p_write || p_rsp_error || response_word==3;\n','')
    text=replace(text,'assign p_rsp_ready=cdc_response_ready && response_complete;',
                      'assign p_rsp_ready=cdc_response_ready;')
    before='''        if(dst_reset || !p_rsp_valid) response_word<=0;
        else if(cdc_response_ready && !p_write && !p_rsp_error) begin
            line_words[{p_wdata[21:16],response_word}]<=p_rsp_rdata[response_word*32+:32];
            response_word<=response_word+1'b1;
        end'''
    text=replace(text,before,'''        if(!dst_reset && fill_valid)
            line_words[{p_wdata[21:16],fill_word}]<=fill_data;''')
    text=replace(text,'.d_rsp_valid(p_rsp_valid && response_complete)', '.d_rsp_valid(p_rsp_valid)')
    adapter=directory/'psx_spu_sdram_adapter.sv';adapter.write_text(text)
    bridge=directory/'psx_spu_scalar_bridge.sv';bridge.write_text(BRIDGE)
    return dict(baseline,path=str(adapter),sha256=digest(adapter),baseline=baseline,
                bridge_path=str(bridge),bridge_sha256=digest(bridge),storage='M10K-stream',
                miss_fill_memory_clocks=0,reply_storage_bits=0,
                allowed_packets='One halfword masked write or aligned eight-halfword read')


def connect(text,adapter_label='adapter',bridge_label='front'):
    """Connect a private test/resource wrapper with one SPU packet frontend."""
    text=replace(text,'psx_spu_sdram_adapter '+adapter_label+' (',
        'wire scalar_fill_valid;wire [1:0] scalar_fill_word;wire [31:0] scalar_fill_data;\n'
        'psx_spu_sdram_adapter '+adapter_label+' (') if 'psx_spu_sdram_adapter '+adapter_label+' (' in text else replace(
        text,'psx_spu_sdram_adapter '+adapter_label+'(',
        'wire scalar_fill_valid;wire [1:0] scalar_fill_word;wire [31:0] scalar_fill_data;\n'
        'psx_spu_sdram_adapter '+adapter_label+'(')
    text=replace(text,'.p_rsp_rdata(rd)', '.p_rsp_rdata(rd),.fill_valid(scalar_fill_valid),.fill_word(scalar_fill_word),.fill_data(scalar_fill_data)')
    text=replace(text,'psx_mem_bridge #(.PIPELINE_READS(1)) '+bridge_label,
                      'psx_spu_scalar_bridge '+bridge_label)
    return replace(text,'.rsp_error(re)', '.rsp_error(re),.fill_valid(scalar_fill_valid),.fill_word(scalar_fill_word),.fill_data(scalar_fill_data)')
