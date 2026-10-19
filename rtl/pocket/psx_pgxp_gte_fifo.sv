// SPDX-License-Identifier: MIT
// Fraction sideband for the selected Laxer SXY FIFO. Guest registers and
// arithmetic remain in the original engine. Metadata layout:
// {valid_y, valid_x, fraction_y[3:0], fraction_x[3:0]}.
`default_nettype none
module psx_pgxp_gte_fifo (
    input wire clk, reset, ce, enabled,
    input wire push_x, push_y,
    input wire [3:0] projection_fraction,
    input wire projection_clipped,
    input wire cpu_write,
    input wire [5:0] write_address, read_address,
    input wire [9:0] cpu_metadata,
    output wire [9:0] read_metadata
);
    reg [3:0] fx [0:2];
    reg [3:0] fy [0:2];
    reg [2:0] vx = 0, vy = 0;
    wire fifo_x = push_x || (cpu_write && write_address == 6'd15);
    wire fifo_y = push_y || (cpu_write && write_address == 6'd15);
    wire write_sxy = write_address >= 6'd12 && write_address <= 6'd15;
    // The integer FIFO chooses CPU data whenever cpu_write is asserted,
    // including a simultaneous engine push. Match that priority exactly.
    wire [3:0] new_x = cpu_write ? cpu_metadata[3:0] : projection_fraction;
    wire [3:0] new_y = cpu_write ? cpu_metadata[7:4] : projection_fraction;
    wire new_vx = cpu_write ? (write_sxy && cpu_metadata[8]) : !projection_clipped;
    wire new_vy = cpu_write ? (write_sxy && cpu_metadata[9]) : !projection_clipped;
    integer i;
    always @(posedge clk) begin
        if (reset || !enabled) begin
            vx <= 0;
            vy <= 0;
        end else if (ce) begin
            for (i = 0; i < 2; i = i + 1) begin
                if (fifo_x) begin fx[i] <= fx[i+1]; vx[i] <= vx[i+1]; end
                else if (cpu_write && write_address == 12+i) begin
                    fx[i] <= new_x; vx[i] <= new_vx;
                end
                if (fifo_y) begin fy[i] <= fy[i+1]; vy[i] <= vy[i+1]; end
                else if (cpu_write && write_address == 12+i) begin
                    fy[i] <= new_y; vy[i] <= new_vy;
                end
            end
            if (fifo_x || (cpu_write && write_address == 6'd14)) begin
                fx[2] <= new_x; vx[2] <= new_vx;
            end
            if (fifo_y || (cpu_write && write_address == 6'd14)) begin
                fy[2] <= new_y; vy[2] <= new_vy;
            end
        end
    end
    wire [1:0] slot = read_address == 6'd12 ? 2'd0 :
                      read_address == 6'd13 ? 2'd1 : 2'd2;
    wire selected = enabled && read_address >= 6'd12 && read_address <= 6'd15;
    // Zero invalid fractions too, so downstream integer fallback cannot
    // accidentally consume an undefined or stale payload.
    assign read_metadata = selected ?
        {vy[slot], vx[slot], vy[slot] ? fy[slot] : 4'd0,
                               vx[slot] ? fx[slot] : 4'd0} : 10'd0;
endmodule
`default_nettype wire
