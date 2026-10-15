// SPDX-License-Identifier: GPL-2.0-only
// Finite-bandwidth replacement for mister_ddr_model, with the same port shape
// and public data[] capture array. Host simulation only, not synthesizable.
// CPU/GPU clocks continue during waits. Writes consume bus time and assert BUSY.
module pocket_vram_model #(
    parameter integer CORE_HZ=67737600, MEM_HZ=125000000,
    HALFWORDS_PER_TICK=1, T_RP=3, T_RCD=3, CAS=3, T_RFC=9,
    REFRESH_TICKS=960, TURNAROUND=3, CDC_TICKS=8,
    MEMORY_WORDS=(1<<24)+512
)(input wire clk, output reg busy=0, input wire [7:0] burstcnt,
    input wire [28:0] addr, output reg [63:0] dout=0, output reg ready=0,
    input wire rd, input wire [63:0] din, input wire [7:0] be, input wire we);
    reg [31:0] data [0:MEMORY_WORDS-1];
    initial foreach(data[i]) data[i]=0;
    localparam IDLE=0, LATENCY=1, TRANSFER=2, RECOVERY=3;
    reg [1:0] state=IDLE;
    integer saved_addr=0, remaining=0, word_index=0, delay_ticks=0;
    integer refresh_age=0, refresh_pending=0, last_row=-1;
    reg direction=0, previous_direction=0;
    reg [63:0] saved_data;
    reg [7:0] saved_mask;
    longint unsigned phase=0, serviced_bytes=0, row_hits=0, row_misses=0;
    longint unsigned refreshes=0, busy_core_cycles=0, requests=0;
    initial begin
        if(CORE_HZ<=0 || MEM_HZ<=0 || REFRESH_TICKS<=T_RFC+T_RP ||
            (HALFWORDS_PER_TICK!=1 && HALFWORDS_PER_TICK!=2))
            $fatal(1,"Invalid Pocket VRAM timing profile");
    end
    always @(posedge clk) begin : advance
        integer ticks, row, base;
        ready<=0;
        phase=phase+MEM_HZ;
        ticks=phase/CORE_HZ; phase=phase%CORE_HZ;
        refresh_age=refresh_age+ticks;
        if (refresh_age>=REFRESH_TICKS) begin
            refresh_age=refresh_age-REFRESH_TICKS; refresh_pending=refresh_pending+T_RFC;
        end
        if (busy) busy_core_cycles=busy_core_cycles+1;
        case(state)
            // BUSY is registered: every request observed while BUSY=0 must
            // be accepted, including one coinciding with a refresh becoming
            // due. Queue that refresh behind the accepted transaction.
            IDLE: if((rd || we) && !busy) begin
                if(rd && we || burstcnt==0) $fatal(1,"Invalid Pocket VRAM request");
                saved_addr=(addr & 29'h7fffff)*2;
                if(saved_addr+burstcnt*2>MEMORY_WORDS) $fatal(1,"Pocket VRAM bounds");
                remaining=burstcnt; word_index=0; direction=we; saved_data=din; saved_mask=be;
                row=(saved_addr*4)/1024;
                delay_ticks=CDC_TICKS+CAS;
                if(last_row!=row) begin delay_ticks+=T_RCD+(last_row<0 ? 0:T_RP); row_misses++; end
                else row_hits++;
                if(previous_direction!=we) delay_ticks+=TURNAROUND;
                last_row=row; previous_direction=we; requests++; state=LATENCY; busy<=1;
            end else if(refresh_pending>0) begin
                busy<=1;
                if(last_row>=0) begin refresh_pending+=T_RP; last_row=-1; end
                refresh_pending=refresh_pending-ticks;
                if(refresh_pending<=0) begin refresh_pending=0; busy<=0; refreshes++; end
            end else busy<=0;
            LATENCY: begin
                delay_ticks-=ticks;
                if(delay_ticks<=0) begin delay_ticks=4/HALFWORDS_PER_TICK; state=TRANSFER; end
            end
            TRANSFER: begin
                delay_ticks-=ticks;
                if(delay_ticks<=0) begin
                    base=saved_addr+word_index*2;
                    if(direction) begin
                        for(integer j=0;j<8;j++) if(saved_mask[j])
                            data[base+j/4][(j%4)*8+:8]=saved_data[j*8+:8];
                    end else begin dout<={data[base+1],data[base]}; ready<=1; end
                    serviced_bytes+=8; remaining--; word_index++;
                    if(remaining==0) begin delay_ticks=CDC_TICKS; state=RECOVERY; end
                    else begin
                        delay_ticks+=4/HALFWORDS_PER_TICK;
                        if(((base+2)*4)/1024!=last_row) begin
                            last_row=((base+2)*4)/1024; delay_ticks+=T_RP+T_RCD+CAS; row_misses++;
                        end
                    end
                end
            end
            RECOVERY: begin delay_ticks-=ticks; if(delay_ticks<=0) state=IDLE; end
        endcase
    end
endmodule
