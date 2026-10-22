"""Edits for prepare_pocket_early --analogizer: the Analogizer adapter.

rtl/pocket/psx_analogizer.sv drives the cartridge port: 15 kHz RGBS / RGsB /
YPbPr from the PSX GPU's own video timing, and the SNAC lines of a real
PlayStation controller. rtl/pocket/psx_snac.sv is MiSTer's SNAC block (PSX.sv)
on the core's own SIO (joypad.vhd's SNAC ports). docs/analogizer-plan.md.

Core Settings (interact.json, package --analogizer):
  "Analogizer Video" 0x50000078: Off (default), RGBS, RGsB, YPbPr
  "Analogizer SNAC"  0x5000007C: Off (default), Port 1, Port 2
With both off every cartridge pin is an input, as without the option.
"""

WRAPPER_PORTS = ('    beginTransferSnac : out std_logic;\n',
                 '    beginTransferSnac : out std_logic;\n'
                 '    snac_port1 : in std_logic;\n'
                 '    snac_port2 : in std_logic;\n'
                 '    irq10Snac_in : in std_logic;\n'
                 '    actionNextSnac_in : in std_logic;\n'
                 '    receiveValidSnac_in : in std_logic;\n'
                 '    ackSnac_in : in std_logic;\n'
                 '    receiveBufferSnac_in : in std_logic_vector(7 downto 0);\n')
WRAPPER_TIES = (("        snacPort1 => '0',\n", "        snacPort1 => snac_port1,\n"),
                ("        snacPort2 => '0',\n", "        snacPort2 => snac_port2,\n"),
                ("        irq10Snac => '0',\n", "        irq10Snac => irq10Snac_in,\n"),
                ("        actionNextSnac => '0',\n", "        actionNextSnac => actionNextSnac_in,\n"),
                ("        receiveValidSnac => '0',\n", "        receiveValidSnac => receiveValidSnac_in,\n"),
                ("        ackSnac => '0',\n", "        ackSnac => ackSnac_in,\n"),
                ("        receiveBufferSnac => (others=>'0'),\n", "        receiveBufferSnac => receiveBufferSnac_in,\n"))


def wrapper_text(text):
    for old, new in (WRAPPER_PORTS,) + WRAPPER_TIES:
        if text.count(old) != 1: raise ValueError('PSX wrapper anchor not found: ' + old.strip())
        text = text.replace(old, new)
    return text


TIES = ("    assign cart_tran_bank1=8'bz, cart_tran_bank2=8'bz, cart_tran_bank3=8'bz;\n"
        "    assign cart_tran_bank1_dir=0, cart_tran_bank2_dir=0, cart_tran_bank3_dir=0;\n"
        "    assign cart_tran_bank0=4'hf, cart_tran_bank0_dir=1;\n"
        "    assign cart_tran_pin30=1'bz, cart_tran_pin30_dir=0, cart_pin30_pwroff_reset=0;\n"
        "    assign cart_tran_pin31=1'bz, cart_tran_pin31_dir=0;\n")
BLOCK = '''    // --analogizer: Core Settings "Analogizer Video" (0x50000078: Off, RGBS,
    // RGsB, YPbPr) and "Analogizer SNAC" (0x5000007C: Off, Port 1, Port 2);
    // list values in any byte lane, as the other lists. Quasi-static levels,
    // two flops into the clock that uses them.
    reg [1:0] az_video_set=2'd0, az_snac_set=2'd0;
    always @(posedge clk_74a) begin
        if (bridge_wr && bridge_addr==32'h50000078) az_video_set<=bridge_wr_data[1:0]|bridge_wr_data[9:8]|bridge_wr_data[17:16]|bridge_wr_data[25:24];
        if (bridge_wr && bridge_addr==32'h5000007C) az_snac_set<=bridge_wr_data[1:0]|bridge_wr_data[9:8]|bridge_wr_data[17:16]|bridge_wr_data[25:24];
    end
    (* async_reg="true" *) reg [3:0] az_vs_0=0, az_vs_1=0;   // video clock: {snac, video}
    always @(posedge video_rgb_clock) begin az_vs_0<={az_snac_set,az_video_set}; az_vs_1<=az_vs_0; end
    (* async_reg="true" *) reg [1:0] az_cs_0=0, az_cs_1=0;   // clk1x: SNAC port
    always @(posedge clk1x) begin az_cs_0<=az_snac_set; az_cs_1<=az_cs_0; end
    wire az_snac_on=az_vs_1[3:2]!=2'd0;
    wire az_on=az_vs_1[1:0]!=2'd0 || az_snac_on;
    wire [1:0] az_mode=az_vs_1[1:0]==2'd0 ? 2'd0 : az_vs_1[1:0]-2'd1;
    wire snac_port1=az_cs_1==2'd1, snac_port2=az_cs_1==2'd2;
    wire [6:0] snac_out;
    wire snac_dat, snac_ack, snac_irq;
    wire irq10Snac, actionNextSnac, receiveValidSnac, ackSnac;
    wire [7:0] receiveBufferSnac;
    psx_snac snac (.clk_1x(clk1x),.snacPort1(snac_port1),.snacPort2(snac_port2),
        .ack_in(snac_ack),.dat_in(snac_dat),.irq_in(snac_irq),.hs(hsync),.vs(vsync),.user_out(snac_out),
        .selectedPort1Snac(selectedPort1Snac),.selectedPort2Snac(selectedPort2Snac),.transmitValueSnac(transmitValueSnac),
        .clk9Snac(clk9Snac),.beginTransferSnac(beginTransferSnac),.irq10Snac(irq10Snac),.receiveBufferSnac(receiveBufferSnac),
        .receiveValidSnac(receiveValidSnac),.actionNextSnac(actionNextSnac),.ackSnac(ackSnac));
    psx_analogizer analogizer (.clk(video_rgb_clock),.clk_dac(video_rgb_clock_90),.enable(az_on),.mode(az_mode),
        .ce(video_ce),.r(video_r),.g(video_g),.b(video_b),.hs(hsync),.vs(vsync),.hblank(hblank),.vblank(psx_vblank),
        .snac_enable(az_snac_on),.att1_n(snac_out[1]),.att2_n(snac_out[0]),.snac_cmd(snac_out[2]),.snac_clk(snac_out[5]),
        .snac_dat(snac_dat),.snac_ack(snac_ack),.snac_irq(snac_irq),
        .cart_tran_bank2(cart_tran_bank2),.cart_tran_bank2_dir(cart_tran_bank2_dir),.cart_tran_bank3(cart_tran_bank3),
        .cart_tran_bank3_dir(cart_tran_bank3_dir),.cart_tran_bank1(cart_tran_bank1),.cart_tran_bank1_dir(cart_tran_bank1_dir),
        .cart_tran_bank0(cart_tran_bank0),.cart_tran_bank0_dir(cart_tran_bank0_dir),.cart_tran_pin30(cart_tran_pin30),
        .cart_tran_pin30_dir(cart_tran_pin30_dir),.cart_pin30_pwroff_reset(cart_pin30_pwroff_reset),
        .cart_tran_pin31(cart_tran_pin31),.cart_tran_pin31_dir(cart_tran_pin31_dir));
'''
TOP_PORTS = ('.beginTransferSnac(beginTransferSnac),',
             '.beginTransferSnac(beginTransferSnac),\n.snac_port1(snac_port1),\n.snac_port2(snac_port2),\n'
             '.irq10Snac_in(irq10Snac),\n.actionNextSnac_in(actionNextSnac),\n.receiveValidSnac_in(receiveValidSnac),\n'
             '.ackSnac_in(ackSnac),\n.receiveBufferSnac_in(receiveBufferSnac),')


def top_text(text):
    for old, new in ((TIES, ''), TOP_PORTS):
        if text.count(old) != 1: raise ValueError('core_top anchor not found: ' + old.strip()[:60])
        text = text.replace(old, new)
    # the block uses the video and SNAC wires, so it goes after their declarations
    anchor = '    wire beginTransferSnac;\n'
    if text.count(anchor) != 1: raise ValueError('core_top anchor not found: wire beginTransferSnac')
    return text.replace(anchor, anchor + BLOCK)


# Settings and the GunCon csync input are quasi-static / asynchronous; the
# controller lines are synchronized inside psx_snac (two flops).
SDC = ('set_false_path -to [get_registers {*|az_vs_0[*] *|az_cs_0[*]}]\n'
       'set_false_path -from [get_registers {*|az_video_set[*] *|az_snac_set[*]}]\n'
       'set_false_path -to [get_registers {*|snac|USER_IN3_1 *|snac|USER_IN4_1 *|snac|USER_IN6_1 *|snac|csync}]\n')

FILES = ('rtl/pocket/psx_analogizer.sv', 'rtl/pocket/psx_snac.sv')
