"""Private M10K version of the scalar SPU raw-data buffer.

Keep the 32-set/two-way tags and existing memory mailbox. Serialize each
128-bit fill into a 512x16 synchronous RAM. Hits add one source clock;
misses add eight fill clocks. A discarded operation cannot publish its line
or emit a later guest completion after the discard pulse ends.
"""
from pathlib import Path

from prepare_resource_targets import digest
from prepare_spu_scalar_buffer import prepare as prepare_mlab


def replace(text,before,after):
    if text.count(before)!=1:raise ValueError('Scalar M10K anchor changed: '+before[:70])
    return text.replace(before,after)


def prepare(directory):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    baseline=prepare_mlab(directory/'baseline',2)
    text=Path(baseline['path']).read_text()
    text=replace(text,'localparam IDLE=0,ISSUE=1,WAIT=2,RETURN=3;','localparam IDLE=0,ISSUE=1,WAIT=2,RETURN=3,HIT_WAIT=4,FILL=5;')
    text=replace(text,'reg [1:0] state=IDLE;','reg [2:0] state=IDLE;')
    for name in ('lines','lines1'):
        text=replace(text,f'    (* ramstyle="MLAB, no_rw_check" *) reg [127:0] {name}[0:31];\n','')
    text=replace(text,'    reg fill_way=0;', '''    reg fill_way=0;
    reg [2:0] fill_word=0;
    reg fill_cancel=0;
    (* ramstyle="M10K, no_rw_check" *) reg [15:0] line_words[0:511];
    reg [15:0] cache_q;
    wire [8:0] cache_read_address={hit1 && !hit,lookup,addr[3:1]};
    always @(posedge clk1x) begin
        cache_q<=line_words[cache_read_address];
        if(state==FILL && !src_reset && !fill_cancel && !discard_returns)
            line_words[{fill_way,fill_index,fill_word}]<=rdata[fill_word*16+:16];
    end''')
    text=replace(text,'&& !discard_returns && !src_reset;','&& !discard_returns && !fill_cancel && !src_reset;')
    text=replace(text,'valid<=0;valid1<=0;victim<=0;fill_way<=0;lane<=0;fill_index<=0;fill_tag<=0;',
                 'valid<=0;valid1<=0;victim<=0;fill_way<=0;fill_word<=0;fill_cancel<=0;lane<=0;fill_index<=0;fill_tag<=0;')
    text=replace(text,'write<=!rnw;word<=data[15:0];mask<=byte_en[1:0];',
                 'write<=!rnw;word<=data[15:0];mask<=byte_en[1:0];fill_cancel<=discard_returns;')
    text=replace(text,"read_data<={16'b0,hit ? lines[lookup][addr[3:1]*16+:16]:lines1[lookup][addr[3:1]*16+:16]};state<=RETURN;",
                 'state<=HIT_WAIT;')
    before='''                WAIT:if(rvalid) begin
                    read_data<=write ? 32'b0:{16'b0,rdata[lane*16+:16]};state<=RETURN;
                    if(rerror) fault<=1;
                    if(!write && !rerror && !discard_returns) begin
                        if(fill_way) begin lines1[fill_index]<=rdata;tags1[fill_index]<=fill_tag;valid1[fill_index]<=1;end
                        else begin lines[fill_index]<=rdata;tags[fill_index]<=fill_tag;valid[fill_index]<=1;end
                        victim[fill_index]<=!fill_way;
                    end
                end
                RETURN:if(core_ce || discard_returns) state<=IDLE;'''
    after='''                WAIT:if(rvalid) begin
                    read_data<=write ? 32'b0:{16'b0,rdata[lane*16+:16]};state<=RETURN;
                    if(rerror) fault<=1;
                    if(!write && !rerror && !discard_returns && !fill_cancel) begin
                        fill_word<=0;state<=FILL;
                    end
                end
                FILL:begin
                    if(fill_word==7) begin
                        // Last RAM write and valid publication share this edge.
                        // No new source request is accepted until RETURN drains.
                        if(!fill_cancel && !discard_returns) begin
                            if(fill_way) begin tags1[fill_index]<=fill_tag;valid1[fill_index]<=1;end
                            else begin tags[fill_index]<=fill_tag;valid[fill_index]<=1;end
                            victim[fill_index]<=!fill_way;
                        end
                        state<=RETURN;
                    end else fill_word<=fill_word+1'b1;
                end
                HIT_WAIT:begin read_data<={16'b0,cache_q};state<=RETURN;end
                RETURN:if(core_ce || discard_returns || fill_cancel) state<=IDLE;'''
    text=replace(text,before,after)
    text=replace(text,'if(discard_returns) begin valid<=0;valid1<=0;end',
                 'if(discard_returns) begin valid<=0;valid1<=0;end\n            if(discard_returns && state!=IDLE) fill_cancel<=1;')
    path=directory/'psx_spu_sdram_adapter.sv';path.write_text(text)
    return dict(baseline,path=str(path),sha256=digest(path),baseline=baseline,storage='M10K',
        data_RAM_shape=[512,16],hit_extra_clocks=1,miss_fill_clocks=8,
        cancelled_fill_published=False,discarded_operation_completes_to_guest=False)
