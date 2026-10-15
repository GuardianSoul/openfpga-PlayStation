// SPDX-License-Identifier: GPL-2.0-only
// psx_disc_swap + psx_disc_select against a loader model with one reloadable
// disc slot: a reload raises disc_pending, the swap opens the lid, strobes
// the loader for slot 0, waits for the drained change and closes the lid.
module tb_disc_select;
  reg clk=0, reset=1, manual=0, pending=0;
  wire change_req, change_done, lid, busy, strobe; wire [15:0] slot;
  reg loader_done=0; integer strobes=0, mounted_file=1, loaded_file=1, lid_closed_on=0;
  psx_disc_swap #(.OPEN_CYCLES(20), .CLOSE_CYCLES(10)) swap(.clk(clk), .reset(reset), .manual_lid_open(manual),
      .swap_request(1'b0), .disc_pending(pending), .change_req(change_req), .change_done(change_done), .lid_open(lid), .busy(busy));
  psx_disc_select sel(.clk(clk), .reset(reset), .change_req(change_req), .change_done(change_done),
      .disc_slot_id(slot), .disc_select_strobe(strobe), .disc_change_done(loader_done));
  always #5 clk=~clk;
  // Loader model: on a strobe for slot 0, drain (40 cycles), mount the newly loaded file, pulse done.
  always @(posedge clk) begin
    loader_done<=0;
    if (strobe) begin
      if (slot!==16'h0000) $fatal(1,"wrong slot %h", slot);
      strobes=strobes+1; repeat(40) @(posedge clk); mounted_file=loaded_file; pending<=0; loader_done<=1;
    end
  end
  reg lid_d=0; always @(posedge clk) begin lid_d<=lid; if (lid_d && !lid) lid_closed_on=mounted_file; end
  task reload(input integer file); begin loaded_file=file; pending=1; wait(busy); wait(!busy); repeat(3) @(negedge clk); end endtask
  initial begin
    repeat(3) @(negedge clk); reset=0; repeat(3) @(negedge clk);
    reload(2); if (mounted_file!=2 || lid_closed_on!=2 || strobes!=1 || lid) $fatal(1,"disc 2 swap failed");
    reload(3); if (mounted_file!=3 || lid_closed_on!=3 || strobes!=2) $fatal(1,"disc 3 swap failed");
    $display("PASS disc select swaps=%0d", strobes); $finish;
  end
endmodule
