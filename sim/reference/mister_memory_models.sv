// SPDX-License-Identifier: GPL-2.0-only
// Simulation models derived from the pinned MiSTer VHDL behavioral memories.
// These are host simulation models, not Pocket memory controllers.
module RamMLAB #(parameter width=8, width_byteena=1, widthad=8)(
    input inclock,wren,input [width-1:0] data,
    input [widthad-1:0] wraddress,rdaddress,output [width-1:0] q);
    reg [width-1:0] ram [0:(1<<widthad)-1];
    initial foreach(ram[i]) ram[i]='0;
    always @(posedge inclock) if(wren) ram[wraddress]<=data;
    assign q=ram[rdaddress];
endmodule

module dpram #(parameter addr_width=8,data_width=8)(
    input clock_a,clken_a,input [addr_width-1:0] address_a,
    input [data_width-1:0] data_a,input wren_a,output reg [data_width-1:0] q_a,
    input clock_b,clken_b,input [addr_width-1:0] address_b,
    input [data_width-1:0] data_b,input wren_b,output reg [data_width-1:0] q_b);
    reg [data_width-1:0] ram [0:(1<<addr_width)-1];
    initial foreach(ram[i]) ram[i]='0;
    always @(posedge clock_a) if(clken_a) begin
        if(wren_a) ram[address_a]=data_a;
        q_a<=ram[address_a];
    end
    always @(posedge clock_b) if(clken_b) begin
        if(wren_b) ram[address_b]=data_b;
        q_b<=ram[address_b];
    end
    // Simultaneous overlapping writes are undefined in the upstream shared
    // variable model and in the target RAM primitive; tests must avoid them.
endmodule

module dpram_dif #(parameter addr_width_a=8,data_width_a=8,addr_width_b=8,data_width_b=8)(
    input clock_a,clken_a,input [addr_width_a-1:0] address_a,
    input [data_width_a-1:0] data_a,input wren_a,output reg [data_width_a-1:0] q_a,
    input clock_b,clken_b,input [addr_width_b-1:0] address_b,
    input [data_width_b-1:0] data_b,input wren_b,output reg [data_width_b-1:0] q_b);
    localparam SMALL=data_width_a<data_width_b ? data_width_a:data_width_b;
    localparam DEPTH=data_width_a>=data_width_b ? (1<<addr_width_b):(1<<addr_width_a);
    reg [SMALL-1:0] ram [0:DEPTH-1];
    initial foreach(ram[i]) ram[i]='0;
    always @(posedge clock_a) if(clken_a) begin
        for(integer i=0;i<data_width_a/SMALL;i++) begin
            if(wren_a) ram[address_a*(data_width_a/SMALL)+i]=data_a[i*SMALL+:SMALL];
            q_a[i*SMALL+:SMALL]<=ram[address_a*(data_width_a/SMALL)+i];
        end
    end
    always @(posedge clock_b) if(clken_b) begin
        for(integer i=0;i<data_width_b/SMALL;i++) begin
            if(wren_b) ram[address_b*(data_width_b/SMALL)+i]=data_b[i*SMALL+:SMALL];
            q_b[i*SMALL+:SMALL]<=ram[address_b*(data_width_b/SMALL)+i];
        end
    end
endmodule

module mister_sdram_model #(parameter MEMORY_BYTES=16777216,DOREFRESH=1,
    parameter string BOOT_FILE="",parameter INITADDR=8388608)(
    input clk,clk3x,refresh,input [26:0] addr,input req,ram_dma,
    input [1:0] ram_dmacnt,input ram_iscache,rnw,input [3:0] be,input [31:0] di,
    output reg [31:0] do32=0,output reg done=0,
    output reg [3:0] cache_wr=0,output reg [31:0] cache_data=0,
    output reg [7:0] cache_addr=0,output reg dma_wr=0,
    output reg [31:0] dma_data=0,output reg reqprocessed=0,
    input [22:0] ram_dmafifo_adr,input [31:0] ram_dmafifo_data,
    input ram_dmafifo_empty,output reg ram_dmafifo_read=0);
    byte unsigned data [0:MEMORY_BYTES-1];
    initial begin
        foreach(data[i]) data[i]=0;
        if(BOOT_FILE!="") $readmemh(BOOT_FILE,data,INITADDR);
    end
    reg toggle=0,toggle3x=0,toggle3x_1=0,index3x=0;
    reg [10:0] delay1=0;
    reg req_buffer=0,refresh_buffer=0,rnw_buffer=0;
    reg [26:0] addr_buffer=0;
    reg [12:0] lastbank=0;
    integer refreshcnt=0;
    reg reqprocessed3x=0,done3x=0,refresh1=0;
    reg cache_buffer=0,cache_next=0;
    reg [3:0] cache_done=0,cache_mask=0;
    reg dma_buffer=0,dma_done=0,dma_ack=0;
    reg [1:0] dma_count3x=0,dma_count=0,dma_counter=0;
    reg [127:0] dout=0,dma_dout=0;
    typedef enum {IDLE,WAIT,RW1,RW2,IDLE9,IDLE8,IDLE7,IDLE6,IDLE5,IDLE4,IDLE3,IDLE2,IDLE1,RFSH} state_t;
    state_t state=IDLE;
    always @(posedge clk) begin
        toggle<=!toggle;done<=done3x;reqprocessed<=reqprocessed3x;
        if(done3x) do32<=addr_buffer[0] ? {8'b0,dout[31:8]}:dout[31:0];
        dma_wr<=0;dma_ack<=0;
        if(dma_wr && dma_counter<dma_count) begin
            dma_wr<=1;dma_counter<=dma_counter+1;
            dma_data<=dma_dout[(dma_counter+1)*32+:32];
        end
        if(dma_done) begin
            dma_ack<=1;dma_wr<=1;dma_data<=dout[31:0];dma_dout<=dout;
            dma_counter<=0;dma_count<=dma_count3x;
        end
    end
    task write_word(input integer base,input [31:0] word_value,input [3:0] mask);
        if(base<0 || base+3>=MEMORY_BYTES) $fatal(1,"SDRAM write out of bounds");
        for(integer j=0;j<4;j++) if(mask[j]) data[base+j]=word_value[j*8+:8];
    endtask
    always @(posedge clk3x) begin : tick3x
        reg [26:0] rotated;
        integer base;
        toggle3x<=toggle;toggle3x_1<=toggle3x;index3x<=toggle3x_1==toggle;
        if(done) done3x<=0;
        if(dma_ack) dma_done<=0;
        cache_wr<=0;cache_done<=0;
        for(integer i=0;i<4;i++) if(cache_done[i]) begin
            cache_data<=dout[i*32+:32];cache_wr<=cache_mask;cache_mask<=cache_mask<<1;
        end
        if(reqprocessed) reqprocessed3x<=0;
        ram_dmafifo_read<=0;
        if(index3x && req) req_buffer<=1;
        refresh1<=refresh;
        if(refresh && !refresh1) refresh_buffer<=1;
        if(DOREFRESH && refreshcnt<1000) refreshcnt<=refreshcnt+1;
        delay1<=delay1>>1;
        if(delay1[6] && !dma_buffer && !cache_next) done3x<=1;
        if(delay1[4] && cache_next) done3x<=1;
        if(delay1[6] && dma_buffer) dma_done<=1;
        if(delay1[7]) cache_next<=cache_buffer;
        if(cache_next) begin
            if(delay1[6]) cache_done[0]<=1;
            if(delay1[4]) cache_done[1]<=1;
            if(delay1[2]) cache_done[2]<=1;
            if(delay1[0]) cache_done[3]<=1;
        end
        if(delay1[7]) begin
            rotated=addr_buffer;
            for(integer i=0;i<8;i++) begin
                base=rotated & 27'h7fffffe;
                if(base+1>=MEMORY_BYTES) $fatal(1,"SDRAM read out of bounds");
                dout[i*16+:16]<={data[base+1],data[base]};
                rotated[9:1]=rotated[9:1]+1;
            end
        end
        case(state)
            IDLE: begin
                if(DOREFRESH && (refresh_buffer || refreshcnt>780)) begin
                    state<=RFSH;refreshcnt<=refreshcnt>780 ? refreshcnt-779:0;refresh_buffer<=0;
                end else if(!ram_dmafifo_empty) begin
                    write_word(ram_dmafifo_adr & 23'h7ffffe,ram_dmafifo_data,4'hf);
                    lastbank<=ram_dmafifo_adr[22:10];ram_dmafifo_read<=1;rnw_buffer<=0;state<=WAIT;
                end else if((req || req_buffer) && !rnw) begin
                    write_word(addr & 27'h7fffffe,di,be);req_buffer<=0;rnw_buffer<=0;done3x<=1;state<=WAIT;
                end else if((req || req_buffer) && rnw) begin
                    req_buffer<=0;addr_buffer<=addr;rnw_buffer<=1;state<=WAIT;
                    cache_buffer<=ram_iscache;cache_addr<=addr[11:4];cache_mask<=4'b1<<addr[3:2];
                    dma_buffer<=ram_dma;reqprocessed3x<=ram_dma;dma_count3x<=ram_dmacnt;
                end
            end
            WAIT: state<=RW1;
            RW1: if(rnw_buffer) begin state<=IDLE9;delay1[10]<=1;end else state<=RW2;
            RW2: if(!ram_dmafifo_empty && ram_dmafifo_adr[22:10]==lastbank) begin
                write_word(ram_dmafifo_adr & 23'h7ffffe,ram_dmafifo_data,4'hf);
                ram_dmafifo_read<=1;state<=RW1;
            end else state<=IDLE2;
            IDLE9: state<=IDLE8;
            IDLE8: state<=IDLE7;
            IDLE7: state<=IDLE6;
            IDLE6: state<=IDLE5;
            IDLE5: state<=IDLE4;
            IDLE4: state<=IDLE3;
            IDLE3: state<=IDLE2;
            IDLE2: state<=IDLE1;
            IDLE1: begin
                state<=IDLE;
                if(DOREFRESH && refreshcnt>780) begin refreshcnt<=refreshcnt-779;state<=RFSH;end
            end
            RFSH: state<=IDLE5;
        endcase
    end
endmodule

module mister_ddr_model #(parameter SLOWTIMING=15)(input clk,
    output reg busy=0,input [7:0] burstcnt,input [28:0] addr,
    output reg [63:0] dout=0,output reg ready=0,input rd,
    input [63:0] din,input [7:0] be,input we);
    reg [31:0] data [0:(1<<24)+511];
    initial foreach(data[i]) data[i]=0;
    integer saved_addr=0,saved_count=0,remaining=0,index=0;
    typedef enum {IDLE,READWAIT,READOUT} state_t;
    state_t state=IDLE;
    task write_burst;
        integer base;
        base=(addr & 29'h7fffff)*2;
        for(integer i=0;i<burstcnt;i++) for(integer half=0;half<2;half++) begin
            case(be[half*4+:4])
                4'hf: data[base+i*2+half]=din[half*32+:32];
                4'h3: data[base+i*2+half][15:0]=din[half*32+:16];
                4'hc: data[base+i*2+half][31:16]=din[half*32+16+:16];
                default: ;
            endcase
        end
    endtask
    always @(posedge clk) begin
        case(state)
            IDLE: if(rd) begin
                saved_addr=(addr & 29'h7fffff)*2;saved_count=burstcnt;
                remaining=SLOWTIMING+1;state=READWAIT;
            end else if(we) write_burst();
            READWAIT: begin
                remaining=remaining-1;
                if(remaining==0) begin
                    index=0;state=READOUT;
                    if(saved_count>0) begin dout<={data[saved_addr+1],data[saved_addr]};ready<=1;end
                end
            end
            READOUT: begin
                index=index+1;
                if(index<saved_count) dout<={data[saved_addr+index*2+1],data[saved_addr+index*2]};
                else begin
                    ready<=0;state=IDLE;
                    // The VHDL process resumes after its final burst wait and
                    // checks a write on this same edge before waiting again.
                    if(we) write_burst();
                end
            end
        endcase
    end
    // Deterministic writes take no clock waits in the original model. Its
    // busy assignments collapse in one delta cycle, hence busy stays zero.
endmodule
