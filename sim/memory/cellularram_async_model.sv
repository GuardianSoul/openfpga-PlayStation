`timescale 1ns/1ps
`default_nettype none

// One Pocket CellularRAM chip (AS1C8M16PL-70: two 4 M x 16 dies on CE0#/CE1#,
// address and data multiplexed on DQ) in its asynchronous power-up mode.
// Behavioral model for simulation only: it latches the address on ADV#'s
// rising edge, writes at WE#'s or CE#'s rising edge and drives read data
// only once both the access and output-enable times have passed (X before).
// Every listed timing below is checked in nanoseconds; a violation is fatal.
// The figures are the async -70 speed grade's, as agg23's controller states
// them, plus CE# high time, maximum CE# low time and output turn-off.
// Electrical behavior, refresh, the configuration registers (CRE), page
// mode and synchronous bursts are not modeled; CRE high is rejected.
module cellularram_async_model #(
    parameter integer CHIP = 0,              // byte lane pair of a 32-bit word
    parameter real T_AA = 70.0,              // ADV# low to valid data
    parameter real T_OE = 20.0,              // OE# low to valid data
    parameter real T_OHZ = 8.0,              // OE#/CE# high to output released
    parameter real T_VP = 5.0,               // ADV# low pulse
    parameter real T_AVS = 5.0,              // address setup to ADV# high
    parameter real T_AVH = 2.0,              // address hold after ADV# high
    parameter real T_CVS = 7.0,              // CE# low to ADV# high
    parameter real T_WP = 45.0,              // WE# low pulse
    parameter real T_AW = 70.0,              // ADV# low to end of write
    parameter real T_DW = 20.0,              // data setup to end of write
    parameter real T_CPH = 5.0,              // CE# high between accesses
    parameter real T_CEM = 4000.0            // maximum CE# low time
) (
    input wire [21:16] a,
    inout wire [15:0] dq,
    input wire host_oe,                      // the controller drives DQ
    input wire ce0_n, ce1_n, adv_n, oe_n, we_n, ub_n, lb_n, cre
);
    function automatic [7:0] initial_byte(input integer address);
        initial_byte = 8'(address * 17 + (address >> 8) * 23 + 'h5A);
    endfunction
    // Byte (die, halfword address, lane) at word (die*4M + address), lanes
    // 2*CHIP and 2*CHIP+1 of the controller's 32-bit word. Only three 4-16 K
    // word windows are stored: the start, the die boundary and the end.
    reg [7:0] memory [0:32767];
    function automatic integer slot(input [22:0] word);
        if (word < 23'h001000) slot = integer'(word);
        else if (word >= 23'h3FF000 && word < 23'h401000) slot = 4096 + integer'(word - 23'h3FF000);
        else if (word >= 23'h7FF000) slot = 12288 + integer'(word - 23'h7FF000);
        else begin slot = 0; $fatal(1, "PSRAM%0d: word %h outside the modeled windows", CHIP, word); end
    endfunction
    initial for (integer i = 0; i < 16384; i = i + 1) begin
        integer word;
        word = i < 4096 ? i : i < 12288 ? 'h3FF000 + i - 4096 : 'h7FF000 + i - 12288;
        memory[2*i] = initial_byte(word * 4 + CHIP * 2);
        memory[2*i+1] = initial_byte(word * 4 + CHIP * 2 + 1);
    end
    function automatic [7:0] read_byte(input reg die, input [21:0] address, input reg lane);
        read_byte = memory[2 * slot({die, address}) + lane];
    endfunction

    integer reads = 0, writes = 0;
    wire ce_n = ce0_n && ce1_n;
    realtime t_ce_fall = -1e9, t_ce_rise = -1e9, t_adv_fall = -1e9, t_adv_rise = -1e9;
    realtime t_we_fall = -1e9, t_oe_fall = -1e9, t_addr_change = -1e9, t_bm_change = -1e9;
    realtime t_dq_change = -1e9, t_dq_prev_change = -1e9, t_release = -1e9;
    reg [15:0] dq_now = 0, dq_prev = 0;
    reg [1:0] bm_now = 2'b11, bm_prev = 2'b11;
    reg latched = 0, write_access = 0, die = 0, committed = 0;
    reg [21:0] address = 0;
    reg [15:0] drive_value = 0;
    reg [1:0] drive_lanes = 0;

    // DQ as the chip sees it, with the time it last changed.
    always @(dq) begin
        dq_prev = dq_now; t_dq_prev_change = t_dq_change;
        dq_now = dq; t_dq_change = $realtime;
        if (latched && $realtime < t_adv_rise + T_AVH && $realtime != t_adv_rise)
            $fatal(1, "PSRAM%0d: DQ address changed %0.1f ns after ADV# high", CHIP, $realtime - t_adv_rise);
        t_addr_change = $realtime;
    end
    always @(a) begin
        if (latched && $realtime < t_adv_rise + T_AVH && $realtime != t_adv_rise)
            $fatal(1, "PSRAM%0d: A changed %0.1f ns after ADV# high", CHIP, $realtime - t_adv_rise);
        t_addr_change = $realtime;
    end
    always @(ub_n or lb_n) begin
        bm_prev = bm_now; bm_now = {ub_n, lb_n}; t_bm_change = $realtime;
    end

    always @(negedge ce0_n or negedge ce1_n) begin
        if (!ce0_n && !ce1_n) $fatal(1, "PSRAM%0d: both dies selected", CHIP);
        if ($realtime - t_ce_rise < T_CPH) $fatal(1, "PSRAM%0d: CE# high only %0.1f ns", CHIP, $realtime - t_ce_rise);
        if (cre) $fatal(1, "PSRAM%0d: configuration access is not modeled", CHIP);
        t_ce_fall = $realtime; latched = 0; committed = 0;
    end
    always @(negedge adv_n) if (!ce_n || $realtime == t_ce_fall) t_adv_fall = $realtime;
    always @(negedge we_n) t_we_fall = $realtime;
    always @(negedge oe_n) t_oe_fall = $realtime;

    always @(posedge adv_n) if (!ce_n) begin
        if ($realtime - t_adv_fall < T_VP) $fatal(1, "PSRAM%0d: ADV# pulse %0.1f ns", CHIP, $realtime - t_adv_fall);
        if ($realtime - t_ce_fall < T_CVS) $fatal(1, "PSRAM%0d: CE# to ADV# high %0.1f ns", CHIP, $realtime - t_ce_fall);
        if ($realtime - t_addr_change < T_AVS) $fatal(1, "PSRAM%0d: address setup %0.1f ns", CHIP, $realtime - t_addr_change);
        if (^{a, dq} === 1'bx) $fatal(1, "PSRAM%0d: undriven address at ADV# high", CHIP);
        address = {a, dq}; die = ce0_n; write_access = !we_n;
        latched = 1; t_adv_rise = $realtime;
    end

    // A write ends at WE#'s or CE#'s rising edge, whichever is first. Signals
    // that change in the same instant still hold their earlier value (zero
    // hold time), so take the value from before this instant.
    task automatic end_write;
        reg [15:0] data;
        reg [1:0] bm;
        realtime data_since;
        data = t_dq_change == $realtime ? dq_prev : dq_now;
        data_since = t_dq_change == $realtime ? t_dq_prev_change : t_dq_change;
        bm = t_bm_change == $realtime ? bm_prev : bm_now;
        if ($realtime - t_we_fall < T_WP) $fatal(1, "PSRAM%0d: WE# pulse %0.1f ns", CHIP, $realtime - t_we_fall);
        if ($realtime - t_adv_fall < T_AW) $fatal(1, "PSRAM%0d: write time %0.1f ns", CHIP, $realtime - t_adv_fall);
        if (bm != 2'b11 && $realtime - data_since < T_DW)
            $fatal(1, "PSRAM%0d: data setup %0.1f ns", CHIP, $realtime - data_since);
        if (!bm[0]) begin
            if (^data[7:0] === 1'bx) $fatal(1, "PSRAM%0d: undriven low write byte", CHIP);
            memory[2 * slot({die, address})] = data[7:0];
        end
        if (!bm[1]) begin
            if (^data[15:8] === 1'bx) $fatal(1, "PSRAM%0d: undriven high write byte", CHIP);
            memory[2 * slot({die, address}) + 1] = data[15:8];
        end
        writes = writes + 1; committed = 1;
    endtask
    always @(posedge we_n) if (latched && write_access && !committed) end_write();
    always @(posedge ce0_n or posedge ce1_n) begin
        if (latched && write_access && !committed && !we_n) end_write();
        if (latched && !write_access) reads = reads + 1;
        if ($realtime - t_ce_fall > T_CEM) $fatal(1, "PSRAM%0d: CE# low %0.1f ns", CHIP, $realtime - t_ce_fall);
        t_ce_rise = $realtime; latched = 0;
    end

    // Output drive, re-evaluated every half nanosecond: X from OE# low until
    // the data is valid, then the addressed halfword on enabled lanes,
    // released T_OHZ after OE#, CE# or WE# ends the read.
    wire reading = !ce_n && !oe_n && we_n && latched && !write_access;
    reg driving = 0;
    always #0.5 begin
        if (reading) begin
            driving = 1; t_release = $realtime + T_OHZ;
            drive_lanes = ~{ub_n, lb_n};
            if ($realtime >= t_adv_fall + T_AA && $realtime >= t_oe_fall + T_OE)
                drive_value = {read_byte(die, address, 1), read_byte(die, address, 0)};
            else drive_value = 16'hxxxx;
        end else if (driving && $realtime >= t_release) driving = 0;
        if (driving && host_oe) $fatal(1, "PSRAM%0d: DQ driven by both the chip and the controller", CHIP);
    end
    assign dq[7:0] = driving && drive_lanes[0] ? drive_value[7:0] : 8'bz;
    assign dq[15:8] = driving && drive_lanes[1] ? drive_value[15:8] : 8'bz;
endmodule
`default_nettype wire
