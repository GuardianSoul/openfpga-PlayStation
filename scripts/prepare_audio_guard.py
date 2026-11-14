#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Audio step "audio-guard" (RTL; public issue #21, docs/public-issues.md).

core_top held the audio bridge in reset while the audio PLL's `locked` was low
(ram_refresh || !audio_lock_sync) and reset it whenever `locked` dropped; a fatal bridge fault
(source overflow, missed output deadline) muted until the next game start. This step replaces
that register with psx_audio_guard (rtl/audio/psx_audio_guard.sv, appended to core_top.sv as a
second module so the project's source list does not change):

  - the lock latches after ~1 ms of stable `locked`, or after ~10 ms whatever `locked` does;
    it stays set until cpu_locked falls. Once latched, only ram_refresh resets the bridge, as
    before.
  - a fatal fault held for 2 ticks (~2 ms, output muted meanwhile) raises the bridge reset for
    one tick (~1 ms); the FIFO refills and sound resumes.
  - diagnostic 0x13000000: bit0 fault (as before), bit1 lock timed out, bit2 recovered (sticky).

SDC: the reset origin is audio_guard|reset_async; the false paths go to the guard's lock_meta
and fatal_meta (single-bit level synchronizers) and to audio_diag_meta.

Check: scripts/run_audio_guard.py (Icarus, Laptop; 5 planted mutations).
Usage: prepare_audio_guard.py SOURCE --output OUTPUT [--seed N]   (frozen project snapshot)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_paths import ROOT

TAG = 'audio-guard'
GUARD = ROOT / 'rtl/audio/psx_audio_guard.sv'


def _once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected 1 match, found {text.count(old)}: {old.strip()[:80]}')
    return text.replace(old, new)


OLD_RESET = '''    (* async_reg="true", preserve, dont_merge *) reg audio_lock_meta=0, audio_lock_sync=0;
    (* preserve, dont_merge *) reg audio_reset_async=1;
    always @(posedge clk1x or negedge cpu_locked) begin
        if(!cpu_locked) begin audio_lock_meta<=0;audio_lock_sync<=0;audio_reset_async<=1;end
        else begin
            audio_lock_meta<=audio_locked;audio_lock_sync<=audio_lock_meta;
            audio_reset_async<=ram_refresh || !audio_lock_sync;
        end
    end
    wire audio_fault;
'''
NEW_RESET = f'''    // {TAG} (#21): sticky audio-PLL lock with a timeout, self-recovery after a fatal fault
    wire audio_reset_async;
    wire audio_fault;
    wire [7:0] audio_status;
    psx_audio_guard audio_guard (.clk(clk1x),.rst_n(cpu_locked),.pll_locked(audio_locked),
        .restart(ram_refresh),.fatal(audio_status[0]),.reset_async(audio_reset_async),.clock_good(),
        .timed_out(audio_lock_timed_out),.recovered(audio_recovered));
'''
OLD_PORT = '.fault(audio_fault),\n        .rate_correction(),'
NEW_PORT = '.fault(audio_fault),.status(audio_status),\n        .rate_correction(),'
OLD_DIAG_SYNC = '''    always @(posedge clk_74a) begin audio_fault_meta<=audio_fault;audio_fault_sync<=audio_fault_meta;end
'''
NEW_DIAG_SYNC = OLD_DIAG_SYNC + f'''    wire audio_lock_timed_out, audio_recovered;   // {TAG}: psx_audio_guard (clk1x, sticky)
    (* async_reg="true", preserve, dont_merge *) reg [1:0] audio_diag_meta=0, audio_diag_sync=0;   // {TAG}
    always @(posedge clk_74a) begin audio_diag_meta<={{audio_recovered,audio_lock_timed_out}};audio_diag_sync<=audio_diag_meta;end
'''
OLD_DIAG = "        32'h13000000: service_read={31'b0,audio_fault_sync};\n"
NEW_DIAG = "        32'h13000000: service_read={29'b0,audio_diag_sync,audio_fault_sync};   // " + TAG + "\n"

OLD_SDC_ORIGIN = 'set audio_reset_origin [get_registers {*|audio_reset_async}]\n'
NEW_SDC_ORIGIN = 'set audio_reset_origin [get_registers {*|audio_guard|reset_async}]\n'
OLD_SDC_META = 'set_false_path -to [get_registers {*|audio_lock_meta *|audio_fault_meta}]\n'
NEW_SDC_META = ('set_false_path -to [get_registers {*|audio_guard|lock_meta *|audio_guard|fatal_meta *|audio_fault_meta '
                '*|audio_diag_meta[*]}]\n')


def top_text(text):
    if TAG in text:
        raise ValueError(f'{TAG}: core_top already has the step')
    for old, new in ((OLD_RESET, NEW_RESET), (OLD_PORT, NEW_PORT), (OLD_DIAG_SYNC, NEW_DIAG_SYNC), (OLD_DIAG, NEW_DIAG)):
        text = _once(text, old, new)
    return text.rstrip('\n') + '\n\n' + GUARD.read_text()


def sdc_text(text):
    if 'audio_guard' in text:
        raise ValueError(f'{TAG}: pocket.sdc already has the step')
    for old, new in ((OLD_SDC_ORIGIN, NEW_SDC_ORIGIN), (OLD_SDC_META, NEW_SDC_META)):
        text = _once(text, old, new)
    return text


def prepare(source, output, seed=None):
    import prepare_save_checksum as cs
    return cs.prepare(source, output, seed, {'core_top.sv': top_text, 'pocket.sdc': sdc_text})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int)
    args = parser.parse_args()
    try:
        seed, changed = prepare(args.source, args.output, args.seed)
    except ValueError as error:
        raise SystemExit(str(error))
    print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
    for path, (old, new) in changed.items():
        print(f'{TAG}: patched {path} (sha256 {old[:16]} -> {new[:16]})')
