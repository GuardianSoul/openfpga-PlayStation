"""Scalar buffer with memory-clock fills and a control-only CDC reply.

The existing packet bridge holds its 128-bit response while four memory
clocks write a dual-clock 256x32 M10K. Only then is the CDC reply accepted.
The source reads the requested halfword from the RAM after that marker.
Read-command data bits21:16 carry the held cache location; writes retain
their original payload. Packet read data fields are otherwise don't-care.
"""
from pathlib import Path

from prepare_resource_targets import digest
from prepare_spu_scalar_buffer import prepare as prepare_mlab
from prepare_spu_scalar_bram_buffer import replace


def prepare(directory):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    baseline=prepare_mlab(directory/'baseline',2);text=Path(baseline['path']).read_text()
    text=replace(text,'localparam IDLE=0,ISSUE=1,WAIT=2,RETURN=3;','localparam IDLE=0,ISSUE=1,WAIT=2,RETURN=3,HIT_WAIT=4;')
    text=replace(text,'reg [1:0] state=IDLE;','reg [2:0] state=IDLE;')
    for name in ('lines','lines1'):
        text=replace(text,f'    (* ramstyle="MLAB, no_rw_check" *) reg [127:0] {name}[0:31];\n','')
    text=replace(text,'    reg fill_way=0;', '''    reg fill_way=0,fill_cancel=0;
    (* ramstyle="M10K, no_rw_check" *) reg [31:0] line_words[0:255];
    reg [31:0] cache_q;
    reg [1:0] response_word=0;
    wire cdc_response_ready;
    wire response_complete=p_write || p_rsp_error || response_word==3;
    wire [7:0] cache_read_address=state==IDLE ?
        {hit1 && !hit,lookup,addr[3:2]}:{fill_way,fill_index,lane[2:1]};
    assign p_rsp_ready=cdc_response_ready && response_complete;
    always @(posedge mem_clk) begin
        if(dst_reset || !p_rsp_valid) response_word<=0;
        else if(cdc_response_ready && !p_write && !p_rsp_error) begin
            line_words[{p_wdata[21:16],response_word}]<=p_rsp_rdata[response_word*32+:32];
            response_word<=response_word+1'b1;
        end
    end
    always @(posedge clk1x) cache_q<=line_words[cache_read_address];''')
    text=replace(text,'&& !discard_returns && !src_reset;','&& !discard_returns && !fill_cancel && !src_reset;')
    text=replace(text,".s_halfwords(write ? 4'd1:4'd8),.s_wdata({112'b0,word}),",
        ".s_halfwords(write ? 4'd1:4'd8),.s_wdata({106'b0,(write ? 6'b0:{fill_way,fill_index}),word}),")
    text=replace(text,'.d_rsp_valid(p_rsp_valid),.d_rsp_ready(p_rsp_ready),',
                 '.d_rsp_valid(p_rsp_valid && response_complete),.d_rsp_ready(cdc_response_ready),')
    text=replace(text,'.d_rsp_rdata(p_rsp_rdata),.d_rsp_error(p_rsp_error)',
                 ".d_rsp_rdata(128'b0),.d_rsp_error(p_rsp_error)")
    text=replace(text,'valid<=0;valid1<=0;victim<=0;fill_way<=0;lane<=0;fill_index<=0;fill_tag<=0;',
                 'valid<=0;valid1<=0;victim<=0;fill_way<=0;fill_cancel<=0;lane<=0;fill_index<=0;fill_tag<=0;')
    text=replace(text,'write<=!rnw;word<=data[15:0];mask<=byte_en[1:0];',
                 'write<=!rnw;word<=data[15:0];mask<=byte_en[1:0];fill_cancel<=discard_returns;')
    text=replace(text,"read_data<={16'b0,hit ? lines[lookup][addr[3:1]*16+:16]:lines1[lookup][addr[3:1]*16+:16]};state<=RETURN;",'state<=HIT_WAIT;')
    text=replace(text,"read_data<=write ? 32'b0:{16'b0,rdata[lane*16+:16]};state<=RETURN;",
                 "read_data<=write ? 32'b0:{16'b0,(lane[0] ? cache_q[31:16]:cache_q[15:0])};state<=RETURN;")
    text=replace(text,'if(!write && !rerror && !discard_returns) begin','if(!write && !rerror && !discard_returns && !fill_cancel) begin')
    text=replace(text,'if(fill_way) begin lines1[fill_index]<=rdata;tags1[fill_index]<=fill_tag;valid1[fill_index]<=1;end',
                 'if(fill_way) begin tags1[fill_index]<=fill_tag;valid1[fill_index]<=1;end')
    text=replace(text,'else begin lines[fill_index]<=rdata;tags[fill_index]<=fill_tag;valid[fill_index]<=1;end',
                 'else begin tags[fill_index]<=fill_tag;valid[fill_index]<=1;end')
    text=replace(text,'                RETURN:if(core_ce || discard_returns) state<=IDLE;',
        "                HIT_WAIT:begin read_data<={16'b0,(lane[0] ? cache_q[31:16]:cache_q[15:0])};state<=RETURN;end\n"
        '                RETURN:if(core_ce || discard_returns || fill_cancel) state<=IDLE;')
    text=replace(text,'if(discard_returns) begin valid<=0;valid1<=0;end',
                 'if(discard_returns) begin valid<=0;valid1<=0;end\n            if(discard_returns && state!=IDLE) fill_cancel<=1;')
    path=directory/'psx_spu_sdram_adapter.sv';path.write_text(text)
    return dict(baseline,path=str(path),sha256=digest(path),baseline=baseline,storage='M10K-dual',
        data_RAM_shape=[256,32],write_clock='mem_clk',read_clock='clk1x',hit_extra_clocks=1,
        miss_fill_memory_clocks=4,CDC_response_data_bits=0,read_command_metadata_bits='21:16',
        cancelled_fill_published=False,discarded_operation_completes_to_guest=False)
