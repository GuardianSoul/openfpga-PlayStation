#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""RAM store row-change cost and SPU DMA request hold as measured on a PS1.

Peip TimingStoreRAM: stores to addresses 1 MiB apart cost about 6.2 clocks
each, as a row change does. memorymux compares bits 20..10, so a store to a
2 MiB mirror counts as a page hit, and a real row change costs 7. This step:

- compares bits 22..10, so only the same page in the same mirror is a hit;
- for a store queued in the write FIFO, waits 2 instead of 3 on a page change, +1
  when the SDRAM row stays the same (bits 20..11, a mirror or the other 1 KiB of
  the 2 KiB row: that service is one clock shorter), +1 on 2 of every 9 changes. The last is a fit to the recorded rows, not a
  known hardware mechanism.

A store that reaches an idle mux directly keeps the old wait of 3. Moving that
one clock breaks DMABLOCKGPU 4 BL LEN 2 and 3 and DMALINKEDLIST 2 BL LEN 0, whose
kernels start from a single store after idle.

Peip DMATimingToDevice SPU16BLOCK16: the SPU DMA request hold (prepare_dma_ps1_
timing --spu-req-delay) goes from 28 to 32. On the native SDRAM path with these
store changes 32 and 33 give 16,755 (PS1 16,607..16,770), 34 gives 16,772.

Guest-visible on purpose: no equivalence claim, the hardware tests decide.
Same-page stores and TURBO are unchanged.
Usage: prepare_store_row_timing.py PROJECT [--spu-req-delay N]
(post-prepare step after dma_io_tail and dma_ps1_timing; memorymux moves to
PROJECT/store-row-timing/, the project's dma.vhd is edited in place)
"""
import argparse
import re
import shutil
from pathlib import Path

TAG = 'store-row-timing'
PAGE_SIGNAL_OLD = '   signal ram_page_addr          : unsigned(10 downto 0);\n'
PAGE_SIGNAL_NEW = (f'   signal ram_page_addr          : unsigned(12 downto 0); -- {TAG}: 22..10, not 20..10\n'
                   f'   signal store_row_misses       : unsigned(3 downto 0) := (others => \'0\'); -- {TAG}\n')
RESET_OLD = "            ram_page_open    <= '0';\n            ext_lastactive   <= '0';\n"
RESET_NEW = "            ram_page_open    <= '0';\n            store_row_misses <= (others => '0'); -- " + TAG + "\n            ext_lastactive   <= '0';\n"
WRITE_OLD = ("                              ram_page_addr <= mem_addressData(20 downto 10);\n"
             "                              if (TURBO = '1' or (ram_page_open = '1' and mem_addressData(20 downto 10) = ram_page_addr)) then\n")
WRITE_NEW = ("                              ram_page_addr <= mem_addressData(22 downto 10);\n"
             "                              if (TURBO = '1' or (ram_page_open = '1' and mem_addressData(22 downto 10) = ram_page_addr)) then\n")
WAIT_OLD = ("                                 if (ram_page_open = '1' and mem_addressData(20 downto 10) /= ram_page_addr) then\n"
            "                                    waitcnt <= 3;\n"
            "                                 end if;\n")
WAIT_NEW = (f"                                 if (ram_page_open = '1' and mem_addressData(22 downto 10) /= ram_page_addr) then -- {TAG}\n"
            "                                    if (writeFifo_Empty = '1') then -- direct, not queued\n"
            "                                       waitcnt <= 3;\n"
            "                                    elsif (mem_addressData(20 downto 11) = ram_page_addr(10 downto 1)) then -- same SDRAM row\n"
            "                                       if (store_row_misses = 3 or store_row_misses = 7) then waitcnt <= 4; else waitcnt <= 3; end if;\n"
            "                                    else\n"
            "                                       if (store_row_misses = 3 or store_row_misses = 7) then waitcnt <= 3; else waitcnt <= 2; end if;\n"
            "                                    end if;\n"
            "                                    if (store_row_misses = 8) then store_row_misses <= (others => '0'); else store_row_misses <= store_row_misses + 1; end if;\n"
            "                                 end if;\n")
MEMORYMUX_EDITS = (('page signal', PAGE_SIGNAL_OLD, PAGE_SIGNAL_NEW), ('reset', RESET_OLD, RESET_NEW),
                   ('page compare', WRITE_OLD, WRITE_NEW), ('row wait', WAIT_OLD, WAIT_NEW))
HOLD_OLD = ("   signal spuReqHeld          : integer range 0 to {0} := 0;",
            "            elsif (spuReqHeld < {0}) then\n",
            "(spu_dmaRequest = '1' and spuReqHeld = {0})",
            "a PS1 answers the SPU's request {0} cycles late")


def memorymux_text(text):
    if TAG in text:
        raise ValueError('memorymux already carries ' + TAG)
    for what, old, new in MEMORYMUX_EDITS:
        if text.count(old) != 1:
            raise ValueError(f'{what}: expected exactly one match, found {text.count(old)}')
        text = text.replace(old, new)
    return text


def dma_text(text, spu_req_delay=32, was=28):
    for old in HOLD_OLD:
        old = old.format(was)
        if text.count(old) != 1:
            raise ValueError(f'SPU hold: expected exactly one {old.strip()!r}, found {text.count(old)}')
    for old in HOLD_OLD:
        text = text.replace(old.format(was), old.format(spu_req_delay))
    return text


def apply(project, spu_req_delay=32):
    project = Path(project).resolve()
    qsf = project / 'apf_top.qsf'
    text = qsf.read_text()
    muxes = re.findall(r'VHDL_FILE "([^"]*/memorymux\.vhd)"', text)
    dmas = sorted(set(re.findall(r'VHDL_FILE "([^"]*/dma\.vhd)"', text)))
    if len(muxes) != 1 or not dmas:
        raise SystemExit(f'{TAG}: expected one memorymux and a dma.vhd, found {muxes} and {dmas}')
    out = project / TAG
    out.mkdir(exist_ok=False)
    source = Path(muxes[0])
    new = out / 'memorymux.vhd'
    new.write_text(memorymux_text(source.read_text()))
    if (source.parent / 'LICENSE').exists():
        shutil.copy2(source.parent / 'LICENSE', out / 'LICENSE')
    qsf.write_text(text.replace(muxes[0], str(new)))
    for dma in dmas:
        Path(dma).write_text(dma_text(Path(dma).read_text(), spu_req_delay))
    return [new] + [Path(d) for d in dmas]


if __name__ == '__main__':
    ap = argparse.ArgumentParser(usage=__doc__.split('Usage: ')[1])
    ap.add_argument('project')
    ap.add_argument('--spu-req-delay', type=int, default=32)
    a = ap.parse_args()
    for p in apply(a.project, a.spu_req_delay):
        print('patched', p)
