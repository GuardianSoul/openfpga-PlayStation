`timescale 1ns/1ps
`default_nettype none
// Autonomous bring-up test, not a guest RAM client. Full and masked writes
// followed by exact readback in four windows spanning both die endpoints.
module psx_psram_probe #(
    parameter integer WORDS=256
) (
    input wire clk, reset, initialized, wait_error,
    output wire valid, write, die, last,
    output wire [21:0] address,
    output wire [31:0] wdata,
    output wire [3:0] byte_en,
    input wire ready, rvalid, idle,
    input wire [31:0] rdata,
    output reg passed, failed,
    output wire checking,
    output reg [17:0] checked_words,
    output reg [22:0] failed_address,
    output reg [31:0] failed_expected, failed_actual
);
    localparam [3:0] INIT=0, FULL=1, FULL_DRAIN=2, MASK=3,
        MASK_DRAIN=4, READ=5, READ_DRAIN=6, DONE=7;
    reg [3:0] state;
    reg [1:0] region;
    reg [3:0] mask;
    reg [8:0] issued, returned;
    reg [15:0] watchdog;
    wire [22:0] base_address=region==0 ? 23'h000000 : region==1 ? 23'h3fff00 :
                               region==2 ? 23'h400000 : 23'h7fff00;
    wire [22:0] request_address=base_address+23'(issued);
    wire [22:0] response_address=base_address+23'(returned);
    // One controller-domain input stage keeps the comparator/status logic
    // out of the sample-clock-to-controller-clock transfer path.
    reg response_valid;
    reg [31:0] response_data;
    always @(posedge clk) begin
        if(reset) begin response_valid<=0; response_data<=0; end
        else begin response_valid<=rvalid; if(rvalid) response_data<=rdata; end
    end
    function automatic [31:0] pattern(input [22:0] a);
        pattern=32'h19a5c37e ^ {a[6:0],a[22:0],2'b01} ^ {9'b0,a};
    endfunction
    wire [31:0] mask_pattern=~pattern(request_address) ^ {8{mask}};
    reg [31:0] expected;
    always @* begin
        expected=pattern(response_address);
        for (integer b=0;b<4;b=b+1)
            if (mask[b]) expected[b*8+:8]=~expected[b*8+:8] ^ {2{mask}};
    end
    assign valid=!reset && initialized && (state==FULL || state==MASK || state==READ);
    assign write=state!=READ;
    assign {die,address}=request_address;
    assign wdata=state==FULL ? pattern(request_address) : mask_pattern;
    assign byte_en=state==FULL ? 4'hf : state==MASK ? mask : 4'h0;
    assign last=issued==WORDS-1;
    assign checking=!passed && !failed;

    always @(posedge clk) begin
        if (reset) begin
            state<=INIT; region<=0; mask<=0; issued<=0; returned<=0;
            passed<=0; failed<=0; checked_words<=0; watchdog<=0;
            failed_address<=0; failed_expected<=0; failed_actual<=0;
        end else begin
            if (initialized && state!=DONE) begin
                if ((valid && ready) || rvalid || state==INIT) watchdog<=0;
                else watchdog<=watchdog+1'b1;
                if (wait_error || &watchdog) begin
                    failed<=1; state<=DONE;
                    failed_address<=response_address; failed_expected<=expected;
                    failed_actual<=response_data;
                end
            end
            case(state)
                INIT: if (initialized) state<=FULL;
                FULL: if (ready) begin
                    if (last) begin issued<=0; state<=FULL_DRAIN; end
                    else issued<=issued+1'b1;
                end
                FULL_DRAIN: if(idle) begin
                    if(region==3) begin region<=0; state<=MASK; end
                    else begin region<=region+1'b1; state<=FULL; end
                end
                MASK: if (ready) begin
                    if (last) begin issued<=0; state<=MASK_DRAIN; end
                    else issued<=issued+1'b1;
                end
                MASK_DRAIN: if(idle) begin
                    if(region==3) begin region<=0; state<=READ; returned<=0; end
                    else begin region<=region+1'b1; state<=MASK; end
                end
                READ: if (ready) begin
                    if(last) begin issued<=0; state<=READ_DRAIN; end
                    else issued<=issued+1'b1;
                end
                READ_DRAIN: if (idle && returned==WORDS) begin
                    returned<=0;
                    if(region==3) begin
                        region<=0;
                        if(mask==15) begin passed<=!failed; state<=DONE; end
                        else begin mask<=mask+1'b1; state<=FULL; end
                    end else begin region<=region+1'b1; state<=READ; end
                end
                default: state<=DONE;
            endcase
            if(response_valid && !failed) begin
                if ((state!=READ && state!=READ_DRAIN) || returned>=WORDS || response_data!==expected) begin
                    failed<=1; passed<=0; state<=DONE;
                    failed_address<=response_address; failed_expected<=expected; failed_actual<=response_data;
                end else begin returned<=returned+1'b1; checked_words<=checked_words+1'b1; end
            end
            // These errors must win over state transitions on the same edge.
            if (initialized && state!=DONE && (wait_error || &watchdog)) begin
                failed<=1; passed<=0; state<=DONE;
            end
        end
    end
endmodule
`default_nettype wire
