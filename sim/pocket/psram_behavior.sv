// Simulation stand-in for PocketQuake's psram.sv port contract (not its pins):
// write_en with byte lanes stores after a busy period; sync_burst_en returns
// sync_burst_len+1 halfwords on read_avail after a random latency with random
// WAIT gaps; config_en is busy for a while. Sparse memory (bench windows only).
module psx_pq_psram #(parameter real CLOCK_SPEED=104.0, parameter integer WRITE_EXTRA=0) (
    input wire clk, bank_sel,
    input wire [21:0] addr,
    input wire write_en,
    input wire [15:0] data_in,
    input wire write_high_byte, write_low_byte,
    input wire read_en, sync_burst_en,
    input wire [5:0] sync_burst_len,
    input wire config_en,
    input wire [15:0] config_data,
    output reg read_avail=0,
    output reg [15:0] data_out=0,
    output reg busy=0,
    output reg dbg_wait_seen, output reg [15:0] dbg_wait_cycles, dbg_burst_count, dbg_stale_count,
    output reg [21:16] cram_a,
    inout wire [15:0] cram_dq,
    input wire cram_wait,
    output reg cram_clk, cram_adv_n, cram_cre, cram_ce0_n, cram_ce1_n, cram_oe_n, cram_we_n, cram_ub_n, cram_lb_n
);
    // Sparse enough for the bench's address windows: die, addr[18:17], addr[12:0].
    reg [15:0] mem [0:65535];
    reg written [0:65535];
    integer q; initial for (q=0;q<65536;q=q+1) written[q]=0;
    function automatic [15:0] slot(input [22:0] x); slot={x[22],x[18],x[17],x[12:0]}; endfunction
    integer delay=0, left=0, configs=0;
    reg [22:0] a=0;
    reg reading=0;
    function automatic [15:0] get(input [22:0] x);
        get=written[slot(x)] ? mem[slot(x)] : {x[15:0]^16'h5a5a};
    endfunction
    always @(posedge clk) begin
        read_avail<=0;
        if (!busy) begin
            if (write_en) begin
                reg [15:0] v; v=get({bank_sel,addr});
                if (write_low_byte) v[7:0]=data_in[7:0];
                if (write_high_byte) v[15:8]=data_in[15:8];
                mem[slot({bank_sel,addr})]=v; written[slot({bank_sel,addr})]=1; busy<=1; delay<=4+$urandom%6; reading<=0;
            end else if (config_en) begin busy<=1; delay<=8; reading<=0; configs<=configs+1; end
            else if (sync_burst_en) begin
                busy<=1; delay<=3+$urandom%5; a<={bank_sel,addr}; left<=sync_burst_len+1; reading<=1;
            end
        end else if (delay>0) delay<=delay-1;
        else if (reading && left>0) begin
            if ($urandom%4!=0) begin
                read_avail<=1; data_out<=get(a); a<=a+1; left<=left-1;
                if (left==1) busy<=0;
            end
        end else busy<=0;
    end
endmodule
