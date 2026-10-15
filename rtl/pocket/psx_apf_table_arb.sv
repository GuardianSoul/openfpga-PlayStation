// SPDX-License-Identifier: MIT
// Host-clock leases for the single APF table A-port. Consumers keep request
// high for their complete lookup/update and advance only while granted.
// The B-port stays with the pinned bridge handler. Table writes/population
// from that host port must be serialized against these leases by the caller.
module psx_apf_table_arb #(parameter integer CLIENTS=2) (
    input wire clk,reset,
    input wire [CLIENTS-1:0] request,write_enable,
    input wire [CLIENTS-1:0][9:0] address,
    input wire [CLIENTS-1:0][31:0] data,
    output wire [CLIENTS-1:0] grant,
    output wire [9:0] table_address,output wire table_write,
    output wire [31:0] table_data
);
    localparam integer IW=CLIENTS>1 ? $clog2(CLIENTS):1;
    reg owned=0;
    reg [IW-1:0] owner=0,next_client=0,selected=0;
    reg found=0;
    always @* begin
        selected=0;found=0;
        for(integer n=0;n<CLIENTS;n++) begin
            integer candidate;
            candidate=(integer'(next_client)+n)%CLIENTS;
            if(!found && request[candidate]) begin selected=IW'(candidate);found=1;end
        end
    end
    assign grant=owned && !reset ? ({{(CLIENTS-1){1'b0}},1'b1}<<owner):0;
    assign table_address=owned ? address[owner]:0;
    assign table_write=owned && !reset && request[owner] && write_enable[owner];
    assign table_data=data[owner];
    always @(posedge clk) begin
        if(reset) begin owned<=0;owner<=0;next_client<=0;end
        else if(owned) begin
            if(!request[owner]) begin owned<=0;next_client<=owner==CLIENTS-1 ? 0:owner+1'b1;end
        end else if(found) begin owned<=1;owner<=selected;end
    end
endmodule
