#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""SDRAM refresh paid early while the controller is idle (Codex's EARLY_REFRESH=64).

Registered form (K fit #1446/#1447 failed release_timing on the first form):
the ref_timer window is registered a cycle ahead (exact), the idle test is
decided one edge early (early_quiet_q, still gated by op == OP_IDLE), and no
clk1x request line takes part.

A periodic refresh that comes due while the CPU or a DMA is waiting adds a
REFRESH (and on OPEN_ROW, a PRECHARGE) in front of that access. With this
step the controller may prepay one periodic refresh when it is ready and
idle, nothing is queued or arriving (CPU, SPU, loader, DMA FIFO, read hint),
no refresh is owed or forced, and the due tick is at most 64 clk3x away.
`early_pending` carries it across the PRECHARGE sequence; the credit is
consumed once at the due tick. Owed and forced refresh keep priority; a
forced refresh discards the credit. The refresh rate never drops: each
interval still gets exactly one REFRESH.

On the native rig, with the load window, row-conflict return, read hint and
DMA return, it takes the 0.5.9 store/SPU rig from 152 to 160 of 167 Peip rows
with no row lost; without it two block-DMA rows are lost (docs/timing-cpu-mem.md).
Guest-visible on purpose: no equivalence claim.
Usage: prepare_sdram_early_refresh.py PROJECT
(post-prepare step, after prepare_ram_read_hint.py when that is used)
"""
import argparse
import re
from pathlib import Path

TAG = 'sdram-early-refresh'
WINDOW = 64


def edits(hint):
    return (
        ('registers', '    reg        force_d = 0, force_pend = 0;\n',
         '    reg        force_d = 0, force_pend = 0;\n'
         f'    // {TAG}: at most one prepaid periodic refresh; pending spans the PRECHARGE.\n'
         '    reg early_credit = 0, early_pending = 0;\n'
         '    // ref_timer in (0, WINDOW] on the next edge (exact), and the idle test one edge early\n'
         '    reg early_window = 0, early_quiet_q = 0;\n'),
        ('need_ref', '    assign need_ref = ref_owed_nz || force_pend || (OPEN_ROW == 0 && force_rise);\n',
         f'    wire early_quiet_c = OPEN_ROW != 0 && // {TAG}\n'
         '        ready && !init_q && op == OP_IDLE && !early_credit && !early_pending &&\n'
         '        !ref_owed_nz && !force_pend && !force_rise && early_window &&\n'
         '        !req1 && !req2 && !req3 && !pend3_new && !fifo_v && !spu_first &&\n'
         f'        dmafifo_empty && !dmafifo_read && {"!hint_v" if hint else "1"};\n'
         '    // registered: decided on the edge before; a request that became pending in\n'
         '    // between waits for this refresh as it would for a due one\n'
         '    wire early_quiet = early_quiet_q && op == OP_IDLE;\n'
         '    assign need_ref = ref_owed_nz || force_pend || (OPEN_ROW == 0 && force_rise) || early_pending || early_quiet;\n'),
        ('flags', '            // Refresh interval\n',
         '            // Refresh interval\n'
         f'            early_window <= ref_timer >= 2 && ref_timer <= {WINDOW + 1}; // {TAG}\n'
         '            early_quiet_q <= early_quiet_c;\n'),
        ('due tick', '                if (ref_owed != 4\'d8) begin ref_owed <= ref_owed + 1\'b1; ref_owed_nz <= 1\'b1; end\n',
         '                if (early_credit) early_credit <= 0;\n'
         '                else if (ref_owed != 4\'d8) begin ref_owed <= ref_owed + 1\'b1; ref_owed_nz <= 1\'b1; end\n'),
        ('init', '                ready <= 0; power_wait <= 0; power_done <= 0; init_step <= 0;',
         '                ready <= 0; power_wait <= 0; power_done <= 0; init_step <= 0; early_credit <= 0; early_pending <= 0;'),
        ('precharge', '                    cmd <= C_PRE; a[10] <= need_ref;\n',
         '                    cmd <= C_PRE; a[10] <= need_ref;\n'
         '                    if (early_quiet) early_pending <= 1;\n'),
        ('refresh',
         '                    cmd <= C_REF;\n'
         '                    if (force_pend || force_rise) begin\n'
         '                        force_pend <= 0;\n'
         '                        if (!ref_owed_nz) ref_timer <= REFRESH_CYCLES[10:0];\n'
         '                        else begin ref_owed <= ref_owed - 1\'b1 + (ref_timer == 0); ref_owed_nz <= ref_owed != 4\'d1 || ref_timer == 0; end\n'
         '                    end else begin\n'
         '                        ref_owed <= ref_owed - 1\'b1 + (ref_timer == 0 && ref_owed != 4\'d8);\n'
         '                        ref_owed_nz <= ref_owed != 4\'d1 || (ref_timer == 0 && ref_owed != 4\'d8);\n'
         '                    end\n',
         '                    cmd <= C_REF;\n'
         f'                    early_pending <= 0; // {TAG}\n'
         '                    if (force_pend || force_rise) begin\n'
         '                        // a forced refresh starts a fresh interval when nothing is owed;\n'
         '                        // it discards a prepaid credit\n'
         '                        early_credit <= 0;\n'
         '                        force_pend <= 0;\n'
         '                        if (!ref_owed_nz) ref_timer <= REFRESH_CYCLES[10:0];\n'
         '                        else begin ref_owed <= ref_owed - 1\'b1 + (ref_timer == 0 && !early_credit); ref_owed_nz <= ref_owed != 4\'d1 || (ref_timer == 0 && !early_credit); end\n'
         '                    end else if (ref_owed_nz) begin\n'
         '                        ref_owed <= ref_owed - 1\'b1 + (ref_timer == 0 && !early_credit && ref_owed != 4\'d8);\n'
         '                        ref_owed_nz <= ref_owed != 4\'d1 || (ref_timer == 0 && !early_credit && ref_owed != 4\'d8);\n'
         '                    end else begin\n'
         '                        // early: a REF on the due tick pays that tick and grants no credit\n'
         '                        early_credit <= ref_timer != 0;\n'
         '                        ref_owed <= 0;\n'
         '                        ref_owed_nz <= 0;\n'
         '                    end\n'),
    )


def controller_text(text):
    if TAG in text:
        raise ValueError('controller already carries ' + TAG)
    if 'OPEN_ROW' not in text or 'LATE_START = CAS_LATENCY == 3' not in text:
        raise ValueError('needs the CL3 OPEN_ROW controller')
    for what, old, new in edits('ram-read-hint' in text):
        if text.count(old) != 1:
            raise ValueError(f'{what}: expected exactly one match, found {text.count(old)}')
        text = text.replace(old, new)
    return text


def apply(project):
    project = Path(project).resolve()
    qsf = project / 'apf_top.qsf'
    text = qsf.read_text()
    found = re.findall(r'SYSTEMVERILOG_FILE "([^"]*/psx_sdram_ctrl\.sv)"', text)
    if len(found) != 1:
        raise SystemExit(f'{TAG}: expected one psx_sdram_ctrl.sv, found {found}')
    out = project / TAG
    out.mkdir(exist_ok=False)
    new = out / 'psx_sdram_ctrl.sv'
    new.write_text(controller_text(Path(found[0]).read_text()))
    qsf.write_text(text.replace(found[0], str(new)))
    return new


if __name__ == '__main__':
    ap = argparse.ArgumentParser(usage=__doc__.split('Usage: ')[1])
    ap.add_argument('project')
    print('patched', apply(ap.parse_args().project))
