`timescale 1ns/1ps
`default_nettype none

// Observes accepted transactions on one shared, single-outstanding memory port.
// It does not drive or arbitrate requests. See docs/memory-baseline.md for the
// sampling contract and the adapter required for pgate's Pocket interface.
module psx_mem_profile #(
    parameter integer PORTS = 4,
    parameter integer COUNTER_WIDTH = 32
) (
    input wire clk,
    input wire rst,
    input wire clear,
    input wire [PORTS-1:0] issue,
    input wire [7:0] issue_bytes,
    input wire complete,
    input wire [PORTS-1:0] waiting,
    output reg busy,
    output reg [COUNTER_WIDTH-1:0] cycles,
    output reg [COUNTER_WIDTH-1:0] busy_cycles,
    output reg [COUNTER_WIDTH-1:0] protocol_errors,
    output reg [PORTS-1:0][COUNTER_WIDTH-1:0] requests,
    output reg [PORTS-1:0][COUNTER_WIDTH-1:0] completions,
    output reg [PORTS-1:0][COUNTER_WIDTH-1:0] bytes_issued,
    output reg [PORTS-1:0][COUNTER_WIDTH-1:0] wait_cycles,
    output reg [PORTS-1:0][COUNTER_WIDTH-1:0] latency_sum,
    output reg [PORTS-1:0][COUNTER_WIDTH-1:0] latency_max
);
    localparam integer OWNER_WIDTH = PORTS > 1 ? $clog2(PORTS) : 1;
    reg [OWNER_WIDTH-1:0] owner;
    reg [COUNTER_WIDTH-1:0] age;
    reg [OWNER_WIDTH-1:0] issued_port;
    integer port_index;

    // The wider second operand retains an entire byte count even with tiny
    // counters used by saturation tests. All counters clamp rather than wrap.
    function automatic [COUNTER_WIDTH-1:0] sat_add(
        input [COUNTER_WIDTH-1:0] a,
        input [COUNTER_WIDTH+7:0] b
    );
        reg [COUNTER_WIDTH+8:0] sum;
        begin
            sum = {9'b0, a} + {1'b0, b};
            sat_add = |sum[COUNTER_WIDTH+8:COUNTER_WIDTH]
                    ? {COUNTER_WIDTH{1'b1}} : sum[COUNTER_WIDTH-1:0];
        end
    endfunction

    wire has_issue = |issue;
    wire single_issue = has_issue && ((issue & (issue - 1'b1)) == 0);
    wire accept_issue = single_issue && (!busy || complete);
    wire bad_cycle = (has_issue && !accept_issue) || (complete && !busy);
    wire [COUNTER_WIDTH-1:0] completed_latency = sat_add(age, 1);

    always @* begin
        issued_port = 0;
        for (integer i = 0; i < PORTS; i = i + 1)
            if (issue[i]) issued_port = OWNER_WIDTH'(i);
    end

    always @(posedge clk) begin
        if (rst || clear) begin
            busy <= 0;
            owner <= 0;
            age <= 0;
            cycles <= 0;
            busy_cycles <= 0;
            protocol_errors <= 0;
            requests <= '0;
            completions <= '0;
            bytes_issued <= '0;
            wait_cycles <= '0;
            latency_sum <= '0;
            latency_max <= '0;
        end else begin
            cycles <= sat_add(cycles, 1);
            if (busy) begin
                busy_cycles <= sat_add(busy_cycles, 1);
                age <= sat_add(age, 1);
            end
            if (bad_cycle) protocol_errors <= sat_add(protocol_errors, 1);
            for (port_index = 0; port_index < PORTS; port_index = port_index + 1)
                if (waiting[port_index])
                    wait_cycles[port_index] <= sat_add(wait_cycles[port_index], 1);

            if (complete && busy) begin
                completions[owner] <= sat_add(completions[owner], 1);
                latency_sum[owner] <= sat_add(latency_sum[owner], completed_latency);
                if (completed_latency > latency_max[owner])
                    latency_max[owner] <= completed_latency;
                busy <= 0;
            end
            // Completion and a new issue may share an edge. The completion is
            // credited to the old owner; the new transaction starts at age zero.
            if (accept_issue) begin
                requests[issued_port] <= sat_add(requests[issued_port], 1);
                bytes_issued[issued_port] <= sat_add(bytes_issued[issued_port], issue_bytes);
                owner <= issued_port;
                age <= 0;
                busy <= 1;
            end
        end
    end
endmodule
`default_nettype wire
