#!/usr/bin/env python3
"""Prepare an opt-in VRAM BUSY timing repair without editing the base RTL.

Valid commands retain their exact credit calculation and acceptance cycles.
Malformed writes may wait for prior write credits before acceptance/fault;
validation still blocks them from the physical command FIFO.
"""
from pathlib import Path

from prepare_resource_targets import digest
from tool_paths import ROOT


def conservative_text(text):
    """Previous timing variant, retained as the cycle-comparison reference."""
    before = "((offered && we && !invalid_command) ? {count,1'b0}:9'b0);"
    if text.count(before) != 1:
        raise ValueError('VRAM projected credit anchor changed')
    text = text.replace(before, "((offered && we) ? {count,1'b0}:9'b0);")
    marker = '    wire [TXB:0] projected_writes='
    if text.count(marker) != 1: raise ValueError('VRAM credit declaration changed')
    text = text.replace(marker, '''    // Conservative credits include a malformed offered write. It can wait
    // for prior writes to drain, then faults without entering the FIFO.
    // Keeping validation out of this expression removes the address/end
    // adder from the synchronous GPU BUSY path. Legal cycles are unchanged.
''' + marker)
    return text


def timing_text(original_text):
    """Render the current timing treatment from the unmodified frontend."""
    text = conservative_text(original_text)
    before = """    wire [TXB:0] projected_writes={1'b0,pending_writes}+
        ((offered && we) ? {count,1'b0}:9'b0);"""
    if text.count(before) != 1:
        raise ValueError('VRAM conservative credit expression changed')
    text = text.replace(before, """    // pending_writes <= MAX_WRITE_WORDS, and offered words <= 510.
    // With MAX_WRITE_WORDS >= 512 and modulus 2*MAX_WRITE_WORDS,
    // adding before subtracting cannot wrap the projected difference.
    // This moves one carry chain off the Gray-decode -> BUSY path.
    wire [TXB-1:0] projected_posted=posted_words+
        ((offered && we) ? {count,1'b0}:9'b0);
    wire [TXB-1:0] projected_delta=projected_posted-write_binary(finished_word_sync);
    wire [TXB:0] projected_writes={1'b0,projected_delta};""")
    return text


def prepare(directory):
    directory = Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True)
    original = ROOT / 'rtl/pocket/psx_vram_stream.sv'
    path = directory / 'psx_vram_stream.sv'; path.write_text(timing_text(original.read_text()))
    return dict(path=str(path), sha256=digest(path), original=str(original), original_sha256=digest(original),
        assignment='SYSTEMVERILOG_FILE', library='work',
        transform='Conservative malformed-write credits and reassociated modulo arithmetic shorten BUSY',
        credit_reassociated=True, additional_acceptance_latency=False,
        legal_command_latency_changed=False, malformed_write_fault_may_wait_for_prior_credits=True,
        production_selected=False, fit_verified=False, timing_verified=False, hardware_verified=False)
