// SPDX-License-Identifier: MIT
// Inserted into the common native-port fixture by run_main_psram.py.
// Controller-port model only: this does not simulate PocketQuake pins.
    wire fabric_idle; wire boot_owned=0,protocol_error=0,wait_error=0;
    wire valid=av && ar,write=aw;
    wire [2:0] client_ready,client_return,client_error;
    wire [127:0] client_data;
    reg [31:0] ticks=0;
    integer background1=0,background2=0,outstanding=0,owner=0;
    reg response_stalled=0;
    reg [127:0] held_response=0;
    reg [2:0] held_owner=0;
    wire [2:0] client_valid={BACKGROUND!=0 && !rst,BACKGROUND!=0 && !rst,av};
    wire [2:0] response_ready={ticks%3!=0,ticks%5!=0,arr};
    wire [15:0] dq0,dq1;
    wire [127:0] observed_data=($test$plusargs("FAULT_UPPER_DMA") && adapter.dma) ? {96'b0,client_data[31:0]}:client_data;
    assign ar=client_ready[0],arv=client_return[0],are=client_error[0],ard=observed_data;
    defparam native.pair.chip0.PAIRED_MODE=PAIRED;
    defparam native.pair.chip1.PAIRED_MODE=PAIRED;
    psx_psram_clients #(.PAIRED(PAIRED),.CLOCK_MHZ(500.0/MEM_HALF),.T_INIT(64)) native (
        .clk(clk),.reset(rst),.initialized(initialized),.idle(fabric_idle),
        .p_valid(client_valid),.p_write({2'b0,aw}),.p_ready(client_ready),
        .p_addr({25'h841000,25'h840800,aa}),.p_halfwords({4'd8,4'd2,an}),
        .p_wdata({256'b0,ad}),.p_byte_en({32'b0,ab}),
        .r_valid(client_return),.r_error(client_error),.r_ready(response_ready),.r_rdata(client_data),
        .fill_valid(),.fill_word(),.fill_data(),.cram0_a(),.cram1_a(),.cram0_dq(dq0),.cram1_dq(dq1),
        .cram0_wait(1'b0),.cram1_wait(1'b0),.cram0_adv_n(),.cram1_adv_n(),.cram0_cre(),.cram1_cre(),
        .cram0_ce0_n(),.cram0_ce1_n(),.cram1_ce0_n(),.cram1_ce1_n(),.cram0_oe_n(),.cram1_oe_n(),
        .cram0_we_n(),.cram1_we_n(),.cram0_ub_n(),.cram0_lb_n(),.cram1_ub_n(),.cram1_lb_n());
    always @(posedge clk) begin
        ticks<=ticks+1;
        if(!rst) for(integer j=1;j<3;j++) if(client_return[j] && response_ready[j]) begin
            if(client_error[j] || client_data!==0) $fatal(1,"Background packet content/class mismatch");
            if(j==1) background1++;else background2++;
        end
        // Scoreboard accepted/returned packets independently of DUT state.
        if(rst) begin
            outstanding=0;response_stalled=0;
            if(client_ready!=0) $fatal(1,"Grant during reset");
        end else begin
            if(fabric_idle && outstanding!=0) $fatal(1,"Idle before accepted packet completed");
            if((client_ready & (client_ready-3'b001))!=0 ||
               (client_return & (client_return-3'b001))!=0)
                $fatal(1,"Multiple packet owners");
            if(client_return!=0 && (outstanding!=1 || client_return!=(3'b001<<owner)))
                $fatal(1,"Response owner differs from accepted packet");
            if(response_stalled && (client_return!==held_owner || client_data!==held_response))
                $fatal(1,"Response changed under backpressure");
            response_stalled=|(client_return & ~response_ready);
            held_owner=client_return;held_response=client_data;
            if(|(client_return & response_ready)) outstanding=outstanding-1;
            if(|client_ready) begin
                if(outstanding!=0) $fatal(1,"Accepted a second outstanding packet");
                outstanding=outstanding+1;
                owner=client_ready[0] ? 0:client_ready[1] ? 1:2;
            end
        end
    end
