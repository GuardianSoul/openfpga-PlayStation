"""Core-top edit for prepare_pocket_early --disc-swap.

The --cd-stream top ties psx_cd_stream's disc_select_strobe low and the CD
lid closed. A runtime disc reload (the user loads the next disc's game file;
APF sends 0x8A for the disc slot) then only marks the file pending: the
loader stops serving the old disc (0xAA fill) and waits for a select strobe
that never comes. This wires the tested sequencer exactly as
sim/pocket/tb_cd_swap_apf.sv does (make test-cd-swap-apf): disc_pending
starts psx_disc_swap's automatic swap (lid open 1 s, change, 0.5 s, close),
psx_disc_select strobes the loader with the disc slot, and the lid drives
cd_top's LIDopen. Everything runs on clk1x with the loader's transport reset.
"""
import re

SWAP = '''    // Disc swap (psx_disc_swap): a reloaded disc file opens the lid, the
    // loader remounts the slot, then the lid closes (make test-cd-swap-apf).
    wire swap_change_req, swap_change_done, swap_lid_open, swap_busy, swap_select_strobe, cd_guest_idle;
    wire [15:0] swap_slot_id;
    psx_disc_swap disc_swap (.clk(clk1x), .reset(cd_stream_reset), .manual_lid_open(1'b0), .swap_request(1'b0),
        .disc_pending(cd_disc_pending), .quiet(cd_guest_idle), .change_req(swap_change_req), .change_done(swap_change_done),
        .lid_open(swap_lid_open), .busy(swap_busy));
    psx_disc_select #(.DISC_SLOT(SLOT_VALUE)) disc_select (.clk(clk1x), .reset(cd_stream_reset),
        .change_req(swap_change_req), .change_done(swap_change_done), .disc_slot_id(swap_slot_id),
        .disc_select_strobe(swap_select_strobe), .disc_change_done(cd_disc_change_done));
    assign LIDopen=swap_lid_open;
'''


def disc_swap_text(text):
    if text.count("    assign LIDopen=1'b0;\n") != 1: raise ValueError('closed-lid assignment not found')
    found = re.findall(r"\.disc_slot_id\((16'[dh]\w+)\), \.disc_select_strobe\(1'b0\),", text)
    if len(found) != 1: raise ValueError('loader disc slot / strobe tie-off not found')
    slot = found[0]
    text = text.replace(f".disc_slot_id({slot}), .disc_select_strobe(1'b0),",
                        f".disc_slot_id(swap_slot_id), .disc_select_strobe(swap_select_strobe),")
    text = text.replace("    assign LIDopen=1'b0;\n", '')
    if text.count('.has_disc(hasCD), ') != 1: raise ValueError('loader has_disc connection not found')
    text = text.replace('.has_disc(hasCD), ', '.has_disc(hasCD), .guest_idle(cd_guest_idle), ')
    inst = re.search(r'^    psx_cd_stream #\(', text, re.M)
    if not inst or text.count('    psx_cd_stream #(') != 1: raise ValueError('psx_cd_stream instance not found')
    text = text[:inst.start()] + SWAP.replace('SLOT_VALUE', slot) + text[inst.start():]
    return text


MENU_SELECT = '''    psx_disc_select #(.DISC_SLOT(SLOT_VALUE)) disc_select (.clk(clk1x), .reset(cd_stream_reset),
        .change_req(swap_change_req), .change_done(swap_change_done), .disc_slot_id(swap_slot_id),
        .disc_select_strobe(swap_select_strobe), .disc_change_done(cd_disc_change_done));
'''

MENU = '''    // "Swap Discs" menu setting (interact.json list, bridge 0x50000044, remembered
    // by the Pocket): psx_disc_menu swaps to that disc's slots in the same
    // game file (--disc-menu). The value is taken from whichever byte holds
    // it: checkbox values were seen outside bit 0 on hardware.
    reg [1:0] disc_setting=2'd0;
    always @(posedge clk_74a) if (bridge_wr && bridge_addr==32'h50000044)
        disc_setting<=bridge_wr_data[1:0]|bridge_wr_data[9:8]|bridge_wr_data[17:16]|bridge_wr_data[25:24];
    wire disc_menu_request;
    psx_disc_menu #(.FIRST_SLOT(SLOT_VALUE), .STEP(16'd100), .DISCS(DISC_COUNT)) disc_menu (.clk(clk1x), .reset(cd_stream_reset),
        .disc_setting(disc_setting), .change_req(swap_change_req), .change_done(swap_change_done),
        .disc_slot_id(swap_slot_id), .disc_select_strobe(swap_select_strobe),
        .disc_change_done(cd_disc_change_done), .discs_present(cd_discs_present), .swap_request(disc_menu_request));
'''

MENU_SDC = 'set_false_path -to [get_registers {*|disc_menu|disc_meta[*]}]\n'


def disc_menu_text(text, discs):
    """Replace psx_disc_select (after disc_swap_text) with the Disc menu setting."""
    found = re.findall(r"psx_disc_select #\(\.DISC_SLOT\((16'[dh]\w+)\)\) disc_select", text)
    if len(found) != 1: raise ValueError('psx_disc_select instance not found (apply --disc-swap first)')
    slot = found[0]
    old = MENU_SELECT.replace('SLOT_VALUE', slot)
    if text.count(old) != 1: raise ValueError('psx_disc_select wiring changed')
    text = text.replace(old, MENU.replace('SLOT_VALUE', slot).replace('DISC_COUNT', str(discs)))
    request = ".manual_lid_open(1'b0), .swap_request(1'b0),"
    if text.count(request) != 1: raise ValueError('psx_disc_swap request tie-off not found')
    text = text.replace(request, ".manual_lid_open(1'b0), .swap_request(disc_menu_request),")
    # The loader reports which discs the game file lists.
    if text.count('.has_disc(hasCD), ') != 1: raise ValueError('loader has_disc connection not found')
    text = text.replace('.has_disc(hasCD), ', '.has_disc(hasCD), .discs_present(cd_discs_present), ')
    found = re.findall(r"psx_cd_stream #\(\.DEFAULT_SLOT\(16'd101\), \.MAX_TRACKS\(\d+\), \.TRACK_STRIDE\(16'd1\)", text)
    if len(found) != 1: raise ValueError('multi-track psx_cd_stream parameters not found')
    text = text.replace(found[0], found[0] + ", .DISC_STEP(16'd100)")
    text = text.replace('    wire [15:0] swap_slot_id;\n', '    wire [15:0] swap_slot_id;\n    wire [3:0] cd_discs_present;\n', 1)
    # psx_disc_menu is declared after psx_disc_swap uses disc_menu_request.
    text = text.replace('    wire [15:0] swap_slot_id;\n', '    wire [15:0] swap_slot_id;\n    wire disc_menu_request;\n', 1)
    text = text.replace('    wire disc_menu_request;\n    psx_disc_menu', '    psx_disc_menu', 1)
    return text


BOOT_WAIT = '''    // --boot-waits-disc: the CPU leaves reset only once the loader's first
    // mount has reached cd_top (disc or no disc), or 14.5 s after Reset Exit
    // without one. On a slow first launch APF can finish loading after Reset
    // Exit; a BIOS that checks the drive before the mount boots to its menu.
    // The mount always reports (a cue that fails reports no disc), so the
    // timeout is only a safety net: 1.8 s was shorter than a cold first
    // launch's cue mount (Castlevania, 2026-10-17).
    (* async_reg="true", preserve, dont_merge *) reg [1:0] disc_settled_sync=2'b00;
    always @(posedge clk_74a) disc_settled_sync<={disc_settled_sync[0],cd_disc_settled};
    reg [29:0] disc_wait=0; reg disc_wait_done=0;
    always @(posedge clk_74a)
        if (!power_reset_n) begin disc_wait<=0; disc_wait_done<=0; end
        else if (reset_n && !disc_wait_done) begin disc_wait<=disc_wait+1'b1; if (&disc_wait) disc_wait_done<=1'b1; end
    // Registered: it feeds psx_boot_gate's asynchronous reset like bios_ready.
    (* preserve, dont_merge *) reg boot_disc_gate=1'b0;
    always @(posedge clk_74a) boot_disc_gate<=READY_SIGNAL && (disc_settled_sync[1] || disc_wait_done);
'''

BOOT_WAIT_SDC = ('set_false_path -to [get_registers {*|disc_settled_sync[0]}]\n'
                 'set_false_path -from [get_registers {*|boot_disc_gate}] -to [get_registers {*|*boot_gate*|reset_pipe[*]}]\n')


def boot_wait_text(text):
    """Gate psx_boot_gate's bios_ready on the first disc mount."""
    found = re.findall(r'(    psx_boot_gate boot_gate \([^;]*?\.bios_ready\()(\w+)\)', text, re.S)
    if len(found) != 1: raise ValueError('psx_boot_gate bios_ready connection not found')
    head, ready = found[0]
    if text.count('.has_disc(hasCD), ') != 1: raise ValueError('loader has_disc connection not found')
    text = text.replace('.has_disc(hasCD), ', '.has_disc(hasCD), .disc_settled(cd_disc_settled), ')
    text = text.replace(head + ready + ')', head + 'boot_disc_gate)')
    text = text.replace('    psx_boot_gate boot_gate (', BOOT_WAIT.replace('READY_SIGNAL', ready) + '    psx_boot_gate boot_gate (', 1)
    text = text.replace('    wire cd_stream_reset=', '    wire cd_disc_settled;\n    wire cd_stream_reset=', 1)
    return text
