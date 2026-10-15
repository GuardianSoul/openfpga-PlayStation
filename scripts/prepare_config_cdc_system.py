"""Optional config snapshot checks in the native shared-SDRAM reset fixture."""


def replace(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Native config CDC fixture anchor changed: '+before[:80])
    return text.replace(before, after)


def config_bench(text):
    text=replace(text,'logic [1:0] biosregion=0;', '''wire [1:0] biosregion;
reg config_host_clk=0;
always #6734000 config_host_clk=~config_host_clk;
reg [1:0] biosregion_host=0,region_host=0;
wire config_current;
reg config_reset=1;
always @(posedge config_host_clk) config_reset<=transport_reset;
psx_config_cdc #(.WIDTH(4)) host_config (
 .src_clk(config_host_clk),.dst_clk(clk1x),.reset_async(config_reset),
 .src_data({biosregion_host,region_host}),.src_current(config_current),
 .dst_data({biosregion,region}));
integer config_bios_requests=0;
reg config_check_cpu=0;
always @(negedge clk1x) begin
 if(config_check_cpu && ram_ena && ram_rnw && ram_Adr[24:23]==2'b01) begin
  if(!config_current || biosregion!==biosregion_host ||
     core.component_core.imemorymux.region!==biosregion_host ||
     ram_Adr[20:19]!==biosregion_host)
   $fatal(1,"Configuration BIOS bank stale at CPU request bank=%h expected=%h",ram_Adr[20:19],biosregion_host);
  config_bios_requests++;
 end
end''')
    text=replace(text,'logic [1:0] region=0;','wire [1:0] region;')
    text=replace(text,'        reset_start_cycle=cycles;reset_check_pending=1;warm_started=epoch==1;reset=0;',
'''        config_check_cpu=0;config_bios_requests=0;
        // Change the host level at reset release; the existing full reset
        // must give the coherent CPU snapshot time to reach its bank latch.
        biosregion_host=epoch==0 ? 2'b01:2'b10;
        reset_start_cycle=cycles;reset_check_pending=1;warm_started=epoch==1;reset=0;''')
    text=replace(text,'        @(negedge clk1x);pause=0;',
'''        if(!config_current || biosregion!==biosregion_host ||
           core.component_core.imemorymux.region!==biosregion_host)
            $fatal(1,"Configuration BIOS bank not latched before CPU release");
        config_check_cpu=1;
        @(negedge clk1x);pause=0;''')
    text=replace(text,'        check_diagnostic_commit();',
'''        check_diagnostic_commit();
        if(config_bios_requests<1) $fatal(1,"Configuration BIOS fetch coverage missing");
        $display("CONFIG_NATIVE epoch=%0d bank=%0d bios_requests=%0d",epoch,biosregion,config_bios_requests);''')
    return text
