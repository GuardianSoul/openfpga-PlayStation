`timescale 1ns/1ps
`default_nettype none
// Simulation-only pin model. Independent command/latency is derived from the
// AS1C8M16PL datasheet, rev 1 Aug 2018, figs 5/6/18/33, tables 6/14/15/16.
// Models CRE writes and continuous fixed-latency bursts within a 128-word row.
// It does not observe RTL ready/state/counters. Memory persists across reset.
// Sparse byte storage, not a vendor electrical/retention model; async ordinary
// memory/variable latency/DPD/read-config are outside this selected test scope.
module cellularram_burst_model #(
    parameter integer HIGH_HALF=0,
    parameter real T_ACLK=9.0, T_KOH=2.0, T_HZ=7.0,
    parameter real T_SETUP=3.0, T_HOLD=2.0, T_CSP=4.0,
    parameter real T_CLK=15.0, T_KP=4.0, T_POWER=150000.0
)(
    input wire clk, host_oe,
    input wire [21:16] a,
    inout wire [15:0] dq,
    input wire ce0_n, ce1_n, adv_n, oe_n, we_n, ub_n, lb_n, cre,
    output wire wait_pin
);
    function automatic integer slot(input [22:0] word);
        if(word<23'h001000) slot=int'(word);
        else if(word>=23'h3ff000 && word<23'h401000) slot=4096+int'(word-23'h3ff000);
        else if(word>=23'h7ff000) slot=12288+int'(word-23'h7ff000);
        else begin slot=0; $fatal(1,"Burst PSRAM address outside modeled windows: %h",word); end
    endfunction
    function automatic [7:0] seed_byte(input integer byte_addr);
        seed_byte=8'(byte_addr*17+(byte_addr>>8)*23+8'h5a);
    endfunction
    reg [7:0] memory[0:32767];
    initial for(integer i=0;i<16384;i=i+1) begin
        integer w;
        w=i<4096 ? i : i<12288 ? 'h3ff000+i-4096 : 'h7ff000+i-12288;
        memory[2*i]=seed_byte(w*4+HIGH_HALF*2);
        memory[2*i+1]=seed_byte(w*4+HIGH_HALF*2+1);
    end
    reg [15:0] bcr[0:1], rcr[0:1];
    initial begin bcr[0]=16'h9d1f; bcr[1]=16'h9d1f; rcr[0]=16'h0010; rcr[1]=16'h0010; end
    wire ce_n=ce0_n && ce1_n;
    reg die=0, selected_config=0, burst=0, write_burst=0, config_latched=0, config_committed=0;
    reg [21:0] address=0;
    reg [15:0] config_data=0;
    reg [1:0] config_select=0;
    integer cycles=0, latency=0, row_words=0, transferred=0;
    integer reads=0,writes=0,commands=0,config_writes=0;
    realtime ce_fall=-1e9,ce_rise=-1e9,adv_fall=-1e9,adv_rise=-1e9,we_fall=-1e9,oe_fall=-1e9;
    realtime clk_rise=-1e9,clk_fall=-1e9,command_clock=-1e9;
    realtime a_change=-1e9,dq_change=-1e9,adv_change=-1e9,we_change=-1e9,bm_change=-1e9;
    reg driving=0,wait_drive=0,wait_value=0;
    reg [15:0] drive_data=0;
    assign dq=driving ? drive_data : 16'bz;
    assign wait_pin=wait_drive ? wait_value : 1'bz;
    always @* if(host_oe && driving) $fatal(1,"Burst PSRAM bus contention");
    always @(a) begin
        if(!ce_n && $realtime-command_clock<T_HOLD) $fatal(1,"Burst address hold");
        a_change=$realtime;
    end
    always @(dq) begin
        if(host_oe && burst && write_burst && $realtime-clk_rise<T_HOLD)
            $fatal(1,"Burst write data hold");
        if(host_oe && $realtime-command_clock<T_HOLD) $fatal(1,"Burst command data hold");
        dq_change=$realtime;
    end
    always @(adv_n) begin
        if(!ce_n && $realtime-clk_rise<T_HOLD) $fatal(1,"Burst ADV hold");
        adv_change=$realtime;
        if(!adv_n) adv_fall=$realtime;
    end
    always @(we_n) begin
        if(!ce_n && $realtime-command_clock<T_HOLD) $fatal(1,"Burst WE hold");
        we_change=$realtime;
        if(!we_n) we_fall=$realtime;
    end
    always @(ub_n or lb_n) begin
        if(burst && write_burst && $realtime-clk_rise<T_HOLD) $fatal(1,"Burst mask hold");
        bm_change=$realtime;
    end
    always @(negedge oe_n) begin
        oe_fall=$realtime;
        if(!ce_n && burst && !write_burst) begin driving<=1; drive_data<=16'hxxxx; end
    end
    always @(negedge ce0_n or negedge ce1_n) begin
        if(!ce0_n && !ce1_n) $fatal(1,"Burst PSRAM selected both dies");
        if($realtime<T_POWER) $fatal(1,"Burst PSRAM access before power-up wait");
        if($realtime-ce_rise<5.0) $fatal(1,"Burst PSRAM CE high gap");
        ce_fall=$realtime; die=ce0_n; selected_config=cre;
        config_latched=0; config_committed=0; burst=0; transferred=0;
    end
    always @(posedge adv_n) if(!ce_n && selected_config) begin
        if($realtime-adv_fall<5.0 || $realtime-ce_fall<7.0) $fatal(1,"Config ADV pulse/setup");
        if($realtime-a_change<5.0 || $realtime-dq_change<5.0) $fatal(1,"Config address setup");
        if(^{a,dq}===1'bx) $fatal(1,"Undriven config address");
        config_latched=1; config_data=dq; config_select=a[19:18]; adv_rise=$realtime;
    end
    task automatic commit_config;
        begin
            if($realtime-we_fall<45.0 || $realtime-ce_fall<70.0 || $realtime-adv_fall<70.0)
                $fatal(1,"Config write pulse/access time");
            if($realtime-dq_change<20.0) $fatal(1,"Config data setup");
            if(dq!==config_data) $fatal(1,"Config address/data changed");
            case(config_select)
                2'b10: bcr[die]=config_data;
                2'b00: rcr[die]=config_data;
                default: $fatal(1,"Unsupported config register write");
            endcase
            config_writes=config_writes+1;config_committed=1;
        end
    endtask
    always @(posedge we_n) if(!ce_n && selected_config && config_latched && !config_committed) commit_config();
    always @(posedge ce0_n or posedge ce1_n) if(ce0_n && ce1_n && ce_fall>0) begin
        if(selected_config && config_latched && !config_committed) commit_config();
        if($realtime-ce_fall>4000.0) $fatal(1,"Burst PSRAM refresh opportunity exceeded");
        if(burst && $realtime-clk_rise<T_HOLD) $fatal(1,"Burst CE hold");
        if(burst && transferred==0) $fatal(1,"Burst terminated before first data");
        ce_rise=$realtime;burst=0;
        driving<=#(T_HZ) 0; wait_drive<=#(T_HZ) 0;
    end
    always @(negedge clk) begin
        if(!ce_n && clk_rise>0 && $realtime-clk_rise<T_KP) $fatal(1,"Burst clock high width");
        clk_fall=$realtime;
    end
    always @(posedge clk) begin : clock_chip
        integer s;
        reg [15:0] value;
        if(!ce_n && clk_fall>0 && $realtime-clk_fall<T_KP) $fatal(1,"Burst clock low width");
        if(!ce_n) begin
            if(selected_config || cre) $fatal(1,"Clock during config access");
            if(!burst) begin
                if(adv_n) $fatal(1,"Burst without ADV address edge");
                if(bcr[die][15] || !bcr[die][14] || bcr[die][13:11]!=4 || rcr[die]!=16'h0010)
                    $fatal(1,"Burst PSRAM not configured for full-refresh fixed code 4");
                if($realtime-ce_fall<T_CSP || $realtime-a_change<T_SETUP ||
                   $realtime-dq_change<T_SETUP || $realtime-adv_change<T_SETUP || $realtime-we_change<T_SETUP)
                    $fatal(1,"Burst command setup");
                if(^{a,dq}===1'bx) $fatal(1,"Undriven burst address");
                address={a,dq}; write_burst=!we_n; cycles=0; latency=int'(bcr[die][13:11]);
                row_words=128-int'(address[6:0]); command_clock=$realtime; burst=1; commands=commands+1;
                wait_drive<=1;wait_value<=#(T_ACLK) 1;
            end else begin
                if($realtime-clk_rise<T_CLK) $fatal(1,"Burst clock exceeds configured frequency");
                if(!adv_n) $fatal(1,"Repeated ADV within burst");
                cycles=cycles+1;
                if(cycles>=latency) wait_value<=#(T_ACLK) 0;
                if(cycles>=(write_burst ? latency+1 : latency)) begin
                    if(transferred>=row_words) $fatal(1,"Burst crossed row without restart");
                    s=2*slot({die,address});
                    if(write_burst) begin
                        if(!host_oe || $realtime-bm_change<T_SETUP || $realtime-dq_change<T_SETUP)
                            $fatal(1,"Burst write data/mask setup");
                        if(!lb_n) begin if(^dq[7:0]===1'bx) $fatal(1,"Undriven write byte"); memory[s]=dq[7:0]; end
                        if(!ub_n) begin if(^dq[15:8]===1'bx) $fatal(1,"Undriven write byte"); memory[s+1]=dq[15:8]; end
                        writes=writes+1;
                    end else begin
                        if(oe_n || host_oe || $realtime-oe_fall<20.0) $fatal(1,"Burst read OE/turnaround");
                        value={memory[s+1],memory[s]};
                        driving<=1;
                        drive_data<=#(T_KOH) 16'hxxxx;
                        drive_data<=#(T_ACLK) value;
                        reads=reads+1;
                    end
                    transferred=transferred+1;address=address+1'b1;
                end
            end
        end
        clk_rise=$realtime;
    end
endmodule
`default_nettype wire
