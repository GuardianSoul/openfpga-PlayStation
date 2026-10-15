`timescale 1ns/1ps
`default_nettype none

// Fair transaction routing onto one memory frontend. Requesters hold their
// payload until accepted; no short-pulse capture or clock crossing is implied.
// Each client has one response slot. A client holding its response does not
// prevent other clients using memory. Ordering between clients is grant order.
module psx_mem_arb #(parameter integer PORTS = 4) (
    input wire clk, rst,
    input wire [PORTS-1:0] p_valid, p_write,
    output reg [PORTS-1:0] p_ready,
    input wire [PORTS-1:0][24:0] p_addr,
    input wire [PORTS-1:0][3:0] p_halfwords,
    input wire [PORTS-1:0][127:0] p_wdata,
    input wire [PORTS-1:0][15:0] p_byte_en,
    output wire [PORTS-1:0] p_rsp_valid,
    input wire [PORTS-1:0] p_rsp_ready,
    output reg [PORTS-1:0][127:0] p_rsp_rdata,
    output reg [PORTS-1:0] p_rsp_error,
    output wire req_valid,
    input wire req_ready,
    output wire req_write,
    output wire [24:0] req_addr,
    output wire [3:0] req_halfwords,
    output wire [127:0] req_wdata,
    output wire [15:0] req_byte_en,
    input wire rsp_valid,
    output wire rsp_ready,
    input wire [127:0] rsp_rdata,
    input wire rsp_error,
    output reg protocol_error
);
    localparam integer INDEX_WIDTH = PORTS > 1 ? $clog2(PORTS) : 1;
    reg [INDEX_WIDTH-1:0] next_port, owner, selected;
    reg active, found;
    reg [PORTS-1:0] outstanding;
    reg [PORTS-1:0] response_valid;
    reg reserved;
    reg [INDEX_WIDTH-1:0] reserved_port;
    integer candidate;
    always @* begin
        selected = 0; found = 0;
        for (integer offset = 0; offset < PORTS; offset = offset + 1) begin
            candidate = (integer'(next_port) + offset) % PORTS;
            if (!found && p_valid[candidate] &&
                (!outstanding[candidate] || (p_rsp_valid[candidate] && p_rsp_ready[candidate]))) begin
                selected = INDEX_WIDTH'(candidate); found = 1;
            end
        end
        if (reserved) begin selected = reserved_port; found = p_valid[reserved_port]; end
        p_ready = 0;
        if (!rst && !active && found) p_ready[selected] = req_ready;
    end
    assign req_valid = !rst && !active && found;
    assign req_write = p_write[selected];
    assign req_addr = p_addr[selected];
    assign req_halfwords = p_halfwords[selected];
    assign req_wdata = p_wdata[selected];
    assign req_byte_en = p_byte_en[selected];
    assign rsp_ready = !rst && active;
    assign p_rsp_valid = response_valid & {PORTS{!rst}};

    // Selection must stay fixed while the memory frontend stalls. The latch
    // below reserves a requester without acknowledging it before req_ready.
    wire [INDEX_WIDTH-1:0] grant = reserved ? reserved_port : selected;

    always @(posedge clk) begin
        if (rst) begin
            next_port <= 0; owner <= 0; active <= 0; outstanding <= 0;
            response_valid <= 0; p_rsp_rdata <= 0; p_rsp_error <= 0; protocol_error <= 0;
            reserved <= 0; reserved_port <= 0;
        end else begin
            if (reserved && !p_valid[reserved_port]) begin protocol_error <= 1; reserved <= 0; end
            for (integer port = 0; port < PORTS; port = port + 1)
                if (p_rsp_valid[port] && p_rsp_ready[port]) begin
                    response_valid[port] <= 0; outstanding[port] <= 0;
                end
            if (rsp_valid && !active) protocol_error <= 1;
            if (rsp_valid && rsp_ready) begin
                if (p_rsp_valid[owner]) protocol_error <= 1;
                p_rsp_rdata[owner] <= rsp_rdata; p_rsp_error[owner] <= rsp_error;
                response_valid[owner] <= 1; active <= 0;
            end
            if (req_valid && req_ready) begin
                owner <= grant; outstanding[grant] <= 1; active <= 1; reserved <= 0;
                next_port <= grant == INDEX_WIDTH'(PORTS-1) ? 0 : grant + 1'b1;
            end else if (req_valid && !req_ready && !reserved) begin
                reserved <= 1; reserved_port <= selected;
            end
        end
    end
endmodule
`default_nettype wire
