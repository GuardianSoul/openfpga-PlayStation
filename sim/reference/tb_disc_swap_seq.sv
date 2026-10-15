// SPDX-License-Identifier: GPL-2.0-only
// psx_disc_swap: automatic swap timing, request edges, manual lid, reset.
module tb_disc_swap_seq;
  reg clk=0, reset=1, manual=0, req=0, done=0, pending=0;
  wire change_req, lid, busy;
  integer cycle=0, opened_at, change_at, closed_at, checks=0;
  psx_disc_swap #(.OPEN_CYCLES(100), .CLOSE_CYCLES(50)) dut(.clk(clk), .reset(reset), .manual_lid_open(manual),
      .swap_request(req), .disc_pending(pending), .change_req(change_req), .change_done(done), .lid_open(lid), .busy(busy));
  always #5 clk=~clk;
  always @(posedge clk) cycle<=cycle+1;
  // CD service model: acknowledges a change 37 cycles after the request.
  always @(posedge clk) begin done<=0; if (change_req && !done) begin repeat(37) @(posedge clk); done<=1; pending<=0; end end
  task tick(input integer n); repeat(n) @(negedge clk); endtask
  initial begin
    tick(3); reset=0; tick(3);
    if (lid || busy) $fatal(1,"lid open after reset"); checks++;
    // Automatic swap with a held request: one swap only.
    req=1; tick(1); opened_at=cycle;
    while (!change_req) begin tick(1); if (!lid) $fatal(1,"lid closed during the open wait"); end
    change_at=cycle; if (change_at-opened_at < 100) $fatal(1,"change requested too early: %0d",change_at-opened_at); checks++;
    while (busy) begin tick(1); if (busy && !lid) $fatal(1,"lid closed before the sequence ended"); end
    closed_at=cycle; if (closed_at-change_at < 50+37) $fatal(1,"lid closed too early"); checks++;
    tick(300); if (busy) $fatal(1,"held request started a second swap"); checks++;
    req=0; tick(2);
    // A new request edge starts a new swap.
    req=1; tick(2); req=0; if (!busy || !lid) $fatal(1,"second swap did not start"); checks++;
    wait(!busy); tick(2);
    // Manual lid passes straight through.
    manual=1; tick(1); if (!lid || busy) $fatal(1,"manual lid not open"); manual=0; tick(1); if (lid) $fatal(1,"manual lid stuck"); checks++;
    // Reset during a swap closes the lid.
    req=1; tick(10); reset=1; tick(2); reset=0; req=0; tick(2); if (lid || busy || change_req) $fatal(1,"reset did not abort the swap"); checks++;
    // Service reports a new file with the lid closed: automatic swap.
    pending=1; tick(2); if (!busy || !lid) $fatal(1,"pending disc did not start a swap"); wait(!busy); tick(2);
    if (pending) $fatal(1,"pending disc not consumed"); checks++;
    // New file while the user holds the lid open: switch at once, lid stays open, no timed sequence.
    manual=1; tick(2); pending=1; tick(2); if (!change_req || busy) $fatal(1,"open-lid change not requested");
    wait(!pending); tick(2); if (change_req || !lid) $fatal(1,"open-lid change not completed"); manual=0; tick(2); checks++;
    // Glue: change_req rising strobes the selected slot once.
    $display("PASS disc swap sequencer checks=%0d", checks); $finish;
  end
endmodule
