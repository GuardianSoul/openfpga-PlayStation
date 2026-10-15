"""Private scalar-SPU read buffer; existing mailbox/physical writes retained.

32 sets of 16-byte lines, with folded address indexing and one/two ways. SPU scalar
requests still complete once; every accepted write invalidates all lines.
No voice-cache, SPU arithmetic or native clock changes.
"""
from pathlib import Path

from prepare_resource_targets import digest
from tool_paths import ROOT


LOGIC=r'''
    localparam WAYS=1;
    localparam IDLE=0,ISSUE=1,WAIT=2,RETURN=3;
    reg [1:0] state=IDLE;
    reg write=0;reg [17:0] halfword=0;reg [15:0] word=0;reg [1:0] mask=0;
    reg [2:0] lane=0;reg [4:0] fill_index=0;reg [14:0] fill_tag=0;
    (* ramstyle="MLAB, no_rw_check" *) reg [127:0] lines[0:31];
    (* ramstyle="MLAB, no_rw_check" *) reg [14:0] tags[0:31];
    reg [31:0] valid=0;
    (* ramstyle="MLAB, no_rw_check" *) reg [127:0] lines1[0:31];
    (* ramstyle="MLAB, no_rw_check" *) reg [14:0] tags1[0:31];
    reg [31:0] valid1=0,victim=0;
    reg fill_way=0;
    wire [4:0] lookup=addr[8:4]^addr[13:9]^addr[18:14];
    wire hit=valid[lookup] && tags[lookup]==addr[18:4] && !discard_returns;
    wire hit1=WAYS==2 && valid1[lookup] && tags1[lookup]==addr[18:4] && !discard_returns;
    wire ready,rvalid,rerror,src_reset,dst_reset;
    wire [127:0] rdata;
    assign source_idle=!src_reset && state==IDLE && ready;
    assign done=state==RETURN && core_ce && !discard_returns && !src_reset;
    psx_mem_cdc crossing (
        .src_clk(clk1x),.dst_clk(mem_clk),.reset_async(reset_async),.src_reset(src_reset),.dst_reset(dst_reset),
        .s_valid(state==ISSUE),.s_ready(ready),.s_write(write),.s_addr(BASE_HALFWORD+{7'b0,halfword}),
        .s_halfwords(write ? 4'd1:4'd8),.s_wdata({112'b0,word}),.s_byte_en({14'b0,mask}),
        .s_rsp_valid(rvalid),.s_rsp_ready(state==WAIT),.s_rsp_rdata(rdata),.s_rsp_error(rerror),
        .d_valid(p_valid),.d_ready(p_ready),.d_write(p_write),.d_addr(p_addr),.d_halfwords(p_halfwords),
        .d_wdata(p_wdata),.d_byte_en(p_byte_en),.d_rsp_valid(p_rsp_valid),.d_rsp_ready(p_rsp_ready),
        .d_rsp_rdata(p_rsp_rdata),.d_rsp_error(p_rsp_error));
    always @(posedge clk1x) begin
        if(src_reset) begin
            state<=IDLE;write<=0;halfword<=0;word<=0;mask<=0;read_data<=0;fault<=0;
            valid<=0;valid1<=0;victim<=0;fill_way<=0;lane<=0;fill_index<=0;fill_tag<=0;
        end else begin
            if(core_ce && request && state!=IDLE) fault<=1;
            case(state)
                IDLE:if(core_ce && request) begin
                    write<=!rnw;word<=data[15:0];mask<=byte_en[1:0];
                    lane<=addr[3:1];fill_index<=lookup;fill_tag<=addr[18:4];
                    fill_way<=WAYS==2 && valid[lookup] && (!valid1[lookup] || victim[lookup]);
                    if(addr[0] || byte_en[3:2]!=0) fault<=1;
                    if(rnw && (hit || hit1)) begin
                        read_data<={16'b0,hit ? lines[lookup][addr[3:1]*16+:16]:lines1[lookup][addr[3:1]*16+:16]};state<=RETURN;
                        victim[lookup]<=hit;
                    end else begin
                        halfword<=rnw ? {addr[18:4],3'b0}:addr[18:1];state<=ISSUE;
                    end
                    if(!rnw) begin valid<=0;valid1<=0;end
                end
                ISSUE:if(ready) state<=WAIT;
                WAIT:if(rvalid) begin
                    read_data<=write ? 32'b0:{16'b0,rdata[lane*16+:16]};state<=RETURN;
                    if(rerror) fault<=1;
                    if(!write && !rerror && !discard_returns) begin
                        if(fill_way) begin lines1[fill_index]<=rdata;tags1[fill_index]<=fill_tag;valid1[fill_index]<=1;end
                        else begin lines[fill_index]<=rdata;tags[fill_index]<=fill_tag;valid[fill_index]<=1;end
                        victim[fill_index]<=!fill_way;
                    end
                end
                RETURN:if(core_ce || discard_returns) state<=IDLE;
                default:state<=IDLE;
            endcase
            // Guest reset/reload invalidation wins even over a returning fill.
            if(discard_returns) begin valid<=0;valid1<=0;end
        end
    end
endmodule
'''


def prepare(directory,ways=1):
    if ways not in (1,2):raise ValueError('Scalar buffer supports one/two ways')
    original=ROOT/'rtl/pocket/psx_spu_sdram_adapter.sv'
    text=original.read_text();anchor='    localparam IDLE=0,ISSUE=1,WAIT=2,RETURN=3;'
    if text.count(anchor)!=1:raise ValueError('Scalar SPU adapter declaration changed')
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    path=directory/'psx_spu_sdram_adapter.sv';path.write_text(text[:text.index(anchor)]+LOGIC.replace('localparam WAYS=1;',f'localparam WAYS={ways};'))
    return dict(path=str(path),sha256=digest(path),original=str(original),original_sha256=digest(original),
        sets=32,ways=ways,entries=32*ways,line_bytes=16,data_bits=4096*ways,tag_bits=480*ways,valid_bits=32*ways,
        write_policy='Original one-halfword physical write; invalidate all cached lines at request',
        required_ownership='All external SPU writes through this adapter, or discard/reset invalidation before guest access',
        production_selected=False,deadline_verified=False,area_verified=False,hardware_verified=False,game_verified=False)
