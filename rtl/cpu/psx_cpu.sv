`timescale 1ns/1ps
`default_nettype none

// Sequential R3000 integer execution baseline. Architectural delay slots are
// preserved despite variable bus latency. This is not a cycle-accurate PSX CPU:
// caches, write buffering, debug breakpoints and GTE execution are outside it.
module psx_cpu #(
    parameter [31:0] RESET_PC = 32'hBFC00000,
    parameter [31:0] RESET_STATUS = 32'h10900000
) (
    input wire clk, rst, run,
    input wire [5:0] irq,
    output wire bus_valid,
    input wire bus_ready,
    output reg bus_write, bus_fetch,
    output reg [31:0] bus_addr, bus_wdata, bus_store_raw,
    output reg [3:0] bus_byte_en,
    output reg [1:0] bus_size,
    input wire bus_rsp_valid, bus_rsp_error,
    output wire bus_rsp_ready,
    input wire [31:0] bus_rsp_rdata,
    output wire cop2_valid,
    input wire cop2_ready,
    output reg [2:0] cop2_kind,
    output reg [4:0] cop2_index,
    output reg [24:0] cop2_command,
    output reg [31:0] cop2_wdata,
    input wire cop2_rsp_valid, cop2_rsp_error,
    output wire cop2_rsp_ready,
    input wire [31:0] cop2_rsp_rdata,
    output wire cache_isolated,
    output reg trace_valid, trace_exception,
    output reg [31:0] trace_pc, trace_instruction,
    output reg [4:0] trace_exception_code,
    input wire [4:0] debug_reg,
    output wire [31:0] debug_value, debug_pc, debug_hi, debug_lo,
    output wire [31:0] debug_status, debug_cause, debug_epc, debug_badaddr
);
    localparam [3:0] IDLE=0, FETCH_SEND=1, FETCH_WAIT=2, EXECUTE=3,
        MEM_SEND=4, MEM_WAIT=5, ITERATE=6, COP_SEND=7, COP_WAIT=8;
    reg [3:0] state;
    reg [31:0] gpr [0:31];
    reg [31:0] cp0 [0:31];
    reg [31:0] pc, next_pc, instruction, hi, lo, branch_origin;
    reg delay_slot;
    reg load_pending;
    reg [4:0] load_reg;
    reg [31:0] load_value;
    reg [5:0] memory_op;
    reg [4:0] memory_rt;
    reg [31:0] merge_value;
    reg [1:0] cop_finish; // 0: no GPR result; 1: delayed GPR; 2: store CP2 data
    reg [63:0] product, multiplicand;
    reg [31:0] multiplier, quotient, divisor;
    reg [32:0] remainder;
    reg divide, result_negative, remainder_negative;
    reg [5:0] iteration;

    wire [5:0] op = instruction[31:26], fn = instruction[5:0];
    wire [4:0] rs = instruction[25:21], rt = instruction[20:16], rd = instruction[15:11];
    wire [31:0] rs_value = rs == 0 ? 32'b0 : gpr[rs];
    wire [31:0] rt_value = rt == 0 ? 32'b0 : gpr[rt];
    wire [31:0] immediate = {{16{instruction[15]}}, instruction[15:0]};
    wire [31:0] effective = rs_value + immediate;
    wire [31:0] pc_plus4 = pc + 32'd4;
    wire [31:0] branch_target = pc_plus4 + (immediate << 2);
    wire [31:0] add_result = rs_value + rt_value;
    wire [31:0] sub_result = rs_value - rt_value;
    wire add_overflow = !(rs_value[31] ^ rt_value[31]) && (rs_value[31] ^ add_result[31]);
    wire sub_overflow = (rs_value[31] ^ rt_value[31]) && (rs_value[31] ^ sub_result[31]);
    wire immediate_overflow = !(rs_value[31] ^ immediate[31]) && (rs_value[31] ^ effective[31]);
    wire [31:0] cause = {cp0[13][31:16], irq, cp0[13][9:0]};
    wire interrupt_pending = cp0[12][0] && |(cause[15:8] & cp0[12][15:8]);
    wire [63:0] product_next = product + (multiplier[0] ? multiplicand : 64'b0);
    wire [32:0] remainder_shift = {remainder[31:0], quotient[31]};
    wire quotient_bit = remainder_shift >= {1'b0, divisor};
    wire [32:0] remainder_next = quotient_bit ? remainder_shift - {1'b0, divisor} : remainder_shift;
    wire [31:0] quotient_next = {quotient[30:0], quotient_bit};
    wire [63:0] signed_product = result_negative ? (~product_next + 64'd1) : product_next;
    wire [31:0] loaded_word = bus_rsp_rdata >> {bus_addr[1:0], 3'b000};
    reg [31:0] memory_result;
    wire cp0_unusable = cp0[12][1] && !cp0[12][28];
    assign bus_valid = !rst && (state == FETCH_SEND || state == MEM_SEND);
    assign bus_rsp_ready = !rst && (state == FETCH_WAIT || state == MEM_WAIT);
    assign cop2_valid = !rst && state == COP_SEND;
    assign cop2_rsp_ready = !rst && state == COP_WAIT;
    assign cache_isolated = cp0[12][16];
    assign debug_value = debug_reg == 0 ? 32'b0 : gpr[debug_reg];
    assign debug_pc = pc; assign debug_hi = hi; assign debug_lo = lo;
    assign debug_status = cp0[12]; assign debug_cause = cause;
    assign debug_epc = cp0[14]; assign debug_badaddr = cp0[8];

    always @* begin
        memory_result = bus_rsp_rdata;
        case (memory_op)
            6'h20: memory_result = {{24{loaded_word[7]}}, loaded_word[7:0]};
            6'h21: memory_result = {{16{loaded_word[15]}}, loaded_word[15:0]};
            6'h24: memory_result = {24'b0, loaded_word[7:0]};
            6'h25: memory_result = {16'b0, loaded_word[15:0]};
            6'h22: case (bus_addr[1:0])
                0: memory_result = {bus_rsp_rdata[7:0], merge_value[23:0]};
                1: memory_result = {bus_rsp_rdata[15:0], merge_value[15:0]};
                2: memory_result = {bus_rsp_rdata[23:0], merge_value[7:0]};
                3: memory_result = bus_rsp_rdata;
            endcase
            6'h26: case (bus_addr[1:0])
                0: memory_result = bus_rsp_rdata;
                1: memory_result = {merge_value[31:24], bus_rsp_rdata[31:8]};
                2: memory_result = {merge_value[31:16], bus_rsp_rdata[31:16]};
                3: memory_result = {merge_value[31:8], bus_rsp_rdata[31:24]};
            endcase
            default: ;
        endcase
    end

    task automatic finish(
        input reg write_register, input reg [4:0] index, input reg [31:0] value,
        input reg new_load, input reg [4:0] new_index, input reg [31:0] new_value,
        input reg branch, input reg taken, input reg [31:0] target
    );
        begin
            if (load_pending && load_reg != 0 && !(write_register && index == load_reg) && !(new_load && new_index == load_reg))
                gpr[load_reg] <= load_value;
            if (write_register && index != 0) gpr[index] <= value;
            load_pending <= new_load && new_index != 0;
            load_reg <= new_index; load_value <= new_value;
            pc <= next_pc;
            next_pc <= branch && taken ? target : next_pc + 32'd4;
            delay_slot <= branch;
            if (branch) branch_origin <= pc;
            trace_valid <= 1; trace_exception <= 0; trace_pc <= pc;
            trace_instruction <= instruction; trace_exception_code <= 0;
            state <= IDLE;
        end
    endtask
    task automatic result(input reg [4:0] index, input reg [31:0] value);
        finish(1, index, value, 0, 0, 0, 0, 0, 0);
    endtask
    task automatic advance;
        finish(0, 0, 0, 0, 0, 0, 0, 0, 0);
    endtask
    task automatic exception(
        input reg [4:0] code, input reg address_error, input reg [31:0] bad_address,
        input reg [1:0] coprocessor, input reg [31:0] fault_instruction
    );
        reg [31:0] vector;
        begin
            if (load_pending && load_reg != 0) gpr[load_reg] <= load_value;
            load_pending <= 0;
            cp0[13] <= {delay_slot, 1'b0, coprocessor, 12'b0, irq, cp0[13][9:8], 1'b0, code, 2'b0};
            cp0[14] <= delay_slot ? branch_origin : pc;
            if (address_error) cp0[8] <= bad_address;
            cp0[12][5:0] <= {cp0[12][3:0], 2'b0};
            vector = cp0[12][22] ? 32'hBFC00180 : 32'h80000080;
            pc <= vector; next_pc <= vector + 32'd4; delay_slot <= 0;
            trace_valid <= 1; trace_exception <= 1; trace_pc <= pc;
            trace_instruction <= fault_instruction; trace_exception_code <= code;
            state <= IDLE;
        end
    endtask

    always @(posedge clk) begin
        trace_valid <= 0;
        if (rst) begin
            state <= IDLE; pc <= RESET_PC; next_pc <= RESET_PC + 32'd4;
            instruction <= 0; hi <= 0; lo <= 0; branch_origin <= 0; delay_slot <= 0;
            load_pending <= 0; load_reg <= 0; load_value <= 0;
            memory_op <= 0; memory_rt <= 0; merge_value <= 0; cop_finish <= 0;
            product <= 0; multiplicand <= 0; multiplier <= 0; quotient <= 0; divisor <= 0;
            remainder <= 0; divide <= 0; result_negative <= 0; remainder_negative <= 0; iteration <= 0;
            bus_write <= 0; bus_fetch <= 0; bus_addr <= 0; bus_wdata <= 0; bus_store_raw <= 0; bus_byte_en <= 0; bus_size <= 0;
            cop2_kind <= 0; cop2_index <= 0; cop2_command <= 0; cop2_wdata <= 0;
            trace_valid <= 0; trace_exception <= 0; trace_pc <= 0; trace_instruction <= 0; trace_exception_code <= 0;
            for (integer index = 0; index < 32; index = index + 1) begin gpr[index] <= 0; cp0[index] <= 0; end
            cp0[12] <= RESET_STATUS; cp0[15] <= 32'd2;
        end else case (state)
            IDLE: if (run) begin
                if (interrupt_pending) exception(0, 0, 0, 0, 0);
                else if (pc[1:0] != 0 || (cp0[12][1] && pc[31])) exception(4, 1, pc, 0, 0);
                else begin
                    bus_addr <= pc; bus_write <= 0; bus_fetch <= 1; bus_size <= 2; bus_byte_en <= 4'hF;
                    bus_wdata <= 0; bus_store_raw <= 0; state <= FETCH_SEND;
                end
            end
            FETCH_SEND: if (bus_ready) state <= FETCH_WAIT;
            FETCH_WAIT: if (bus_rsp_valid) begin
                if (bus_rsp_error) exception(6, 0, 0, 0, 0);
                else begin instruction <= bus_rsp_rdata; state <= EXECUTE; end
            end
            EXECUTE: case (op)
                0: case (fn)
                    6'h00: result(rd, rt_value << instruction[10:6]);
                    6'h02: result(rd, rt_value >> instruction[10:6]);
                    6'h03: result(rd, $signed(rt_value) >>> instruction[10:6]);
                    6'h04: result(rd, rt_value << rs_value[4:0]);
                    6'h06: result(rd, rt_value >> rs_value[4:0]);
                    6'h07: result(rd, $signed(rt_value) >>> rs_value[4:0]);
                    6'h08: finish(0, 0, 0, 0, 0, 0, 1, 1, rs_value);
                    6'h09: finish(1, rd, pc + 32'd8, 0, 0, 0, 1, 1, rs_value);
                    6'h0C: exception(8, 0, 0, 0, instruction);
                    6'h0D: exception(9, 0, 0, 0, instruction);
                    6'h10: result(rd, hi);
                    6'h11: begin hi <= rs_value; advance(); end
                    6'h12: result(rd, lo);
                    6'h13: begin lo <= rs_value; advance(); end
                    6'h18, 6'h19, 6'h1A, 6'h1B: begin
                        divide <= fn[1]; iteration <= 0;
                        result_negative <= !fn[0] && (rs_value[31] ^ rt_value[31]);
                        remainder_negative <= !fn[0] && rs_value[31];
                        product <= 0; remainder <= 0;
                        multiplicand <= {32'b0, (!fn[0] && rs_value[31] ? ~rs_value + 32'd1 : rs_value)};
                        multiplier <= !fn[0] && rt_value[31] ? ~rt_value + 32'd1 : rt_value;
                        quotient <= !fn[0] && rs_value[31] ? ~rs_value + 32'd1 : rs_value;
                        divisor <= !fn[0] && rt_value[31] ? ~rt_value + 32'd1 : rt_value;
                        if (fn[1] && rt_value == 0) begin
                            lo <= !fn[0] && rs_value[31] ? 32'd1 : 32'hFFFFFFFF;
                            hi <= rs_value; advance();
                        end else state <= ITERATE;
                    end
                    6'h20: if (add_overflow) exception(12, 0, 0, 0, instruction); else result(rd, add_result);
                    6'h21: result(rd, add_result);
                    6'h22: if (sub_overflow) exception(12, 0, 0, 0, instruction); else result(rd, sub_result);
                    6'h23: result(rd, sub_result);
                    6'h24: result(rd, rs_value & rt_value);
                    6'h25: result(rd, rs_value | rt_value);
                    6'h26: result(rd, rs_value ^ rt_value);
                    6'h27: result(rd, ~(rs_value | rt_value));
                    6'h2A: result(rd, {31'b0, $signed(rs_value) < $signed(rt_value)});
                    6'h2B: result(rd, {31'b0, rs_value < rt_value});
                    default: exception(10, 0, 0, 0, instruction);
                endcase
                6'h01: if (rt == 0 || rt == 1 || rt == 16 || rt == 17)
                    finish(rt[4], 31, pc + 32'd8, 0, 0, 0, 1, rt[0] ? !rs_value[31] : rs_value[31], branch_target);
                    else exception(10, 0, 0, 0, instruction);
                6'h02, 6'h03: finish(op[0], 31, pc + 32'd8, 0, 0, 0, 1, 1, {pc_plus4[31:28], instruction[25:0], 2'b0});
                6'h04: finish(0, 0, 0, 0, 0, 0, 1, rs_value == rt_value, branch_target);
                6'h05: finish(0, 0, 0, 0, 0, 0, 1, rs_value != rt_value, branch_target);
                6'h06: finish(0, 0, 0, 0, 0, 0, 1, $signed(rs_value) <= 0, branch_target);
                6'h07: finish(0, 0, 0, 0, 0, 0, 1, $signed(rs_value) > 0, branch_target);
                6'h08: if (immediate_overflow) exception(12, 0, 0, 0, instruction); else result(rt, effective);
                6'h09: result(rt, effective);
                6'h0A: result(rt, {31'b0, $signed(rs_value) < $signed(immediate)});
                6'h0B: result(rt, {31'b0, rs_value < immediate});
                6'h0C: result(rt, rs_value & {16'b0, instruction[15:0]});
                6'h0D: result(rt, rs_value | {16'b0, instruction[15:0]});
                6'h0E: result(rt, rs_value ^ {16'b0, instruction[15:0]});
                6'h0F: result(rt, {instruction[15:0], 16'b0});
                6'h10: if (cp0_unusable) exception(11, 0, 0, 0, instruction);
                    else if (rs == 0) finish(0, 0, 0, 1, rt, rd == 13 ? cause : cp0[rd], 0, 0, 0);
                    else if (rs == 4) begin
                        case (rd)
                            3, 5, 7, 9, 11: cp0[rd] <= rt_value;
                            12: cp0[12] <= rt_value & 32'hF27FFF3F;
                            13: cp0[13][9:8] <= rt_value[9:8];
                            default: ; // read-only or reserved registers
                        endcase
                        advance();
                    end else if (instruction == 32'h42000010) begin
                        cp0[12][3:0] <= cp0[12][5:2]; advance();
                    end else exception(10, 0, 0, 0, instruction);
                6'h11, 6'h13: exception(11, 0, 0, op[1:0], instruction);
                6'h12: if (!cp0[12][30]) exception(11, 0, 0, 2, instruction);
                    else if (rs == 0 || rs == 2 || rs == 4 || rs == 6 || rs[4]) begin
                        cop2_kind <= rs[4] ? 3'd4 : rs == 0 ? 3'd0 : rs == 2 ? 3'd1 : rs == 4 ? 3'd2 : 3'd3;
                        cop2_index <= rd; cop2_command <= instruction[24:0]; cop2_wdata <= rt_value;
                        cop_finish <= rs == 0 || rs == 2 ? 2'd1 : 2'd0; memory_rt <= rt;
                        state <= COP_SEND;
                    end else exception(10, 0, 0, 0, instruction);
                6'h20, 6'h21, 6'h22, 6'h23, 6'h24, 6'h25, 6'h26,
                6'h28, 6'h29, 6'h2A, 6'h2B, 6'h2E, 6'h32, 6'h3A: begin
                    if ((op == 6'h32 || op == 6'h3A) && !cp0[12][30]) exception(11, 0, 0, 2, instruction);
                    else if ((cp0[12][1] && effective[31]) ||
                        ((op == 6'h21 || op == 6'h25 || op == 6'h29) && effective[0]) ||
                        ((op == 6'h23 || op == 6'h2B || op == 6'h32 || op == 6'h3A) && effective[1:0] != 0))
                        exception(op[3] ? 5'd5 : 5'd4, 1, effective, 0, instruction);
                    else begin
                        bus_addr <= effective; bus_write <= op[3]; bus_fetch <= 0;
                        bus_store_raw <= rt_value; bus_wdata <= rt_value;
                        bus_byte_en <= 4'hF; bus_size <= 2;
                        memory_op <= op; memory_rt <= rt;
                        merge_value <= load_pending && load_reg == rt ? load_value : rt_value;
                        case (op)
                            6'h20, 6'h24: bus_size <= 0;
                            6'h21, 6'h25: bus_size <= 1;
                            6'h28: begin bus_size <= 0; bus_byte_en <= 4'b0001 << effective[1:0]; bus_wdata <= rt_value << {effective[1:0], 3'b0}; end
                            6'h29: begin bus_size <= 1; bus_byte_en <= 4'b0011 << effective[1:0]; bus_wdata <= rt_value << {effective[1:0], 3'b0}; end
                            6'h2A: begin bus_byte_en <= 4'hF >> (3-effective[1:0]); bus_wdata <= rt_value >> ((3-effective[1:0])*8); end
                            6'h2E: begin bus_byte_en <= 4'hF << effective[1:0]; bus_wdata <= rt_value << {effective[1:0], 3'b0}; end
                            default: ;
                        endcase
                        if (op == 6'h3A) begin
                            cop2_kind <= 0; cop2_index <= rt; cop2_command <= 0; cop2_wdata <= 0;
                            cop_finish <= 2; state <= COP_SEND;
                        end else state <= MEM_SEND;
                    end
                end
                6'h30, 6'h31, 6'h33, 6'h38, 6'h39, 6'h3B: exception(11, 0, 0, op[1:0], instruction);
                default: exception(10, 0, 0, 0, instruction);
            endcase
            MEM_SEND: if (bus_ready) state <= MEM_WAIT;
            MEM_WAIT: if (bus_rsp_valid) begin
                if (bus_rsp_error) exception(7, 0, 0, 0, instruction);
                else if (memory_op == 6'h32) begin
                    cop2_kind <= 2; cop2_index <= memory_rt; cop2_command <= 0; cop2_wdata <= bus_rsp_rdata;
                    cop_finish <= 0; state <= COP_SEND;
                end else if (bus_write) advance();
                else finish(0, 0, 0, 1, memory_rt, memory_result, 0, 0, 0);
            end
            ITERATE: begin
                iteration <= iteration + 6'd1;
                if (divide) begin quotient <= quotient_next; remainder <= remainder_next; end
                else begin product <= product_next; multiplicand <= multiplicand << 1; multiplier <= multiplier >> 1; end
                if (iteration == 31) begin
                    if (divide) begin
                        lo <= result_negative ? ~quotient_next + 32'd1 : quotient_next;
                        hi <= remainder_negative ? ~remainder_next[31:0] + 32'd1 : remainder_next[31:0];
                    end else begin hi <= signed_product[63:32]; lo <= signed_product[31:0]; end
                    advance();
                end
            end
            COP_SEND: if (cop2_ready) state <= COP_WAIT;
            COP_WAIT: if (cop2_rsp_valid) begin
                if (cop2_rsp_error) exception(10, 0, 0, 0, instruction);
                else if (cop_finish == 1) finish(0, 0, 0, 1, memory_rt, cop2_rsp_rdata, 0, 0, 0);
                else if (cop_finish == 2) begin bus_wdata <= cop2_rsp_rdata; bus_store_raw <= cop2_rsp_rdata; state <= MEM_SEND; end
                else advance();
            end
            default: state <= IDLE;
        endcase
    end
endmodule
`default_nettype wire
