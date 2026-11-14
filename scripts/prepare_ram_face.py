#!/usr/bin/env python3
"""Rig stages 1-2 of the main-RAM redesign (docs/memctrl-redesign.md). Simulation only.

Pure text functions over the K4 rig sources; native_timing_trial.py applies them.

controller_text(k4_ctrl)  K4 controller (build/mr/k4-ctrl.sv = CPU/mem's full9.sv) plus:
  - hint PRECHARGE decided straight from hint_st (one clk3x earlier than K4's hint_go_q),
  - hint ACTIVE: a hinted main-RAM row whose bank is closed is opened (never READ/WRITE),
  - a per-bank tRAS guard for every PRECHARGE of a kept-open main-RAM bank
    (a hint ACTIVE can now be followed by a PRECHARGE sooner than any access could).
wrapper_text(native_mem)  the PS1 face in psx_native_memory, FACE=1:
  - early send: a scalar RAM read that memorymux parks in WAITFORRAMREAD (ram_early)
    goes to the controller in that clk1x; memorymux's later ram_ena for it is swallowed,
  - fixed release: ram_done for a scalar read is given in the clk1x MiSTer's controller
    would give it (3 clk1x after ram_ena; 4 after the early send), or later if the
    data is late. Never earlier.
bench_text(bench, cpu_hint, dma_hint)  rig bench: ram_early from memorymux's state, and
  the hint port fed from the pending stage-4 load and the decode stage (cpu_hint='decode'),
  the stage-4 load only (cpu_hint='m4'), the release form's registered hint (cpu_hint='rel') or K4's registered request hint (cpu_hint='k4'), merged with the DMA's next read address (dma_hint=True).
Planted faults (fault=...): 'early-release' / 'late-release' (release one clk1x before / after the PS1 time),
  'wrong-hint' (hint address +2 KiB: another row of the same bank),
  'no-ras' (tRAS guard removed), 'act-no-open' (hint ACTIVE leaves open_v clear).
"""
import re

TAG = 'ram-face'


def once(s, old, new, count=1):
    if s.count(old) != count:
        raise ValueError(f'{TAG}: expected {count} of {old[:70]!r}, found {s.count(old)}')
    return s.replace(old, new)


def controller_text(text, fault=None):
    extras = set((fault or '').split('+')[1:]); fault = (fault or '').split('+')[0] or None
    if TAG in text:
        raise ValueError('already carries ' + TAG)
    lean = fault in ('lean', 'lean3', 'leannm', 'leannmro', 'leannmrd', 'leannmeq')
    lean3 = fault == 'lean3'
    nomiss = fault in ('leannm', 'leannmro', 'leannmrd', 'leannmeq')
    eqgate = fault == 'leannmeq'
    refdefer = fault == 'leannmrd'
    reopen = fault == 'leannmro'
    if lean:
        fault = None
    else:  # hint survival (needed for a decode-stage hint captured on another request's edge)
        text = once(text, '                if (after_base && ch1_req) begin hint_v <= 0; hint_precharged <= 0; end\n',
                    '                if (after_base && ch1_req && !(ch1_hint_req && ch1_hint_addr[26:2] != ch1_addr[26:2])) begin hint_v <= 0; hint_precharged <= 0; end // ' + TAG + '\n')
    text = once(text, '    reg hint_go_q = 0;\n',
                '    reg hint_go_q = 0;\n'
                f'    // {TAG}: tRAS left per kept-open main-RAM bank (ACTIVE sets 4; PRECHARGE needs 0)\n'
                '    reg [2:0] ras_left0 = 0, ras_left1 = 0;\n'
                '    wire ras_ok_all = ras_left0 == 0 && ras_left1 == 0;\n'
                '    function ras_ok(input b); ras_ok = b ? ras_left1 == 0 : ras_left0 == 0; endfunction\n')
    # decrement first in the clocked block; the ACTIVE set below wins (later NBA)
    text = once(text, '            // Refresh interval\n',
                f'            if (ras_left0 != 0) ras_left0 <= ras_left0 - 1\'b1; // {TAG}\n'
                '            if (ras_left1 != 0) ras_left1 <= ras_left1 - 1\'b1;\n'
                '            // Refresh interval\n')
    guard = ('ras_ok_all' if fault != 'no-ras' else "1'b1")
    one = ('ras_ok(idle_addr[10])' if fault != 'no-ras' else "1'b1")
    text = once(text,
                '                if (OPEN_ROW != 0 && (need_ref ? open_v != 0 : next_st == ST_MISS)) begin\n',
                f'                if (OPEN_ROW != 0 && (need_ref ? open_v != 0 : next_st == ST_MISS) && !(need_ref ? {guard} : {one})) begin\n'
                f'                    // {TAG}: the bank was opened less than tRAS ago (hint ACTIVE); wait\n'
                '                end else if (OPEN_ROW != 0 && (need_ref ? open_v != 0 : next_st == ST_MISS)) begin\n')
    hb = ('ras_ok(hint_addr[10])' if fault != 'no-ras' else "1'b1")
    text = once(text,
                '                end else if (OPEN_ROW != 0 && hint_go_q && // ram-read-hint\n'
                '                             !pend3_new && dmafifo_empty && !dmafifo_read && hint_ref_ok) begin\n',
                f'                end else if (OPEN_ROW != 0 && hint_v && !hint_precharged && hint_st == ST_MISS && hint_addr[26:21] == 0 && {hb} && // {TAG}: hint_st direct\n'
                '                             !pend3_new && dmafifo_empty && !dmafifo_read && hint_ref_ok) begin\n')
    open_set = ('open_v[hint_addr[10]] <= 1; ' if fault != 'act-no-open' else '')
    text = once(text,
                '                    hint_precharged <= 1;\n'
                '                    op <= OP_PRE; step <= 1; last <= 1;\n'
                '                end\n',
                '                    hint_precharged <= 1;\n'
                '                    op <= OP_PRE; step <= 1; last <= 1;\n'
                '                end else if (OPEN_ROW != 0 && hint_v && hint_st == ST_EMPTY && hint_addr[26:21] == 0 &&\n'
                f'                             !pend3_new && dmafifo_empty && !dmafifo_read && hint_ref_ok) begin // {TAG}\n'
                '                    // Open the hinted row; the real read then issues READ as a row hit.\n'
                '                    cmd <= C_ACT; a <= row_of(hint_addr); ba <= bank_of(hint_addr); ncs <= 0;\n'
                f'                    {open_set}open_row[hint_addr[10]] <= hint_addr[22:11];\n'
                "                    if (hint_addr[10]) ras_left1 <= 3'd4; else ras_left0 <= 3'd4;\n"
                '                    hint_precharged <= 0;\n'
                '                    op <= OP_PRE; step <= 1; last <= 1;   // busy 2 edges: tRCD before a READ\n'
                '                end\n')
    text = once(text,
                '                        if (keep_open(sel_adr)) begin open_v[sel_adr[10]] <= 1; open_row[sel_adr[10]] <= sel_adr[22:11]; end\n',
                '                        if (keep_open(sel_adr)) begin open_v[sel_adr[10]] <= 1; open_row[sel_adr[10]] <= sel_adr[22:11];\n'
                f"                            if (sel_adr[10]) ras_left1 <= 3'd4; else ras_left0 <= 3'd4; end // {TAG}\n")
    if fault == 'trace':
        text = once(text, '                    hint_precharged <= 0;\n                    op <= OP_PRE; step <= 1; last <= 1;   // busy 2 edges: tRCD before a READ\n',
                    '                    hint_precharged <= 0;\n                    op <= OP_PRE; step <= 1; last <= 1;   // busy 2 edges: tRCD before a READ\n'
                    '                    if (hint_addr[20:0]==21\'h4710||hint_addr[20:0]==21\'h714||hint_addr[20:0]==21\'h140000||hint_addr[20:0]==21\'h180000) $display("CT t=%0t HACT %h", $time, hint_addr[20:0]);\n')
        text = once(text, '                    hint_precharged <= 1;\n',
                    '                    hint_precharged <= 1;\n'
                    '                    if (hint_addr[20:0]==21\'h4710||hint_addr[20:0]==21\'h714||hint_addr[20:0]==21\'h140000||hint_addr[20:0]==21\'h180000) $display("CT t=%0t HPRE %h", $time, hint_addr[20:0]);\n')
        text = once(text, '            if (after_base && ch1_req && !took1) begin pend1 <= 1; ch1_adr_q <= ch1_addr;\n',
                    '            if (after_base && ch1_req && (ch1_addr[20:0]==21\'h4710||ch1_addr[20:0]==21\'h714||ch1_addr[20:0]==21\'h140000||ch1_addr[20:0]==21\'h180000)) $display("CT t=%0t REQ %h op=%0d st1n=%0d hv=%0d hst=%0d ha=%h", $time, ch1_addr[20:0], op, row_state(ch1_addr), hint_v, hint_st, hint_addr[20:0]);\n'
                    '            if (after_base && ch1_req && !took1) begin pend1 <= 1; ch1_adr_q <= ch1_addr;\n')
    if fault == 'noearly':  # drop K's early refresh (room): no prepaid refresh is ever granted
        text = once(text, '    wire early_quiet_c = OPEN_ROW != 0 && // sdram-early-refresh\n',
                    f"    wire early_quiet_c = 1'b0 && OPEN_ROW != 0 && // sdram-early-refresh ({TAG}: off)\n")
    if lean:  # the hint is the idle address when nothing is pending: no separate pin override
        decl = '    reg hint_v = 0, hint_precharged = 0;\n    reg [26:0] hint_addr = 0;\n'
        text = once(text, decl, '')
        text = once(text, '    wire [26:0] idle_addr = spu_first ? ch3_a', decl + '    wire [26:0] idle_addr = spu_first ? ch3_a')
        text = once(text, '                          : (pend2 || (!LATE_START && ch2_req)) ? ch2_a : ch3_a;\n',
                    f'                          : (pend2 || (!LATE_START && ch2_req)) ? ch2_a : pend3 ? ch3_a : hint_addr; // {TAG}\n')
        text = once(text, '                       : row_of((pend2 || (!LATE_START && ch2_req)) ? ch2_a : ch3_a);\n',
                    f'                       : row_of((pend2 || (!LATE_START && ch2_req)) ? ch2_a : pend3 ? ch3_a : hint_addr); // {TAG}\n')
        text = once(text, "                    cmd <= C_PRE; a[10] <= 1'b0; ba <= bank_of(hint_addr); ncs <= 0; // one bank\n",
                    "                    cmd <= C_PRE; a[10] <= 1'b0; // one bank; ba = bank_of(idle_addr) = the hint's (" + TAG + ")\n")
        text = once(text, '                    cmd <= C_ACT; a <= row_of(hint_addr); ba <= bank_of(hint_addr); ncs <= 0;\n',
                    '                    cmd <= C_ACT;   // a, ba: idle_a / idle_addr are the hint\'s when nothing is pending\n')
    if lean:  # a[10] on idle edges from a short expression (out of the tRAS / hint-decision cone)
        # PRE: need_ref (all banks) or 0 (one bank); hint PRE: 0; ACT / hit READ: idle_a[10]; a
        # tRAS-held NOP or a REF: don't care. A hint PRE needs hint_st == ST_MISS with nothing
        # pending; a hint ACT needs ST_EMPTY and every real access needs something pending.
        text = once(text, '    wire [12:0] idle_a = spu_first ? row_of(ch3_a)',
                    '    wire pre_cond = OPEN_ROW != 0 && (need_ref ? open_v != 0 : next_st == ST_MISS); // ' + TAG + '\n'
                    '    wire idle_nothing = !spu_first && !fifo_v && !pend1 && !pend2 && !pend3;\n'
                    '    wire [12:0] idle_a = spu_first ? row_of(ch3_a)')
        text = once(text, "ras_left0 <= 3'd4; end // " + TAG + "\n                    end\n                end\n            end\n",
                    "ras_left0 <= 3'd4; end // " + TAG + "\n                    end\n                end\n"
                    "                a[10] <= pre_cond ? need_ref : (idle_nothing && hint_st == ST_MISS) ? 1'b0 : idle_a[10]; // " + TAG + "\n"
                    "            end\n")
    if 'dmatrace' in extras:  # diagnostic: DMA read captures with the row state they see
        text = once(text, '            if (after_base && ch1_req && !took1) begin pend1 <= 1; ch1_adr_q <= ch1_addr;\n',
                    '            if (after_base && ch1_req && ch1_dma) $display("CD t=%0t DREQ %h cnt=%0d op=%0d st=%0d hv=%0d ha=%h hst=%0d fifo=%0d p3=%0d ref=%0d", $time, ch1_addr[20:0], ch1_cntDMA, op, row_state(ch1_addr), hint_v, hint_addr[20:0], hint_st, fifo_v, pend3, need_ref);\n'
                    '            if (after_base && ch1_req && !took1) begin pend1 <= 1; ch1_adr_q <= ch1_addr;\n')
    if 'dmatrace' in extras:  # last 24 edges of commands / state, dumped at each DMA read capture
        text = once(text, '    reg hint_go_q = 0;\n', '''    reg hint_go_q = 0;
    reg [63:0] tr_t [0:23]; reg [2:0] tr_c [0:23]; reg [1:0] tr_b [0:23]; reg [3:0] tr_op [0:23]; reg [12:0] tr_a [0:23];
    reg [4:0] tr_i = 0; integer tr_k;
    always @(posedge clk) begin
        tr_t[tr_i] <= $time; tr_c[tr_i] <= cmd; tr_b[tr_i] <= ba; tr_op[tr_i] <= op; tr_a[tr_i] <= a; tr_i <= tr_i == 5'd23 ? 5'd0 : tr_i + 1'b1;
        if (after_base && ch1_req && ch1_dma) for (tr_k = 0; tr_k < 24; tr_k = tr_k + 1)
            if (tr_c[(tr_i + tr_k) % 24] != 3'b111 || tr_op[(tr_i + tr_k) % 24] != 0)
                $display("CH t=%0t cmd=%b ba=%0d a=%h op=%0d", tr_t[(tr_i + tr_k) % 24], tr_c[(tr_i + tr_k) % 24], tr_b[(tr_i + tr_k) % 24], tr_a[(tr_i + tr_k) % 24], tr_op[(tr_i + tr_k) % 24]);
    end
''')
        text = once(text, '                    chosen = OP_REFRESH;\n',
                    '                    chosen = OP_REFRESH;\n                    $display("CR t=%0t REF owed=%0d early=%0d/%0d force=%0d", $time, ref_owed, early_pending, early_quiet, force_pend);\n')
    if lean:  # idle_a's open-row READ column without need_ref: with need_ref set the edge is a
        # PRECHARGE (only A10, set below), a REFRESH or a tRAS-held NOP (F7 s6: ref_owed_nz -> a[3] -0.052 ns)
        text = once(text, '    wire hit1 = OPEN_ROW != 0 && !need_ref && st1 == ST_HIT && ch1_rnw;\n',
                    '    wire hit1 = OPEN_ROW != 0 && st1 == ST_HIT && ch1_rnw; // ' + TAG + ': need_ref left out (a[] only)\n')
    if lean:  # open_row written from idle_addr on every idle edge whose main-RAM bank is closed: a
        # closed bank's row is never read (row_state checks open_v) and every ACTIVE goes to a closed
        # bank through idle_addr (F8 s12: op -> open_row enables -0.147 ns)
        text = once(text, '                        if (keep_open(sel_adr)) begin open_v[sel_adr[10]] <= 1; open_row[sel_adr[10]] <= sel_adr[22:11];\n',
                    '                        if (keep_open(sel_adr)) begin open_v[sel_adr[10]] <= 1;\n')
        text = once(text, '                    open_v[hint_addr[10]] <= 1; open_row[hint_addr[10]] <= hint_addr[22:11];\n',
                    '                    open_v[hint_addr[10]] <= 1;\n')
        text = once(text, "                a[10] <= pre_cond ? need_ref :",
                    "                if (keep_open(idle_addr) && !open_v[idle_addr[10]]) open_row[idle_addr[10]] <= idle_addr[22:11]; // " + TAG + "\n"
                    "                a[10] <= pre_cond ? need_ref :")
    if nomiss:  # K's row-conflict fast return off: the face releases at fixed times and the hint
        # opens rows ahead (its clk3x ready reaches clk1x/clk2x consumers: K6n s6 -0.158 ns)
        text = once(text, '                        miss_read_return <= 1; // ram-miss-return\n',
                    "                        miss_read_return <= 1'b0; // ram-miss-return (" + TAG + ': off)\n')
        text = once(text, '                miss_read_return <= hint_precharged && hint_addr[26:2] == ch1_addr[26:2] && ch1_rnw && !ch1_dma && !ch1_cache && keep_open(ch1_addr);\n',
                    "                miss_read_return <= 1'b0; // " + TAG + ': fast return off\n')
    if reopen:  # hidden refresh: the main-RAM rows open before a refresh are reopened after it,
        # one at a time, as hints (hint ACTIVE path); a real hint or request takes over at once
        text = once(text, '    reg hint_go_q = 0;\n', '    reg hint_go_q = 0;\n    reg [1:0] reopen = 0; // ' + TAG + '\n')
        text = once(text, '    reg [1:0] st1 = 0, st2 = 0, st3 = 0, stf = 0;\n', '    reg [1:0] st1 = 0, st2 = 0, st3 = 0, stf = 0;\n'
                    "    wire [26:0] ro_addr = {4'b0, open_row[reopen[0] ? 1'b0 : 1'b1], reopen[0] ? 1'b0 : 1'b1, 10'b0}; // " + TAG + "\n")
        text = once(text, '                    if (need_ref) open_v <= 0; else open_v[idle_addr[10]] <= 0;\n',
                    '                    if (need_ref) begin open_v <= 0; reopen <= open_v; end else open_v[idle_addr[10]] <= 0; // ' + TAG + '\n')
        text = once(text, '                end else if (hint_v) begin\n',
                    "                end else if (!hint_v && reopen != 0 && op == OP_IDLE && !need_ref) begin // " + TAG + ": reopen\n"
                    "                    hint_v <= 1; hint_precharged <= 0; hint_age <= 0;\n"
                    "                    hint_addr <= ro_addr;\n"
                    "                    if (reopen[0]) reopen[0] <= 0; else reopen[1] <= 0;\n"
                    '                end else if (hint_v) begin\n')
        text = once(text, '                hint_st <= row_state(after_base && ch1_hint_req ? ch1_hint_addr : hint_addr);\n',
                    '                hint_st <= row_state(after_base && ch1_hint_req ? ch1_hint_addr : hint_addr);\n'
                    "                if (!(after_base && ch1_hint_req) && !hint_v && reopen != 0 && op == OP_IDLE && !need_ref) hint_st <= row_state(ro_addr); // " + TAG + "\n")
    if refdefer:  # a periodic refresh waits while a hint says an access is coming (fewer than 4 owed);
        # registered one edge ahead, so need_ref keeps its depth
        text = once(text, '    reg hint_go_q = 0;\n', '    reg hint_go_q = 0;\n    reg ref_due_q = 0; // ' + TAG + ': ref_owed_nz unless a hint defers it\n')
        text = once(text, '    assign need_ref = ref_owed_nz ||', '    assign need_ref = ref_due_q ||')
        text = once(text, '            hint_ref_ok <= ref_timer > 4 || ref_timer == 0;\n',
                    '            hint_ref_ok <= ref_timer > 4 || ref_timer == 0;\n'
                    "            ref_due_q <= ref_owed_nz && !(hint_v && ref_owed < 4'd4); // " + TAG + "\n")
    if eqgate:  # no prepaid (early) refresh while the hint port is asserted: a DMA keeps it asserted for
        # the whole transfer, and the gap between a consumed hint and the next block's let one in
        text = once(text, '        dmafifo_empty && !dmafifo_read && !hint_v;\n',
                    '        dmafifo_empty && !dmafifo_read && !hint_v && !ch1_hint_req; // ' + TAG + '\n')
    if lean3:  # hint_st from the incoming hint address on every clk1x boundary: the request (CPU
        # stage-4 decode, F4 sign-off -0.27 ns) only drives enables and hint_st_bad, which holds the
        # hint for one clk3x when hint_st was taken from an address that came without a request
        text = once(text, '    reg [1:0] hint_st = 0;\n', '    reg [1:0] hint_st = 0;\n    reg hint_st_bad = 0; // ' + TAG + '\n')
        text = once(text, '                hint_st <= row_state(after_base && ch1_hint_req ? ch1_hint_addr : hint_addr);\n',
                    '                hint_st <= row_state(after_base ? ch1_hint_addr : hint_addr); // ' + TAG + '\n'
                    '                hint_st_bad <= after_base && !ch1_hint_req;\n')
        text = once(text, 'end else if (OPEN_ROW != 0 && hint_v && !hint_precharged && hint_st == ST_MISS',
                    'end else if (OPEN_ROW != 0 && hint_v && !hint_st_bad && !hint_precharged && hint_st == ST_MISS')
        text = once(text, 'end else if (OPEN_ROW != 0 && hint_v && hint_st == ST_EMPTY',
                    'end else if (OPEN_ROW != 0 && hint_v && !hint_st_bad && hint_st == ST_EMPTY')
    return f'// {TAG}: prepare_ram_face.controller_text\n' + text


WRAP_PARAM = 'module psx_native_memory #(parameter READ_HINT = 0, parameter DMA_MISS_RETURN = 0, parameter EARLY_REFRESH = 0) (\n'
WRAP_PORT = '    input wire ram_hint_req, input wire [24:0] ram_hint_addr,\n'
WRAP_DONE = '    assign ram_done=rd_ready || wr_ready;\n'
WRAP_REQ = '.ch1_req(ram_ena && ram_rnw),'


def wrapper_text(text, fault=None, wface=None):
    """wface: (Dh, Dm, P, R) write face: a RAM write is released Dh clk1x after ram_ena on the
    same 1 KiB page (bits 22..10) as the previous write, Dm on a change; every steal window (R clk1x
    each P clk1x of a free-running counter) that opens while a write is in flight lengthens it by R
    (RAM time taken, as a DRAM refresh would). None: off."""
    if TAG in text:
        raise ValueError('already carries ' + TAG)
    text = once(text, WRAP_PARAM, WRAP_PARAM.replace(') (', ', parameter FACE = 0) ('))
    text = once(text, WRAP_PORT, WRAP_PORT + f'    input wire ram_early,               // {TAG}: memorymux holds a RAM read in WAITFORRAMREAD\n')
    due_early, due_win = {'early-release': (3, 2), 'late-release': (5, 4)}.get(fault, (4, 3))
    face = f"""    // {TAG}: PS1 face for scalar RAM reads. The controller may finish early; the
    // read is released in the clk1x MiSTer's fixed-latency controller would release it.
    wire face_scalar = ram_rnw && !ram_dma && !ram_cache;
    reg face_sent=0, face_pend=0, face_got=0;
    reg [2:0] face_rel=0;
    wire face_early_now = FACE != 0 && ram_early && face_scalar && !face_sent && !face_pend;
    wire face_win_now = FACE != 0 && ram_ena && face_scalar && !face_sent;
    wire face_start = face_early_now || face_win_now;
    wire face_done = face_pend && face_rel == 3'd1 && (face_got || rd_ready);
    always @(posedge clk1x) begin
        if (face_start) begin face_pend <= 1; face_got <= 0; face_rel <= face_early_now ? 3'd{due_early} : 3'd{due_win}; end
        else begin
            if (face_rel > 3'd1) face_rel <= face_rel - 1'b1;
            if (face_pend && rd_ready) face_got <= 1;
            if (face_done) begin face_pend <= 0; face_got <= 0; face_rel <= 0; end
        end
        if (face_early_now) face_sent <= 1; else if (ram_ena && ram_rnw) face_sent <= 0;
        if (cpu_reset) begin face_sent <= 0; face_pend <= 0; face_got <= 0; face_rel <= 0; end
    end
    assign ram_done = wr_ready || (FACE != 0 && face_pend ? face_done : rd_ready);
"""
    if fault == 'trace':  # diagnostic: one line per kernel-address scalar read (Peip load kernels)
        face += """    reg [3:0] face_late=0; reg face_was_early=0; reg [31:0] face_n=0;
    wire face_kernel = ram_adr[20:0]==21'h004710 || ram_adr[20:0]==21'h000714 || ram_adr[20:0]==21'h140000 || ram_adr[20:0]==21'h180000;
    always @(posedge clk1x) begin
        if (face_start) begin face_late <= 0; face_was_early <= face_early_now; end
        else if (face_pend && face_rel == 3'd1 && !face_got && !rd_ready) face_late <= face_late + 1'b1;
        if (face_done && face_kernel) begin
            face_n <= face_n + 1;
            $display("FACE n=%0d t=%0t adr=%h early=%0d got_before=%0d late=%0d", face_n, $time, ram_adr[20:0], face_was_early, face_got, face_late);
        end
    end
"""
    if wface and len(wface) == 5:  # Psy-Q / psx-spx drain: continuous same-page Dh, else Dm; a refresh every P
        # clk1x runs when the RAM is free, delays the next write by what is left of it, and closes the page
        dh, dm, sp, sr, _ = wface
        face = face.replace('    assign ram_done = wr_ready || (FACE != 0 && face_pend ? face_done : rd_ready);\n', f"""    // {TAG}: PS1 write drain (Psy-Q table 2-5, psx-spx DRAM_CTRL)
    reg wf_pend=0, wf_got=0; reg [5:0] wf_rel=0; reg [12:0] wf_page=13'h1fff; reg [9:0] wf_t=0;
    reg wf_refp=0, wf_closed=1; reg [5:0] wf_refl=0; reg [3:0] wf_gap=4'hf;
    wire wf_start = ram_ena && !ram_rnw;
    wire wf_done = wf_pend && wf_rel <= 6'd1 && (wf_got || wr_ready);
    wire wf_cont = ram_adr[22:10] == wf_page && !wf_closed && !wf_refp && wf_refl == 0 && wf_gap <= 4'd1;
    always @(posedge clk1x) begin
        wf_t <= (wf_t == 10'd{max(sp,1) - 1}) ? 10'd0 : wf_t + 1'b1;
        if (wf_t == 10'd0 && {'1' if sr else '0'}) wf_refp <= 1;
        if (wf_refl != 0) wf_refl <= wf_refl - 1'b1;
        else if (wf_refp && !wf_pend && !wf_start) begin wf_refl <= 6'd{sr}; wf_refp <= 0; wf_closed <= 1; end
        if (!wf_done && wf_gap != 4'hf) wf_gap <= wf_gap + 1'b1;
        if (wf_start) begin wf_pend <= 1; wf_got <= 0; wf_page <= ram_adr[22:10]; wf_closed <= 0;
            wf_rel <= (wf_cont ? 6'd{dh} : 6'd{dm}) + wf_refl + (wf_refp ? 6'd{sr} : 6'd0);
            if (wf_refp) begin wf_refp <= 0; end end
        else begin
            if (wf_rel > 6'd1) wf_rel <= wf_rel - 1'b1;
            if (wf_pend && wr_ready) wf_got <= 1;
            if (wf_done) begin wf_pend <= 0; wf_got <= 0; wf_gap <= 0; end
        end
        if (cpu_reset) begin wf_pend <= 0; wf_got <= 0; end
    end
    assign ram_done = (wf_pend ? wf_done : wr_ready) || (FACE != 0 && face_pend ? face_done : rd_ready);
""")
    elif wface:
        dh, dm, sp, sr = wface
        face = face.replace('    assign ram_done = wr_ready || (FACE != 0 && face_pend ? face_done : rd_ready);\n', f"""    // {TAG}: write face
    reg wf_pend=0, wf_got=0; reg [5:0] wf_rel=0; reg [12:0] wf_page=13'h1fff; reg [9:0] wf_t=0;
    wire wf_start = ram_ena && !ram_rnw;
    // a steal window that opens while the write holds RAM lengthens it by R (time-based steal)
    wire wf_steal_open = {'1' if sr else '0'} && wf_t == 10'd0;
    wire wf_done = wf_pend && wf_rel <= 6'd1 && (wf_got || wr_ready);
    always @(posedge clk1x) begin
        wf_t <= (wf_t == 10'd{max(sp,1) - 1}) ? 10'd0 : wf_t + 1'b1;
        if (wf_start) begin wf_pend <= 1; wf_got <= 0; wf_page <= ram_adr[22:10];
            wf_rel <= (ram_adr[22:10] == wf_page) ? 6'd{dh} : 6'd{dm}; end
        else begin
            if (wf_pend && wf_steal_open) wf_rel <= wf_rel + 6'd{sr} - (wf_rel > 6'd1 ? 6'd1 : 6'd0);
            else if (wf_rel > 6'd1) wf_rel <= wf_rel - 1'b1;
            if (wf_pend && wr_ready) wf_got <= 1;
            if (wf_done) begin wf_pend <= 0; wf_got <= 0; end
        end
        if (cpu_reset) begin wf_pend <= 0; wf_got <= 0; end
    end
    assign ram_done = (wf_pend ? wf_done : wr_ready) || (FACE != 0 && face_pend ? face_done : rd_ready);
""")
    if fault == 'dmatrace':
        face += """    always @(posedge clk1x) begin
        if (ram_ena && ram_dma) $display("WD t=%0t REQ %h cnt=%0d", $time, ram_adr[20:0], ram_cntDMA);
        if (dma_reqprocessed) $display("WD t=%0t RP", $time);
        if (dma_wr) $display("WD t=%0t WR %h", $time, dma_data);
    end
"""
    text = once(text, WRAP_DONE, face)
    text = once(text, WRAP_REQ, '.ch1_req((ram_ena && ram_rnw && !(FACE != 0 && face_sent)) || face_early_now),')
    return text


BENCH_HINT = """wire [31:0] cpu_hint_address=core.component_core.imemorymux.mem_in_addressdata;
wire cpu_hint_request=read_hint_active && !reset &&
 core.component_core.imemorymux.mem_in_request && core.component_core.imemorymux.mem_in_rnw &&
 core.component_core.imemorymux.mem_in_isdata &&
 cpu_hint_address[28:0]<29'h0800000 && !core.component_core.dmaon;
"""
BENCH_PORT_OLD = ".ram_hint_req(cpu_hint_request),.ram_hint_addr({4'b0,cpu_hint_address[20:0]}),"


def bench_text(text, cpu_hint='decode', dma_hint=True, face=True, fault=None):
    """cpu_hint: 'decode' (stage-3 operands, this design) or 'k4' (K4's clk1x-registered request)."""
    if TAG in text:
        raise ValueError('already carries ' + TAG)
    text = once(text, BENCH_HINT, BENCH_HINT + f"""// {TAG}: hint sources
wire [31:0] dh_v1=core.component_core.icpu.decodevalue1;
wire [15:0] dh_imm=core.component_core.icpu.decodeimmdata;
wire [4:0] dh_s=core.component_core.icpu.decodesource1;
wire [5:0] dh_op=core.component_core.icpu.decodeop;
wire [31:0] dh_addr=dh_v1+{{{{16{{dh_imm[15]}}}},dh_imm}};
wire dh_fwd=dh_s!=5'd0 && (
 (core.component_core.icpu.resulttarget==dh_s && core.component_core.icpu.resultwriteenable) ||
 (core.component_core.icpu.latereadtarget==dh_s && core.component_core.icpu.latereadbypass) ||
 (core.component_core.icpu.writebacktarget==dh_s && core.component_core.icpu.writebackwriteenable) ||
 (core.component_core.icpu.writedonetarget==dh_s && core.component_core.icpu.writedonewriteenable));
wire dma_side=core.component_core.dmaon || core.component_core.idma.dmarequest;
// a load issued from stage 3 waits in the stage-4 request register (mem4_*) while the
// previous access is served: that register leads its request by the whole service time
wire [31:0] m4_addr=core.component_core.icpu.mem4_address;
wire m4_inflight=(core.component_core.imemorymux.readram || core.component_core.imemorymux.state==4'd1) &&
 m4_addr[22:2]==core.component_core.imemorymux.ram_adr[22:2];
wire m4_hint=core.component_core.icpu.mem4_request && core.component_core.icpu.mem4_rnw && !m4_inflight &&
 m4_addr[28:0]<29'h0800000;
wire dh_hint={'1\'b0' if cpu_hint == 'm4' else "dh_op>=6'h20 && dh_op<=6'h26 && !dh_fwd && dh_addr[28:0]<29'h0800000"};
wire dec_hint_req=read_hint_active && !reset && !dma_side && (m4_hint || dh_hint);
wire [20:0] dec_hint_adr=m4_hint ? m4_addr[20:0] : dh_addr[20:0];
wire dma_hint_first=core.component_core.idma.dmarequest && core.component_core.idma.dmastate==3'd0;
wire dma_hint_req=read_hint_active && !reset && (dma_hint_first || (core.component_core.idma.dmastate!=3'd0 && core.component_core.idma.todevice));
wire [20:0] dma_hint_adr=dma_hint_first ? core.component_core.idma.trig_madr[20:0] : core.component_core.idma.ram_adr[20:0]+21'd16;
// release form (prepare_ram_face_release.py): psx_top registers the hint on clk1x; the CPU side is
// the pending stage-4 read (mem_isData and mem_rnw), the DMA side its own next read address
wire rel_cpu_req=core.component_core.icpu.mem4_request && core.component_core.icpu.mem4_rnw && m4_addr[28:0]<29'h0800000;
wire rel_dma_ena=(core.component_core.idma.dmastate==3'd0 && core.component_core.idma.dmarequest) || (core.component_core.idma.dmastate!=3'd0 && core.component_core.idma.todevice);
wire [20:0] rel_dma_adr=core.component_core.idma.dmastate==3'd0 ? {{core.component_core.idma.trig_madr[20:2],2'b00}} : core.component_core.idma.ram_adr[20:0];
reg rel_req_q=0;reg [20:0] rel_adr_q=0;
always @(posedge clk1x) begin rel_req_q<=read_hint_active && !reset && (dma_side ? rel_dma_ena : rel_cpu_req); rel_adr_q<=dma_side ? rel_dma_adr : {{m4_addr[20:2],2'b00}}; end
// release form 2: CPU side straight from the stage-4 registers (no extra clk1x), DMA side registered
reg rel2_dma_q=0, rel2_side_q=0; reg [20:0] rel2_dma_adr_q=0;
always @(posedge clk1x) begin rel2_side_q<=dma_side; rel2_dma_q<=read_hint_active && !reset && REL2_DMA_ENA; rel2_dma_adr_q<=rel_dma_adr; end
wire rel2_req=REL_DMA_UNREG ? (dma_side ? (read_hint_active && !reset && rel_dma_ena) : (read_hint_active && !reset && REL_CPU_REQ)) : rel2_side_q ? rel2_dma_q : (!dma_side && read_hint_active && !reset && REL_CPU_REQ);
wire [20:0] rel2_adr=REL_DMA_UNREG ? (dma_side ? rel_dma_adr : {{m4_addr[20:2],2'b00}}) : rel2_side_q ? rel2_dma_adr_q : {{m4_addr[20:2],2'b00}};
// release form 7: the DMA's first read address from registers (the last MADR written on the bus, armed
// by a CHCR start write that reads RAM); a working channel's ram_Adr direct; the CPU's pending load first
reg [20:0] dmah_madr=0; reg dmah_armed=0;
always @(posedge clk1x) begin
  if (core.component_core.idma.bus_write && core.component_core.idma.bus_addr[6:4]!=3'd7 && core.component_core.idma.bus_addr[3:2]==2'd0) dmah_madr<={{core.component_core.idma.bus_datawrite[20:2],2'b00}};
  if (core.component_core.idma.bus_write && core.component_core.idma.bus_addr[6:4]!=3'd7 && core.component_core.idma.bus_addr[3:2]==2'd2 && core.component_core.idma.bus_datawrite[24] && core.component_core.idma.bus_datawrite[0]) begin dmah_armed<=1; dmah_run<=0; dmah_idle<=0; end
  else if (REL8 && dmah_armed) begin
    if (core.component_core.idma.dmastate!=3'd0 || dma_side) begin dmah_run<=1; dmah_idle<=0; end
    else if (dmah_idle==7'd63) dmah_armed<=0; else dmah_idle<=dmah_idle+1'b1;
  end
  else if (!REL8 && core.component_core.idma.dmastate!=3'd0) dmah_armed<=0;
end
reg dmah_run=0; reg [6:0] dmah_idle=0;
wire rel7_cpu=core.component_core.icpu.executereadenable && core.component_core.icpu.executereadaddress[28:0]<29'h0800000;
wire rel7_work=core.component_core.idma.dmastate!=3'd0 && core.component_core.idma.todevice;
wire rel7_req=read_hint_active && !reset && (dma_side ? (core.component_core.idma.dmastate==3'd0 ? 1'b1 : rel7_work) : (rel7_cpu || dmah_armed));
wire [20:0] rel7_adr=dma_side ? (core.component_core.idma.dmastate==3'd0 && dmah_armed && !(REL8 && dmah_run) ? dmah_madr : core.component_core.idma.ram_adr[20:0]) : rel7_cpu ? {{core.component_core.icpu.executereadaddress[20:2],2'b00}} : (REL8 && dmah_run) ? core.component_core.idma.ram_adr[20:0] : dmah_madr;
reg k4_req_q=0;reg [20:0] k4_adr_q=0;
always @(posedge clk1x) begin k4_req_q<=cpu_hint_request;k4_adr_q<=cpu_hint_address[20:0]; end
wire face_cpu_req={'k4_req_q' if cpu_hint == 'k4' else 'dec_hint_req'};
wire [20:0] face_cpu_adr={'k4_adr_q' if cpu_hint == 'k4' else 'dec_hint_adr'};
wire face_hint_req={'rel7_req' if cpu_hint in ('rel7', 'rel8') else 'rel2_req' if cpu_hint.startswith('rel2') or cpu_hint in ('rel3', 'rel4', 'rel5') else 'rel_req_q' if cpu_hint == 'rel' else ('dma_side ? dma_hint_req : face_cpu_req' if dma_hint else 'face_cpu_req')};
wire [20:0] face_hint_adr0={'rel7_adr' if cpu_hint in ('rel7', 'rel8') else 'rel2_adr' if cpu_hint.startswith('rel2') or cpu_hint in ('rel3', 'rel4', 'rel5') else 'rel_adr_q' if cpu_hint == 'rel' else ('dma_side ? dma_hint_adr : face_cpu_adr' if dma_hint else 'face_cpu_adr')};
wire [20:0] face_hint_adr={'face_hint_adr0 ^ 21\'h000800' if fault == 'wrong-hint' else 'face_hint_adr0'};
wire face_ram_early=core.component_core.imemorymux.state==4'd1;
""" + ('''always @(posedge clk1x) if ((dh_op>=6'h20 && dh_op<=6'h26 || m4_hint || rel_req_q) && (dec_hint_adr==21'h4710||dec_hint_adr==21'h714||dec_hint_adr==21'h140000||dec_hint_adr==21'h180000||rel_adr_q==21'h4710||rel_adr_q==21'h714||dh_addr[20:0]==21'h4710||dh_addr[20:0]==21'h714||dh_addr[20:0]==21'h140000||dh_addr[20:0]==21'h180000)) $display("BH t=%0t dec %h req=%0d fwd=%0d m4=%0d m4a=%h m4req=%0d infl=%0d relq=%0d rela=%h", $time, dh_addr[20:0], dec_hint_req, dh_fwd, m4_hint, m4_addr[20:0], core.component_core.icpu.mem4_request, m4_inflight, rel_req_q, rel_adr_q);
''' if fault == 'trace' else ''))
    text = text.replace('REL8', "1'b1" if cpu_hint == 'rel8' else "1'b0")
    if cpu_hint == 'rel8':
        cpu_hint = 'rel7'
    text = text.replace('REL_DMA_UNREG', "1'b1" if cpu_hint == 'rel5' else "1'b0")
    if cpu_hint == 'rel5':
        cpu_hint_eff = 'rel4'
    else:
        cpu_hint_eff = cpu_hint
    text = text.replace('REL_CPU_REQ', "m4_addr[28:0]<29'h0800000" if cpu_hint == 'rel3' else
                        "core.component_core.icpu.executereadenable && core.component_core.icpu.executereadaddress[28:0]<29'h0800000" if cpu_hint_eff == 'rel4' else 'rel_cpu_req')
    if cpu_hint_eff == 'rel4':
        line = [l for l in text.splitlines() if l.startswith('wire [20:0] rel2_adr=')][0]
        text = once(text, line, line.replace("{m4_addr[20:2],2'b00}", "{core.component_core.icpu.executereadaddress[20:2],2'b00}"))
    text = text.replace('REL2_DMA_ENA', '(core.component_core.idma.dmastate!=3\'d0 && core.component_core.idma.todevice)' if cpu_hint == 'rel2w' else 'rel_dma_ena')
    text = once(text, BENCH_PORT_OLD,
                ".ram_hint_req(face_hint_req),.ram_hint_addr({4'b0,face_hint_adr}),.ram_early(face_ram_early),")
    text = once(text, 'psx_native_memory #(.READ_HINT(1), .DMA_MISS_RETURN(1), .EARLY_REFRESH(64)) native (',
                'psx_native_memory #(.READ_HINT(1), .DMA_MISS_RETURN(1), .EARLY_REFRESH(64), .FACE(%d)) native (' % int(face))
    return text


def wbuf_text(mm, F=4, C=11, Kr=0, X=1, W=0, P=256, refprio=1, FE=1, dbg=0, L=99, Wc=2, Wr=2, RP=1, Q=2, RF=1, KE=0):
    """PS1 write-buffer timing model inside memorymux_Barch (rig only). Offline twin:
    docs/measurements/memctrl-redesign/wbmodel.py.

    Psy-Q Hardware table 2-5: 4 one-word entries; a store enters in 1 cycle and stalls the CPU
    only when 4 are pending, until the oldest is written. Buffer to RAM: a burst's first word F
    (4), each further word 2 while the next entry is already buffered, on the same 1 KiB page
    (bits 22..10, DRAM_CTRL's 8 MB decode) and the burst is shorter than L. A new burst waits Wc
    cycles after the last access on a page change (precharge), Wr after a refresh, W on the same
    page. psx-spx DRAM_CTRL 0B88h: a refresh every P cycles, cost C, run when the RAM is free
    (RP: not while a burst can continue; refprio: before a waiting write), closing the page.
    Reads wait until the model is empty plus Kr, or (KE) until only the last write is left and
    it has at most KE cycles to go. CPU: resumes X cycles after a slot frees,
    rounded to a multiple of Q cycles; FE: a slot freeing at this edge is free for a store.
    The real FIFO/SDRAM path keeps running; its own full flag is ORed in when RF (RF=0: the
    model alone stalls the CPU; real-FIFO overflows are counted and must stay 0). D0..D1: trace
    stores numbered D0 to D1 (and the drain around them). Plusargs +wb_<name>=
    override every constant at run time (one build per sweep).
    """
    old_full = 'assign mem_fifofull = writefifo_nearfull; //(module output)'
    names = dict(F=F, C=C, Kr=Kr, X=X, W=W, P=P, RPR=refprio, FE=FE, L=L, Wc=Wc, Wr=Wr, RP=RP, Q=Q, RF=RF, KE=KE, D0=0, D1=0)
    decl = ', '.join(f'wbp_{k} = {v}' for k, v in names.items())
    plus = '\n'.join(f'    if ($value$plusargs("wb_{k}=%d", wbp_{k})) ;' for k in names)
    show = ' '.join(f'{k}=%0d' for k in names)
    showv = ', '.join(f'wbp_{k}' for k in names)
    model = f"""// ps1wb: PS1 write-buffer timing model
  integer {decl};
  initial begin
{plus}
    $display("PS1WB {show}", {showv});
  end
  wire wb_store = ce && mem_in_request && !mem_in_rnw && mem_in_addressdata[28:0] < 29'h0800000;
  reg [12:0] wb_q [0:7];
  reg [2:0] wb_head = 3'd0, wb_tail = 3'd0;
  wire [2:0] wb_cnt = wb_tail - wb_head;
  reg wb_busy = 1'b0, wb_isref = 1'b0, wb_open = 1'b0, wb_refp = 1'b0, wb_afterref = 1'b1;
  reg [5:0] wb_left = 6'd0;
  reg [7:0] wb_burst = 8'd0;
  reg [12:0] wb_page = 13'd0;
  reg [9:0] wb_t = 10'd0;
  reg [3:0] wb_gap = 4'd15;    // cycles since the last access ended (saturating)
  reg [3:0] wb_since = 4'd15;
  reg [2:0] wb_hold = 3'd0;    // CPU resume delay after a slot frees
  reg [31:0] wb_realfull = 32'd0, wb_nstore = 32'd0, wb_nref = 32'd0, wb_ovf = 32'd0, wb_cyc = 32'd0;
  wire wb_rfull;
  wire wb_tr = wb_nstore >= wbp_D0 && wb_nstore <= wbp_D1 && wbp_D1 != 0;
  wire wb_fin = wb_busy && wb_left == 6'd1;
  wire wb_fin_w = wb_fin && !wb_isref;
  wire [2:0] wb_left_cnt = wb_cnt - {{2'b0, wbp_FE != 0 && wb_fin_w}};
  wire wb_full_now = ({{1'b0, wb_left_cnt}} + {{3'b0, wb_store}}) >= 4'd5;
  wire wb_full = wb_full_now || wb_hold != 3'd0;
  wire wb_rd_early = wb_cnt == 3'd1 && wb_busy && !wb_isref && {{26'd0, wb_left}} <= wbp_KE;
  wire wb_rdblock = (wb_cnt != 3'd0 && !wb_rd_early) || {{28'd0, wb_since}} < wbp_Kr;
  always @(posedge clk1x) begin : ps1wb
    reg free, cont, has_next, need_ok, refgo;
    reg [12:0] nxt;
    reg [5:0] cost;
    reg [3:0] gap, need;
    reg [2:0] rem;
    if (ce) begin
      free = !wb_busy || wb_fin;
      rem = wb_cnt - {{2'b0, wb_fin_w}};
      // the next write: the oldest buffered entry, or the store entering at this edge
      has_next = rem != 3'd0 || wb_store;
      nxt = rem != 3'd0 ? wb_q[wb_head + {{2'b0, wb_fin_w}}] : mem_in_addressdata[22:10];
      gap = wb_fin ? 4'd0 : wb_gap;
      cont = wb_fin_w && has_next && wb_open && nxt == wb_page && {{24'd0, wb_burst}} < wbp_L;
      need = wb_afterref || wb_fin && wb_isref ? wbp_Wr[3:0] : (nxt != wb_page || !wb_open) ? wbp_Wc[3:0] : wbp_W[3:0];
      refgo = free && wb_refp && wbp_C != 0 && !(wbp_RP != 0 && cont) && (wbp_RPR != 0 || !has_next);
      wb_t <= {{22'd0, wb_t}} >= wbp_P - 1 ? 10'd0 : wb_t + 10'd1;
      if (wb_t == 10'd0) wb_refp <= 1'b1;
      if (wb_busy) wb_left <= wb_left - 6'd1;
      if (wb_fin) begin wb_busy <= 1'b0; wb_gap <= 4'd1; end
      else if (!wb_busy && wb_gap != 4'd15) wb_gap <= wb_gap + 4'd1;
      if (wb_store) begin wb_q[wb_tail] <= mem_in_addressdata[22:10]; wb_nstore <= wb_nstore + 32'd1; end
      wb_tail <= wb_tail + {{2'b0, wb_store}};
      if (wb_fin_w) wb_head <= wb_head + 3'd1;
      wb_cyc <= wb_cyc + 32'd1;
      if (wb_tr && wb_store) $display("WB c=%0d store pg=%h cnt=%0d full=%0d realnf=%0d", wb_cyc, mem_in_addressdata[22:10], wb_cnt, wb_full_now, writefifo_nearfull);
      if (wb_tr && refgo) $display("WB c=%0d refresh", wb_cyc);
      if (wb_rfull && writefifo_wr) wb_ovf <= wb_ovf + 32'd1;
      if (refgo) begin
        wb_busy <= 1'b1; wb_isref <= 1'b1; wb_left <= wbp_C[5:0]; wb_refp <= 1'b0; wb_open <= 1'b0;
        wb_afterref <= 1'b1; wb_nref <= wb_nref + 32'd1;
      end else if (free && has_next && (cont || gap >= need)) begin
        cost = cont ? 6'd2 : wbp_F[5:0];
        wb_busy <= 1'b1; wb_isref <= 1'b0; wb_left <= cost; wb_open <= 1'b1; wb_afterref <= 1'b0;
        wb_page <= nxt; wb_burst <= cont ? wb_burst + 8'd1 : 8'd1;
        if (wb_tr) $display("WB c=%0d start pg=%h cont=%0d cost=%0d cnt=%0d gap=%0d", wb_cyc, nxt, cont, cost, wb_cnt, gap);
      end else if (wbp_C == 0) wb_refp <= 1'b0;
      if (wb_cnt != 3'd0 || wb_busy && !wb_isref) wb_since <= 4'd0;
      else if (wb_since != 4'd15) wb_since <= wb_since + 4'd1;
      // CPU resume: X cycles after the slot frees, then to the next multiple of Q (global timer)
      if (wb_full_now) wb_hold <= wbp_X[2:0] + ((wbp_Q == 2 && wb_t[0] == (wbp_X[0] ? 1'b1 : 1'b0)) ? 3'd1 : 3'd0);
      else if (wb_hold != 3'd0) wb_hold <= wb_hold - 3'd1;
      if (writefifo_nearfull && !wb_full) wb_realfull <= wb_realfull + 32'd1;
    end
    if (reset) begin wb_head <= 3'd0; wb_tail <= 3'd0; wb_busy <= 1'b0; wb_refp <= 1'b0; wb_open <= 1'b0; wb_hold <= 3'd0; end
  end
  final $display("PS1WB stores=%0d refreshes=%0d realfifo_limited_cycles=%0d realfifo_overflows=%0d", wb_nstore, wb_nref, wb_realfull, wb_ovf);
  assign mem_fifofull = (wbp_RF != 0 && writefifo_nearfull) | wb_full; // ps1wb"""
    mm = once(mm, old_full, model)
    mm = once(mm, '    .full(),\n    .nearfull(writefifo_nearfull),', '    .full(wb_rfull),\n    .nearfull(writefifo_nearfull),')
    for n in ('n458', 'n463'):
        mm = once(mm, f'assign {n} = ~writefifo_busy;', f'assign {n} = ~writefifo_busy & ~(wb_rdblock & n347); // ps1wb')
    return mm
