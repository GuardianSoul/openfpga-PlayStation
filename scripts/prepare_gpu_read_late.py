#!/usr/bin/env python3
"""GPU VRAM read setup: remain, next and wrap one clk2x after the request.

In gpu.vhd's IDLE state a VRAM read request computes, in the same clk2x
cycle as vram_RD, the rounded read size, the first burst count and from it
reqVRAMremain/reqVRAMremain2 (burst words still to come), reqVRAMnext (the
second burst past 128 words) and reqVRAMwrap (the burst after a wrap at
x=1024). In full fits that cone, vramState -> rounded size -> compare ->
subtract -> reqVRAMremain/reqVRAMwrap, is the worst clk2x path (-0.24 ns on
some seeds, about 60 % routing at 99.9 % LABs).

Only vram_BURSTCNT and vram_ADDR are needed on the request edge. The other
four registers are first read on a vram_DOUT_READY (READVRAM/READSECOND),
and a memory answers a read at the earliest several clks after accepting
it (vram_psram: ~10; the MiSTer DDR model: SLOWTIMING). So this variant
latches the request's x position and rounded size, and computes the four
registers on the next edge from them: remain = remain2 = BURSTCNT - 1 (true
in both branches), next and wrap as before. The read request itself
(address, burst count, state) is unchanged, cycle for cycle.

Contract: no vram_DOUT_READY in the clk2x cycle right after a read
request. gpu.vhd itself never relied on an earlier answer.
Applies on top of prepare_gpu_timing's bounded read-wrap source (uses
psx_gpu_read_math). A pending setup across a reset is harmless: the four
registers are only read after the next request rewrites them.
"""
import argparse
from pathlib import Path


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('Pinned gpu.vhd changed near: ' + old.strip().splitlines()[0])
    return text.replace(old, new)


def read_late_text(text):
    text = _replace(text, '''   signal reqVRAMnext               : unsigned(7 downto 0);
''', '''   signal reqVRAMnext               : unsigned(7 downto 0);
   -- gpu-read-late: read setup finished one clk after the request
   signal readLatePend              : std_logic := '0';
   signal readLateWrap              : std_logic := '0';
   signal readLateXPos              : unsigned(9 downto 0) := (others => '0');
   signal readLateSize              : unsigned(10 downto 0) := (others => '0');
''')
    text = _replace(text, '''                        if (reqVRAMSizeRounded > 512) then
                           vram_BURSTCNT  <= x"80";
                           reqVRAMremain  <= x"80" - 1;
                           reqVRAMremain2 <= x"80" - 1;
                           reqVRAMnext    <= resize((reqVRAMSizeRounded - 512) / 4, 8);
                        else
                           vram_BURSTCNT  <= std_logic_vector(reqVRAMSizeRounded(9 downto 2));
                           reqVRAMremain  <= reqVRAMSizeRounded(9 downto 2) - 1;
                           reqVRAMremain2 <= reqVRAMSizeRounded(9 downto 2) - 1;
                           reqVRAMnext    <= (others => '0');
                        end if;
                        reqVRAMwrap <= (others => '0');
                        if (vram2vram_reqVRAMEnable = '1' or vram2cpu_reqVRAMEnable = '1') then
                           reqVRAMwrap <= work.psx_gpu_read_math.read_wrap(reqVRAMXPos, reqVRAMSizeRounded);
                        end if;
''', '''                        if (reqVRAMSizeRounded > 512) then
                           vram_BURSTCNT  <= x"80";
                        else
                           vram_BURSTCNT  <= std_logic_vector(reqVRAMSizeRounded(9 downto 2));
                        end if;
                        -- remain, remain2, next and wrap follow on the next clk
                        readLatePend <= '1';
                        readLateWrap <= vram2vram_reqVRAMEnable or vram2cpu_reqVRAMEnable;
                        readLateXPos <= reqVRAMXPos;
                        readLateSize <= reqVRAMSizeRounded;
''')
    text = _replace(text, '''            case (vramState) is
               when IDLE =>
                  if ((ce = '1' or (videoout_reqVRAMEnable = '1' and savestate_busy = '0')) and (vram_WE = '0' or vram_BUSY = '0') and vram_pause = '0') then
''', '''            -- gpu-read-late: the read setup of the request one clk ago (no
            -- vram_DOUT_READY can arrive before it, see prepare_gpu_read_late.py)
            readLatePend <= '0';
            if (readLatePend = '1') then
               reqVRAMremain  <= unsigned(vram_BURSTCNT) - 1;
               reqVRAMremain2 <= unsigned(vram_BURSTCNT) - 1;
               if (readLateSize > 512) then
                  reqVRAMnext <= resize((readLateSize - 512) / 4, 8);
               else
                  reqVRAMnext <= (others => '0');
               end if;
               reqVRAMwrap <= (others => '0');
               if (readLateWrap = '1') then
                  reqVRAMwrap <= work.psx_gpu_read_math.read_wrap(readLateXPos, readLateSize);
               end if;
            end if;

            case (vramState) is
               when IDLE =>
                  if ((ce = '1' or (videoout_reqVRAMEnable = '1' and savestate_busy = '0')) and (vram_WE = '0' or vram_BUSY = '0') and vram_pause = '0') then
''')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.write_text(read_late_text(args.source.read_text()))


if __name__ == '__main__':
    main()
