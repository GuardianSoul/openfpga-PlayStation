#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""0.6.0 LAB-room step "lab-dma" on a frozen snapshot's save-states/dma.vhd (docs/integration-060.md).

Parts (--parts, comma-separated):
  zero-row   The MADR / BCR / CHCR channel tables (six falling-edge M10K reads) were masked after
             the read with the per-channel valid bit (`q when valid(ch) else 0`, 138 AND terms).
             The tables grow from 8 to 16 rows; rows 8-15 are never written and read as zero
             (initialised zero, M10K power-up content). Each read address becomes
             ch + 8 * not valid(ch), sampled at the same falling edge as before, so the data
             registered for a channel without a valid entry is zero; the mask goes. valid only
             changes on rising edges and bus_channel / triggerchannel are stable between the
             falling edge and the next rising edge, so the masked and the unmasked value agree at
             every rising edge that uses them.
  madr-step  dmaSettings.D_MADR's +4 (READHEADER) and +4 / -4 (WORKING) become one adder,
             D_MADR + {21 x directionNeg, "100"}. READHEADER only runs in linked-list mode, where
             directionNeg is 0 (the trigger sets it only for sync modes 0 / 1), so it still adds 4.

Check: scripts/run_lab_dma.py (tb_dma_channel_ram lockstep against the snapshot's own dma.vhd,
rising-edge stimulus, three seeds; planted faults).
Usage: prepare_lab_dma.py SOURCE --output OUTPUT [--parts zero-row,madr-step] [--seed N]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'lab-dma'
PARTS = ('zero-row', 'madr-step')


def _once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected 1 match, found {text.count(old)}: {old.strip()[:90]}')
    return text.replace(old, new)


ZERO_ROW = [
    ("   type tmadrMem is array(0 to 7) of unsigned(23 downto 0);\n   type tbcrMem  is array(0 to 7) of unsigned(31 downto 0);\n",
     f"   -- {TAG} zero-row: rows 8-15 are never written; a channel without a valid entry reads one of them\n"
     "   type tmadrMem is array(0 to 15) of unsigned(23 downto 0);\n   type tbcrMem  is array(0 to 15) of unsigned(31 downto 0);\n"),
    ("   signal madrMem_bus  : tmadrMem;\n   signal madrMem_trig : tmadrMem;\n   signal bcrMem_bus   : tbcrMem;\n   signal bcrMem_trig  : tbcrMem;\n",
     "   signal madrMem_bus  : tmadrMem := (others => (others => '0'));\n   signal madrMem_trig : tmadrMem := (others => (others => '0'));\n"
     "   signal bcrMem_bus   : tbcrMem := (others => (others => '0'));\n   signal bcrMem_trig  : tbcrMem := (others => (others => '0'));\n"),
    ("   type tchcrMem is array(0 to 7) of unsigned(12 downto 0);\n   signal chcrMem_bus  : tchcrMem;\n   signal chcrMem_trig : tchcrMem;\n",
     "   type tchcrMem is array(0 to 15) of unsigned(12 downto 0);\n   signal chcrMem_bus  : tchcrMem := (others => (others => '0'));\n"
     "   signal chcrMem_trig : tchcrMem := (others => (others => '0'));\n"
     "   function zrow(ch : integer range 0 to 7; v : std_logic) return integer is\n"
     "   begin\n      if v = '1' then return ch; else return ch + 8; end if;\n   end function;\n"),
    ("         madrMem_bus_q <= madrMem_bus(bus_channel);\n         madrMem_trig_q <= madrMem_trig(triggerchannel);\n"
     "         bcrMem_bus_q <= bcrMem_bus(bus_channel);\n         bcrMem_trig_q <= bcrMem_trig(triggerchannel);\n"
     "         chcrMem_bus_q <= chcrMem_bus(bus_channel);\n         chcrMem_trig_q <= chcrMem_trig(triggerchannel);\n",
     "         madrMem_bus_q <= madrMem_bus(zrow(bus_channel, madrValid(bus_channel)));\n"
     "         madrMem_trig_q <= madrMem_trig(zrow(triggerchannel, madrValid(triggerchannel)));\n"
     "         bcrMem_bus_q <= bcrMem_bus(zrow(bus_channel, bcrValid(bus_channel)));\n"
     "         bcrMem_trig_q <= bcrMem_trig(zrow(triggerchannel, bcrValid(triggerchannel)));\n"
     "         chcrMem_bus_q <= chcrMem_bus(zrow(bus_channel, chcrValid(bus_channel)));\n"
     "         chcrMem_trig_q <= chcrMem_trig(zrow(triggerchannel, chcrValid(triggerchannel)));\n"),
    ("   bus_MADR  <= madrMem_bus_q     when madrValid(bus_channel) = '1'    else (others => '0');\n"
     "   bus_BCR   <= bcrMem_bus_q      when bcrValid(bus_channel) = '1'     else (others => '0');\n"
     "   trig_MADR <= madrMem_trig_q when madrValid(triggerchannel) = '1' else (others => '0');\n"
     "   trig_BCR  <= bcrMem_trig_q  when bcrValid(triggerchannel) = '1'  else (others => '0');\n"
     "   bus_CHCRs  <= chcrMem_bus_q     when chcrValid(bus_channel) = '1'    else (others => '0');\n"
     "   trig_CHCRs <= chcrMem_trig_q when chcrValid(triggerchannel) = '1' else (others => '0');\n",
     "   bus_MADR  <= madrMem_bus_q;   -- lab-dma zero-row: invalid channels read a zero row\n"
     "   bus_BCR   <= bcrMem_bus_q;\n   trig_MADR <= madrMem_trig_q;\n   trig_BCR  <= bcrMem_trig_q;\n"
     "   bus_CHCRs  <= chcrMem_bus_q;\n   trig_CHCRs <= chcrMem_trig_q;\n"),
]

MADR_STEP = [
    ("   signal chcrMem_bus_q : unsigned(12 downto 0);\n",
     "   signal chcrMem_bus_q : unsigned(12 downto 0);\n"
     f"   signal madr_pm4      : unsigned(23 downto 0);   -- {TAG} madr-step: D_MADR +4 / -4, one adder\n"
     "   signal madr_stepv    : unsigned(23 downto 0);\n"),
    ("   bus_channel <= to_integer(unsigned(bus_addr(6 downto 4)));\n",
     "   bus_channel <= to_integer(unsigned(bus_addr(6 downto 4)));\n"
     "   madr_stepv <= (23 downto 3 => directionNeg, 2 => '1', 1 downto 0 => '0');\n"
     "   madr_pm4 <= dmaSettings.D_MADR + madr_stepv;\n"),
    ("                        dmaSettings.D_MADR <= dmaSettings.D_MADR + 4;\n                        dmaState           <= WORKING; ",
     "                        dmaSettings.D_MADR <= madr_pm4;\n                        dmaState           <= WORKING; "),
    ("                     if (directionNeg = '1')  then \n                        dmaSettings.D_MADR <= dmaSettings.D_MADR - 4;\n"
     "                     else\n                        dmaSettings.D_MADR <= dmaSettings.D_MADR + 4;\n                     end if;\n",
     "                     dmaSettings.D_MADR <= madr_pm4;\n"),
]

PAIRS = {'zero-row': ZERO_ROW, 'madr-step': MADR_STEP}

# Planted faults on the transformed text: (part, old, new)
FAULTS = {
    'zero-row-no-offset': ('zero-row', "if v = '1' then return ch; else return ch + 8; end if;", "return ch;"),
    'zero-row-trig-bus-valid': ('zero-row', "madrMem_trig(zrow(triggerchannel, madrValid(triggerchannel)))",
                                "madrMem_trig(zrow(triggerchannel, madrValid(bus_channel)))"),
    'madr-step-sign': ('madr-step', "(23 downto 3 => directionNeg,", "(23 downto 3 => '0',"),
    'madr-step-header-dir': ('madr-step', "                        dmaSettings.D_MADR <= madr_pm4;\n                        dmaState           <= WORKING; ",
                             "                        dmaSettings.D_MADR <= dmaSettings.D_MADR - 4;\n                        dmaState           <= WORKING; "),
}


def dma_text(text, parts=PARTS):
    if TAG in text:
        raise ValueError(f'{TAG}: already applied')
    for part in parts:
        for old, new in PAIRS[part]:
            text = _once(text, old, new)
    return text


def fault_text(text, fault):
    _, old, new = FAULTS[fault]
    return _once(text, old, new)


def prepare(source, output, parts, seed=None):
    import prepare_save_checksum as cs
    return cs.prepare(source, output, seed, {'dma.vhd': lambda t: dma_text(t, parts)})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--parts', default=','.join(PARTS))
    parser.add_argument('--seed', type=int)
    args = parser.parse_args()
    parts = [p for p in args.parts.split(',') if p]
    if any(p not in PARTS for p in parts):
        parser.error(f'parts: {PARTS}')
    try:
        seed, changed = prepare(args.source, args.output, parts, args.seed)
    except ValueError as error:
        raise SystemExit(str(error))
    print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed}), parts {",".join(parts)}')
    for path, (old, new) in changed.items():
        print(f'{TAG}: patched {path} (sha256 {old[:16]} -> {new[:16]})')
