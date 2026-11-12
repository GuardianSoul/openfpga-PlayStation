#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Side-by-side check of prepare_save_disc_wrong.py: the "wrong disc" synchroniser cut from a project's
core_top.sv before and after the step (Icarus). clk_74a drives mount_failed / cue_mounted through every
combination at random times, both synchronisers sample on clk1x; on every clk1x edge:
  - the new output equals mount_failed two clk1x edges earlier (a reference);
  - old and new agree whenever cue_mounted was 1 at that sample (the step changes only the case
    "failed, not mounted");
  - the case "failed, not mounted" is reached (counted) and there the old output is 0, the new 1.
Planted faults (prepare_save_disc_wrong.FAULTS) must fail.
Usage: run_save_disc_wrong.py PROJECT [--work DIR]
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

import prepare_save_disc_wrong as step
from tool_paths import ROOT, find_tool

TB = r'''
`timescale 1ns/1ps
module tb;
    reg clk1x = 0, clk_74a = 0; always #14.765 clk1x = ~clk1x; always #6.734 clk_74a = ~clk_74a;
    reg cue_mount_failed = 0, cue_mounted = 0;
    wire w_old, w_new;
    old_sync o(.clk1x(clk1x), .cue_mount_failed(cue_mount_failed), .cue_mounted(cue_mounted), .w(w_old));
    new_sync n(.clk1x(clk1x), .cue_mount_failed(cue_mount_failed), .cue_mounted(cue_mounted), .w(w_new));
    always @(posedge clk_74a) if ($urandom % 41 == 0) {cue_mount_failed, cue_mounted} <= $urandom;
    // audit-059 #17: DISC = the cue disc (cur - 1; disc 1 for 0) while a cue is mounted, else the menu's
    reg [3:0] cur = 0; reg [2:0] menu = 0; wire [2:0] disc;
    always @(posedge clk_74a) if ($urandom % 53 == 0) cur <= $urandom % 6;
    always @(posedge clk1x) if ($urandom % 37 == 0) menu <= $urandom % 5;
    disc_mux d(.clk1x(clk1x), .cue_mounted(cue_mounted), .cue_cur_disc(cur), .save_menu_disc(menu), .save_disc(disc));
    reg [4:0] dh0 = 0, dh1 = 0; integer cue_disc = 0;
    reg [1:0] f_h = 0, m_h = 0; integer errors = 0, edges = 0, differ = 0;
    always @(posedge clk1x) begin
        // values before this edge: the outputs reflect the inputs sampled two edges ago
        if (edges > 2) begin
            if (w_new !== f_h[1]) begin errors = errors + 1; if (errors < 5) $display("FAIL new is not mount_failed two clocks late"); end
            if (m_h[1] && w_old !== w_new) begin errors = errors + 1; if (errors < 5) $display("FAIL differs while mounted"); end
            if (!m_h[1] && f_h[1]) begin differ = differ + 1; if (w_old !== 1'b0) begin errors = errors + 1; $display("FAIL old not 0"); end end
        end
        if (edges > 2) begin
            if (dh1[4] && disc !== (dh1[3:0] == 0 ? 3'd0 : dh1[2:0] - 3'd1)) begin errors = errors + 1; if (errors < 5) $display("FAIL DISC is not the cue disc"); end
            if (!dh1[4] && disc !== menu) begin errors = errors + 1; if (errors < 5) $display("FAIL DISC is not the menu's"); end
            if (dh1[4] && dh1[3:0] != 0 && dh1[3:0] - 1 != menu) cue_disc = cue_disc + 1;
        end
        dh1 = dh0; dh0 = {cue_mounted, cur};
        f_h = {f_h[0], cue_mount_failed}; m_h = {m_h[0], cue_mounted}; edges = edges + 1;
    end
    initial begin
        #2000000;
        if (differ < 50) begin errors = errors + 1; $display("FAIL the changed case too rare: %0d", differ); end
        if (cue_disc < 50) begin errors = errors + 1; $display("FAIL the cue disc rarely differs from the menu's: %0d", cue_disc); end
        if (errors == 0) $display("DISCWRONG PASS edges=%0d failed_not_mounted=%0d cue_disc_not_menu=%0d", edges, differ, cue_disc);
        else $display("DISCWRONG FAIL errors=%0d", errors);
        $finish;
    end
endmodule
'''


def disc_module(core_top):
    m = re.search(r"    \(\* async_reg=\"true\", preserve, dont_merge \*\) reg \[4:0\] save_cue_disc_sync0.*?\n(.*?assign save_disc = [^\n]*\n)", core_top, re.S)
    if not m: return ('module disc_mux(input clk1x, input cue_mounted, input [3:0] cue_cur_disc, input [2:0] save_menu_disc, output [2:0] save_disc);\n'
                      '    assign save_disc = save_menu_disc;\nendmodule\n')   # (the old core_top: DISC is the menu's)
    return ('module disc_mux(input clk1x, input cue_mounted, input [3:0] cue_cur_disc, input [2:0] save_menu_disc, output [2:0] save_disc);\n'
            "    reg [4:0] save_cue_disc_sync0=5'd0, save_cue_disc_sync1=5'd0;\n" + m[1] + 'endmodule\n')


def module(name, core_top):
    m = re.search(r'    \(\* async_reg="true", preserve, dont_merge \*\) reg \[1:0\] save_disc_wrong_sync=2\'b00;.*?\n(.*?)    assign save_disc_wrong = save_disc_wrong_sync\[1\];\n', core_top, re.S)
    if not m: raise SystemExit('synchroniser not found in core_top')
    return (f'module {name}(input clk1x, input cue_mount_failed, input cue_mounted, output w);\n'
            "    reg [1:0] save_disc_wrong_sync = 2'b00;\n" + m[1] + '    assign w = save_disc_wrong_sync[1];\nendmodule\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project', type=Path)
    ap.add_argument('--work', type=Path, default=ROOT / 'build/sim/save-disc-wrong')
    args = ap.parse_args()
    w = args.work.resolve(); w.mkdir(parents=True, exist_ok=True)
    rows = [Path(r['path']) for r in json.loads((args.project / 'fit-inputs.json').read_text())['quartus_sources'] if Path(r['path']).name == 'core_top.sv']
    text = rows[0].read_text()
    old, new = (step.restore(text), text) if step.TAG in text else (text, step.ct_text(text))
    (w / 'tb.sv').write_text(TB)
    runs = {'step': new, **{f: step.fault_text(new, f) for f, (o, _) in step.FAULTS.items() if 'cur_disc_o<=' not in o}}
    ok, report = True, {}
    for name, t in runs.items():
        (w / f'{name}.v').write_text(module('old_sync', old) + module('new_sync', t) + disc_module(t))
        subprocess.run([find_tool('iverilog'), '-g2012', '-o', str(w / f'{name}.vvp'), str(w / f'{name}.v'), str(w / 'tb.sv')], check=True)
        out = subprocess.run([find_tool('vvp'), str(w / f'{name}.vvp')], capture_output=True, text=True, timeout=600).stdout
        line = (re.findall(r'DISCWRONG [^\n]*', out) or ['no result'])[-1]
        good = line.startswith('DISCWRONG PASS') if name == 'step' else not line.startswith('DISCWRONG PASS')
        ok &= good; report[name] = dict(result=line, ok=good)
        print(f'{name}: {line} -> {"ok" if good else "WRONG"}', flush=True)
    (w / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    print('PASS save-disc-wrong' if ok else 'FAIL save-disc-wrong')
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
