// SPDX-License-Identifier: GPL-2.0-only
// SNAC for prepare --analogizer: a real PlayStation controller (or memory
// card) on the Analogizer's SNAC port, driven by the PSX core's own SIO.
//
// Ported from the SNAC block of the pinned MiSTer PSX top
// (references/PSX_MiSTer/PSX.sv, lines 1639-1833, Copyright (C) 2019 Robert
// Peip, GPL-2.0): the always block is that code unchanged, with MiSTer's user
// port named (USER_IN[3] ack_in, USER_IN[4] dat_in, USER_IN[6] irq_in,
// user_out[6:0] as USER_OUT, VGA_HS/VGA_VS as hs/vs) and its implicit nets
// declared. joypad.vhd's SNAC ports connect as in MiSTer's psx_top instance:
// ackSnac is ~ack.
// user_out: [0] ATT2_n, [1] ATT1_n, [2] CMD, [5] CLK, [6] GunCon csync / 1;
// [3]/[4] (ACK/DAT) drive high on MiSTer's open-drain port and are not used
// here.
`default_nettype none
module psx_snac #(
    // CARDS 0 (2026-10-22: pads and GunCon; memory cards stay virtual):
    // with joypad.vhd's snacMC tied 0 it drops the SNAC select on a 0x81 card
    // access, so the card/PocketStation branch below never runs; CARDS 0
    // lets synthesis remove it (~60 ALMs).
    parameter CARDS = 0
) (
    input  wire       clk_1x,
    input  wire       snacPort1, snacPort2,
    input  wire       ack_in, dat_in, irq_in,   // controller lines, asynchronous
    input  wire       hs, vs,                   // GunCon csync (video clock, quasi-static use)
    output reg  [6:0] user_out = 7'h7f,
    // joypad.vhd
    input  wire       selectedPort1Snac, selectedPort2Snac,
    input  wire [7:0] transmitValueSnac,
    input  wire       clk9Snac, beginTransferSnac,
    output reg        irq10Snac = 0,
    output reg [7:0]  receiveBufferSnac = 0,
    output reg        receiveValidSnac = 0, actionNextSnac = 0,
    output wire       ackSnac
);
    wire clk8Snac;
    reg oldClk8 = 0, oldClk9 = 0, oldselectedPort1 = 0, oldselectedPort2 = 0;
    reg [7:0] Send = 0, Receive = 0, pad1ID = 0, pad2ID = 0, targetID = 0, PSdatalength = 0;
    reg Cmd = 1, Dat = 1, ack = 1, oldAck = 1, ackNone = 0, oneTime = 0, actionNextPadSnac = 0, csync = 0;
    reg MCtransfer = 0, PStransfer = 0;
    // CARDS 0: the 60000 load (cards) never runs, so ackTimer stays at or under
    // 1800 and 11 bits hold every value it takes; CARDS 1 keeps all 16.
    localparam ATW = CARDS ? 16 : 11;
    reg [ATW-1:0] ackTimer = 0;
    reg [3:0] bitCnt = 0;
    reg [8:0] byteCnt = 0, bytesLeft = 0;
    reg USER_IN3_1 = 1, USER_IN4_1 = 1, USER_IN6_1 = 1, USER_IN3_2 = 1, USER_IN4_2 = 1, USER_IN6_2 = 1;
    reg USER_IN3_3 = 1, USER_IN3_4 = 1, ackglitch = 1;
    assign ackSnac = ~ack;   // MiSTer: .ackSnac(~ack), the real ack

assign clk8Snac = bitCnt < 8 ? clk9Snac : 1'b1;

always @(posedge clk_1x)
begin

   USER_IN3_1 <= ack_in;
   USER_IN4_1 <= dat_in;
   USER_IN6_1 <= irq_in;

   USER_IN3_2 <= USER_IN3_1;
   USER_IN4_2 <= USER_IN4_1;
   USER_IN6_2 <= USER_IN6_1;

   USER_IN3_3 <= USER_IN3_2;//glitch filter for ack
   USER_IN3_4 <= USER_IN3_3;
   ackglitch  <= ~USER_IN3_1 && ~USER_IN3_2 && ~USER_IN3_3 && ~USER_IN3_4 ? 1'b0 : 1'b1;

	if (snacPort1 || snacPort2) begin
		user_out[0] <= ~selectedPort2Snac;
		user_out[1] <= ~selectedPort1Snac;
		user_out[2] <= Cmd;
		user_out[3] <= 1'b1; //ACK
		user_out[4] <= 1'b1; //DAT
		user_out[5] <= oldClk8;
		ack         <= ~ackglitch ? USER_IN3_2 : 1'b1;
		Dat         <= USER_IN4_2;

		if ((pad1ID == 8'h63 || pad2ID == 8'h63) && (pad1ID != 8'h31 || pad2ID != 8'h31)) begin //quirk for guncon, irq is N/C in guncon. so using irq line and outputting csync on snac for g-con. only if justifier isn't connected
			user_out[6] <= ~csync;
			irq10Snac   <= 1'b0;
			csync       <= hs ^ vs;//real csync shifts HSync during VSync, should be close enough to work	with guncon
		end
		else begin
			user_out[6] <= 1'b1;
			irq10Snac   <= ~USER_IN6_2;
		end
	end
	else begin
		user_out  <= '1;
		irq10Snac <= 1'b0;
		ack       <= 1'b1;
		Dat       <= 1'b1;
	end

	oldselectedPort1 <= selectedPort1Snac;
	oldselectedPort2 <= selectedPort2Snac;

	if ((~oldselectedPort1 && selectedPort1Snac) || (~oldselectedPort2 && selectedPort2Snac)) begin
		byteCnt    <= 9'd0;
		bytesLeft  <= 9'd0;
		MCtransfer <= 1'b0;
	end

	if (beginTransferSnac) begin
		bitCnt  <= 4'd0;
		byteCnt <= byteCnt + 9'd1 ;
	end

	oldClk8 <= clk8Snac;
	oldClk9 <= clk9Snac;

	if (oldClk9 && ~clk9Snac) begin	//send on falling edge
		if (bitCnt < 8) begin
			if (bitCnt==0) begin
				Cmd  <= transmitValueSnac[0];
				Send <= {1'b1, transmitValueSnac[7:1]};
			end
			else begin
				Cmd  <= Send[0];
				Send <= {1'b1, Send[7:1]};
			end
		end
		else begin
			Cmd  <= 1'b1;
			Send <= Send;
		end
	end

	if(~oldClk8 && clk8Snac) begin //receive on rising edge
		Receive <= { Dat, Receive[7:1]};
		bitCnt <= bitCnt + 1'b1;
		if(bitCnt == 4'd7) begin//check for ack
			oneTime <= 1'b1;
			if (MCtransfer) ackTimer <= ATW'(60000);//very late ack after 7th byte. around 56000 cycles (1.7ms) with a sony MC. 3rd party MCs don't seem to do this
			else begin
				if (byteCnt == bytesLeft + 3) ackTimer <= ATW'(400);//only wait around 150 on last byte
				else ackTimer <= ATW'(1800);//1st byte of multitap(1375) cycles to ack,digital(460),analog(350-400),ds2(250-400),mouse(120),guncon(270)
			end
		end
	end

	if (ackTimer > 0) begin
		ackTimer <= ackTimer - 1'b1;
	end

	oldAck <= ack;
	if(oldAck && ~ack) begin //ack received
		actionNextPadSnac <= 1'b1;
		ackTimer <= ATW'(173);//16'd255;//a delay between ack and next action. too small might cause a hang. was using acktimer 1-255
	end
	else if(ackTimer == 1) begin //wait over
		actionNextPadSnac <= 1'b1;
		oneTime <= 1'b0;
	end
	else if (ackTimer == ATW'(258)) begin //no ack
		ackNone <= 1'b1;
		actionNextPadSnac <= 1'b1;
	end
	else if (ackTimer == ATW'(256)) begin //reset if no ack
		oneTime <= 1'b0;
		ackTimer <= ATW'(0);
	end
	else begin
		actionNextPadSnac <= 1'b0;
		ackNone <= 1'b0;
	end

	if (actionNextPadSnac && ((snacPort1 && selectedPort1Snac) || (snacPort2 && selectedPort2Snac))) begin //logic for joypad.vhd
		if (oneTime) begin
			if (ackNone) begin
				if (byteCnt < (bytesLeft + 4)) begin // no ack on last byte of transfer
					receiveBufferSnac <= Receive;
					receiveValidSnac <= 1'b1;
					actionNextSnac <= 1'b1;
				end
				else
					actionNextSnac <= 1'b1;
				end
			else begin
				if (byteCnt < (bytesLeft + 4)) begin
					receiveBufferSnac <= Receive;
					receiveValidSnac <= 1'b1;
					//ackSnac <= 1'b1;
				end
				actionNextSnac <= 1'b1;
			end
		end
		else begin
			actionNextSnac <= 1'b1;
		end
	end
	else begin
		receiveBufferSnac <= 8'd0;
		receiveValidSnac <= 1'b0;
		actionNextSnac <= 1'b0;
		//ackSnac <= 1'b0;
	end

	if (receiveValidSnac) begin
		if (byteCnt == 1) begin
			targetID <= transmitValueSnac;
		end
		if (byteCnt == 2) begin
			if (CARDS && (targetID == 8'h81 || targetID == 8'h82 || targetID == 8'h83 || targetID == 8'h84)) begin 	//memcard quirks
				MCtransfer <= 1'b1;
				if (transmitValueSnac == 8'h52) bytesLeft <= 9'd137;//read
				if (transmitValueSnac == 8'h57) bytesLeft <= 9'd135;//write
				if (transmitValueSnac == 8'h53) bytesLeft <= 9'd7;//ID Cmd
				//pocketstation
				if (transmitValueSnac == 8'h50) bytesLeft <= 9'd0;//Change a FUNC 03h related value
				if (transmitValueSnac == 8'h58) bytesLeft <= 9'd2;//Get an ID or Version value
				if (transmitValueSnac == 8'h59) bytesLeft <= 9'd6;//Prepare File Execution with Dir_index, and Parameter
				if (transmitValueSnac == 8'h5A) bytesLeft <= 9'd18;//Get Dir_index, ComFlags, F_SN, Date, and Time
				if (transmitValueSnac == 8'h5D) bytesLeft <= 9'd3;//Execute Custom Download Notification
				if (transmitValueSnac == 8'h5E) bytesLeft <= 9'd3;//Get-and-Send ComFlags.bit1,3,2
				if (transmitValueSnac == 8'h5F) bytesLeft <= 9'd1;//Get-and-Send ComFlags.bit0
				if (transmitValueSnac == 8'h5B) begin//Execute Function and transfer data from Pocketstation to PSX--variable length
					bytesLeft <= 9'd3;
					PStransfer <= 1'b1;
				end
				if (transmitValueSnac == 8'h5C) begin//Execute Function and transfer data from PSX to Pocketstation--variable length
					bytesLeft <= 9'd3;
					PStransfer <= 1'b1;
				end
			end
			else begin //joypad quirks
				MCtransfer <= 1'b0;
				if (selectedPort1Snac) pad1ID <= Receive;
				if (selectedPort2Snac) pad2ID <= Receive;

				if (Receive == 8'h80) bytesLeft <= 9'd32; //for multitap
				else bytesLeft <= {5'd0, (Receive[3:0] + Receive[3:0])};
			end
		end
		if (byteCnt == 4 && PStransfer == 1) begin //for pocketstation
			bytesLeft <= bytesLeft + Receive;
			PSdatalength <=  Receive;
		end
		if ((byteCnt == PSdatalength + 5) && PStransfer == 1) begin
			bytesLeft <= bytesLeft + Receive;
			PStransfer <= 1'b0;
		end
	end
end

endmodule
`default_nettype wire
