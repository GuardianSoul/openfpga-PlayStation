#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Rejected area experiment for Claude's 9e2d864 native 368 video slot.

Seed4 physical LAB use regresses 1847 -> 1848 on the matched FIR graph.
This helper exists for reproduction; it is not a selected optimization.
No change to selected video modes or cycles. The CE tracker stores only its
seven-clock predicate; the centering expression reuses the selected width.
"""
import hashlib

EXPECTED = "df4f89b7399120b78d3c72b37882e8fcab3bf112fa73c78f440b01ffa9fe81d7"
OUTPUT = "4382d6d0ff62b81ac0a42501ce511bb052755b57d0bdbf455abe98065e2dfdb6"

def video_368_budget_text(text):
    if hashlib.sha256(text.encode()).hexdigest() != EXPECTED:
        raise ValueError("Unreviewed 368-slot source")
    changes = [
        ("reg [3:0] cec=0, ce_p=0, ce_line=0;",
         "reg [2:0] cec=0;\n    reg ce_p=0, ce_line=0;"),
        ("ce_line==4'd7", "ce_line"),
        ("if (ce) begin ce_p<=cec+1'b1; cec<=0; end else if (cec!=4'hF) cec<=cec+1'b1;",
         "if (ce) begin ce_p<=cec==3'd6; cec<=0; end else if (cec!=3'd7) cec<=cec+1'b1;"),
        ("lead<=use_368 ? (over_368 ? 10'd0 : (10'd368-wlen)>>1) : over_320 ? 10'd0 : dbl_next ? 10'd320-wlen : (w_next-wlen)>>1;",
         "lead<=(use_368 ? over_368 : over_320) ? 10'd0 : dbl_next ? 10'd320-wlen : (w_next-wlen)>>1;"),
    ]
    for before, after in changes:
        if text.count(before) != 1:
            raise ValueError("Unreviewed expression: " + before)
        text = text.replace(before, after)
    if hashlib.sha256(text.encode()).hexdigest() != OUTPUT:
        raise ValueError("368-slot proposal differs from the measured source")
    return text

if __name__ == "__main__":
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output must be a new private file")
    args.output.write_text(video_368_budget_text(args.source.read_text()))
