`timescale 1ns/1ps
`default_nettype none

// Memory-facing adapter for PlayStation_core.sflp, not a guest bus decoder.
// Port 0 owns WRAM/BIOS event requests; port 1 owns held GPU VRAM requests.
// Every signal here uses clk. CDCs are outside this block.
module psx_pgate_mem (
    input wire clk, rst,
    input wire wram_read, wram_write, bios_read,
    input wire [20:0] a,
    input wire [4:0] bytes,
    input wire [31:0] dout,
    output wire wram_ack, bios_ack, wram_error, bios_error,
    output wire [127:0] din,
    input wire vram_read, vram_write, vram_flush,
    input wire [18:0] vram_addr,
    input wire [2:0] vram_halfwords,
    input wire [15:0] vram_dout,
    output wire vram_read_ack, vram_write_ack, vram_read_error, vram_write_error,
    output wire [63:0] vram_din,
    output reg protocol_error,
    output wire [1:0] p_valid, p_write,
    input wire [1:0] p_ready,
    output reg [1:0][24:0] p_addr,
    output reg [1:0][3:0] p_halfwords,
    output reg [1:0][127:0] p_wdata,
    output reg [1:0][15:0] p_byte_en,
    input wire [1:0] p_rsp_valid, p_rsp_error,
    output wire [1:0] p_rsp_ready,
    input wire [1:0][127:0] p_rsp_rdata
);
    localparam [1:0] IDLE = 0, SEND = 1, WAIT_RESPONSE = 2, LOCAL_RESPONSE = 3;
    reg [1:0] sys_state, gpu_state;
    reg sys_bios, sys_write, sys_shift;
    reg gpu_read, gpu_write, gpu_local_error;
    reg [127:0] sys_data;
    reg [63:0] gpu_data;
    wire sys_complete = sys_state == WAIT_RESPONSE && p_rsp_valid[0];
    wire gpu_complete = (gpu_state == WAIT_RESPONSE && p_rsp_valid[1]) || gpu_state == LOCAL_RESPONSE;
    wire sys_available = sys_state == IDLE || sys_complete;
    wire sys_request = wram_read || wram_write || bios_read;
    wire sys_collision = (wram_read && wram_write) || (bios_read && (wram_read || wram_write));
    wire small_read = bytes == 1 || bytes == 2;
    wire valid_ram_read = small_read || bytes == 4 || bytes == 8 || bytes == 12 || bytes == 16;
    wire valid_ram_write = small_read || bytes == 4;
    wire valid_bios_read = small_read || bytes == 4;
    wire [20:0] aligned_ram = small_read ? {a[20:1], 1'b0} : {a[20:2], 2'b0};
    wire [18:0] aligned_bios = small_read ? {a[18:1], 1'b0} : {a[18:2], 2'b0};
    wire [21:0] ram_end = {1'b0, aligned_ram} + (small_read ? 22'd2 : {17'b0, bytes});
    wire gpu_bad_span = ({1'b0, vram_addr} + {17'b0, vram_halfwords}) > 20'h80000;
    wire [127:0] current_sys_data = sys_shift ? p_rsp_rdata[0] << 16 : p_rsp_rdata[0];

    // Legacy WRAM DMA engines need idle readiness before issuing an event.
    // During a transaction only its owner sees completion, with its own data.
    assign wram_ack = !rst && (sys_state == IDLE || (sys_complete && !sys_bios));
    assign bios_ack = !rst && (sys_state == IDLE || (sys_complete && sys_bios));
    assign wram_error = !rst && sys_complete && !sys_bios && p_rsp_error[0];
    assign bios_error = !rst && sys_complete && sys_bios && p_rsp_error[0];
    assign din = !rst && sys_complete && !sys_write ? current_sys_data : sys_data;
    assign vram_read_ack = !rst && gpu_complete && gpu_read;
    assign vram_write_ack = !rst && gpu_complete && gpu_write;
    assign vram_read_error = vram_read_ack && (gpu_state == LOCAL_RESPONSE ? gpu_local_error : p_rsp_error[1]);
    assign vram_write_error = vram_write_ack && (gpu_state == LOCAL_RESPONSE ? gpu_local_error : p_rsp_error[1]);
    assign vram_din = !rst && gpu_complete && gpu_read ?
        (gpu_state == LOCAL_RESPONSE ? 64'b0 : p_rsp_rdata[1][63:0]) : gpu_data;
    assign p_valid = {!rst && gpu_state == SEND, !rst && sys_state == SEND};
    assign p_write = {gpu_write, sys_write};
    assign p_rsp_ready = {!rst && gpu_state == WAIT_RESPONSE, !rst && sys_state == WAIT_RESPONSE};

    always @(posedge clk) begin
        if (rst) begin
            sys_state <= IDLE; gpu_state <= IDLE;
            sys_bios <= 0; sys_write <= 0; sys_shift <= 0;
            gpu_read <= 0; gpu_write <= 0; gpu_local_error <= 0;
            sys_data <= 0; gpu_data <= 0; protocol_error <= 0;
            p_addr <= 0; p_halfwords <= 0; p_wdata <= 0; p_byte_en <= 0;
        end else begin
            if (sys_state == SEND && p_ready[0]) sys_state <= WAIT_RESPONSE;
            if (sys_complete) begin
                if (!sys_write) sys_data <= current_sys_data;
                sys_state <= IDLE;
            end
            if (sys_request) begin
                if (!sys_available || sys_collision) protocol_error <= 1;
                else begin
                    sys_state <= SEND; sys_bios <= bios_read; sys_write <= wram_write;
                    sys_shift <= !wram_write && small_read && a[1];
                    p_addr[0] <= bios_read ? 25'h800000 + {7'b0, aligned_bios[18:1]} : {5'b0, aligned_ram[20:1]};
                    p_halfwords[0] <= small_read ? 4'd1 : bios_read ? 4'd2 : bytes[4:1];
                    p_wdata[0] <= bytes == 1 && a[0] ? {96'b0, dout} << 8 : {96'b0, dout};
                    p_byte_en[0] <= bytes == 1 ? (a[0] ? 16'h0002 : 16'h0001) : bytes == 2 ? 16'h0003 : 16'h000F;
                    if ((bios_read && !valid_bios_read) || (wram_read && (!valid_ram_read || ram_end > 22'h200000)) ||
                        (wram_write && !valid_ram_write)) p_halfwords[0] <= 0;
                end
            end
            if (gpu_state == SEND && p_ready[1]) gpu_state <= WAIT_RESPONSE;
            if (gpu_complete) begin
                if (gpu_read) gpu_data <= gpu_state == LOCAL_RESPONSE ? 64'b0 : p_rsp_rdata[1][63:0];
                gpu_state <= IDLE;
            end
            // Held GPU requests are ignored while their one command is pending.
            // The producer observes ack at the completion edge, then changes or
            // drops its request before the next edge. No deassertion bubble is required.
            if (gpu_state == IDLE && (vram_read || vram_write || vram_flush)) begin
                gpu_read <= vram_read; gpu_write <= vram_write || vram_flush;
                gpu_local_error <= 0;
                p_addr[1] <= 25'h100000 + {6'b0, vram_addr};
                p_halfwords[1] <= vram_read ? {1'b0, vram_halfwords} : 4'd1;
                p_wdata[1] <= {112'b0, vram_dout}; p_byte_en[1] <= 16'h0003;
                gpu_state <= SEND;
                if (vram_read && (vram_write || vram_flush)) begin
                    protocol_error <= 1; gpu_local_error <= 1; gpu_state <= LOCAL_RESPONSE;
                end else if (!vram_read && !vram_write) gpu_state <= LOCAL_RESPONSE;
                else if (vram_read && (vram_halfwords == 0 || vram_halfwords > 4 || gpu_bad_span))
                    p_halfwords[1] <= 0;
            end
        end
    end
endmodule
`default_nettype wire
