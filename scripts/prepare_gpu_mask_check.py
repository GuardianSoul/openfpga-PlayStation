#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""GP0 E6 check-mask on CPU->VRAM uploads and VRAM->VRAM copies, as on a PS1.

ps1-tests gpu/mask-bit testCheckMaskBit: a pixel uploaded over a masked pixel
must not replace it; gpu_cpu2vram ignores the check ("todo: AND/OR masking").
gpu_vram2vram does check, but looks at bit 15 of the SOURCE pixel. Beetle
(Command_FBWrite, Command_FBCopy) and the PS1 test look at the DESTINATION.

One check at the VRAM write port serves both. A word they queue while check-mask
is on carries a flag; before it is written its VRAM word is read (not stored
anywhere but four flip-flops) and the byte enables of masked pixels are cleared.
Writes before it are already in the controller's queue, so the read sees them,
as Beetle's per-pixel fetch does. Drawing and fills check through the pixel
pipeline and carry no flag; with check-mask off nothing changes.

ps1-tests gpu/vram-to-vram-overlap and Beetle: a copy down onto itself reads
rows this copy has already written. gpu_vram2vram reads the next row while those
writes are still queued, so it sees a mix. When the destination is below the
source by less than the height (mask on or off), each row now waits for the
writes, through the existing REQUESTFIRST state; other copies don't wait.

Guest-visible: right pixels, and with check-mask on one extra VRAM read per word
written. Usage: prepare_gpu_mask_check.py PROJECT  (post-prepare step; patches
the project's gpu.vhd and gpu_vram2vram.vhd in place)
"""
import re
import sys
from pathlib import Path

TAG = 'gpu-mask-check'

V_WRITE_OLD = ("                  if (DrawPixelsMask = '0' or vramLineData(15) = '0') then\n"
               "                     pixelWrite <= '1';\n"
               "                  end if;  \n")
V_WRITE_NEW = f"                  pixelWrite <= '1'; -- {TAG}: the write port checks the destination\n"
V_TAIL_OLD = ("                        state <= READVRAM;\n"
              "                     end if;\n"
              "                  end if;\n")
V_TAIL_NEW = ("                        state <= READVRAM;\n"
              f"                        if (dstY /= srcY and resize(dstY - srcY, 10) < heig) then -- {TAG}: the next source row was written, drain first\n"
              "                           state <= REQUESTFIRST;\n"
              "                        end if;\n"
              "                     end if;\n"
              "                  end if;\n")
VRAM2VRAM_EDITS = (('write', V_WRITE_OLD, V_WRITE_NEW), ('tail', V_TAIL_OLD, V_TAIL_NEW))

G_SIGNAL_OLD = "   signal pixel64source             : std_logic := '0';\n"
G_SIGNAL_NEW = (G_SIGNAL_OLD + f"   signal pixel64check              : std_logic := '0'; -- {TAG}\n"
                "   signal maskReady                 : std_logic := '0';\n"
                "   signal maskBits                  : std_logic_vector(3 downto 0) := (others => '0');\n")
G_DOUT_OLD = "   signal fifoOut_Dout              : std_logic_vector(85 downto 0);\n"
G_DOUT_NEW = "   signal fifoOut_Dout              : std_logic_vector(86 downto 0);\n"
G_DIN_SIG_OLD = "   signal fifoOut_Din               : std_logic_vector(85 downto 0);\n"
G_DIN_SIG_NEW = "   signal fifoOut_Din               : std_logic_vector(86 downto 0);\n"
G_WIDTH_OLD = "      DATAWIDTH        => 64 + 17 + 4 + 1,  -- 64bit data + 17 bit address + 4bit word enable + 1bit source=pipeline\n"
G_WIDTH_NEW = "      DATAWIDTH        => 64 + 17 + 4 + 1 + 1,  -- 64bit data + 17 bit address + 4bit word enable + 1bit source=pipeline + 1bit check mask\n"
G_DIN_OLD = "         fifoOut_Din  <= pixel64source & pixel64wordEna & pixel64Addr & pixel64data;\n"
G_DIN_NEW = "         fifoOut_Din  <= pixel64check & pixel64source & pixel64wordEna & pixel64Addr & pixel64data;\n"
G_FILL_OLD = "               fifoOut_Din   <=  '1' & \"1111\" & std_logic_vector(vramFill_pixelAddr(19 downto 3))"
G_FILL_NEW = "               fifoOut_Din   <=  \"01\" & \"1111\" & std_logic_vector(vramFill_pixelAddr(19 downto 3))"
G_SOURCE_OLD = "               pixel64source <= pipeline_pixelWrite;\n"
G_SOURCE_NEW = G_SOURCE_OLD + "               pixel64check  <= GPUSTAT_DrawPixelsMask and (not pipeline_pixelWrite);\n"
G_RD_OLD = "fifoOut_Empty = '0' and reqVRAMEnable = '0' and vram_pause = '0') else '0';\n"
G_RD_NEW = "fifoOut_Empty = '0' and reqVRAMEnable = '0' and vram_pause = '0' and (fifoOut_Dout(86) = '0' or maskReady = '1')) else '0';\n"
G_STATE_OLD = "      READVRAM,\n      CLEARLINESTART,\n"
G_STATE_NEW = f"      READVRAM,\n      READMASK, -- {TAG}\n      CLEARLINESTART,\n"
G_RESET_OLD = "            vramState   <= IDLE;\n            reqVRAMDone <= '0';\n"
G_RESET_NEW = G_RESET_OLD + "            maskReady   <= '0';\n"
G_WRITE_OLD = ("                     elsif (fifoOut_Empty = '0') then\n"
               "                        if (render24 = '1' or interlaced480pHack = '1') then\n")
G_WRITE_NEW = (f"                     elsif (fifoOut_Empty = '0' and fifoOut_Dout(86) = '1' and maskReady = '0') then -- {TAG}: read the word first\n"
               "                        vramState     <= READMASK;\n"
               "                        vram_RD       <= '1';\n"
               "                        vram_ADDR     <= x\"00\" & fifoOut_Dout(80 downto 64) & \"000\";\n"
               "                        vram_BURSTCNT <= x\"01\";\n"
               "                        reqVRAMStore  <= '0';\n"
               "                     elsif (fifoOut_Empty = '0') then\n"
               "                        maskReady <= '0';\n"
               "                        if (render24 = '1' or interlaced480pHack = '1') then\n")
G_BE_OLD = re.compile(r'( *vram_BE +<= )fifoOut_Dout\(84\) & fifoOut_Dout\(84\) & fifoOut_Dout\(83\) & fifoOut_Dout\(83\) & fifoOut_Dout\(82\) & fifoOut_Dout\(82\) & fifoOut_Dout\(81\) & fifoOut_Dout\(81\);\n')
G_BE_NEW = (r"\1(fifoOut_Dout(84) & fifoOut_Dout(84) & fifoOut_Dout(83) & fifoOut_Dout(83) & fifoOut_Dout(82) & fifoOut_Dout(82) & fifoOut_Dout(81) & fifoOut_Dout(81))"
            "\n                                           and not (maskUse(3) & maskUse(3) & maskUse(2) & maskUse(2) & maskUse(1) & maskUse(1) & maskUse(0) & maskUse(0));\n")
G_MASKUSE_OLD = G_SIGNAL_NEW
G_MASKUSE_NEW = G_SIGNAL_NEW + "   signal maskUse                   : std_logic_vector(3 downto 0);\n"
G_USE_OLD = "   VRAMIdle    <= '1' when (vramState = IDLE and (vram_WE = '0' or vram_BUSY = '0')) else '0';\n"
G_USE_NEW = "   maskUse     <= maskBits when (fifoOut_Dout(86) = '1') else \"0000\";\n" + G_USE_OLD
G_CASE_OLD = "               when READVRAM =>\n                  if (vram_DOUT_READY = '1') then\n"
G_CASE_NEW = (f"               when READMASK => -- {TAG}\n"
              "                  if (vram_DOUT_READY = '1') then\n"
              "                     maskBits  <= vram_DOUT(63) & vram_DOUT(47) & vram_DOUT(31) & vram_DOUT(15);\n"
              "                     maskReady <= '1';\n"
              "                     vramState <= IDLE;\n"
              "                  end if;\n\n" + G_CASE_OLD)
GPU_EDITS = (('signal', G_SIGNAL_OLD, G_MASKUSE_NEW), ('dout', G_DOUT_OLD, G_DOUT_NEW), ('din signal', G_DIN_SIG_OLD, G_DIN_SIG_NEW), ('width', G_WIDTH_OLD, G_WIDTH_NEW),
             ('din', G_DIN_OLD, G_DIN_NEW), ('fill', G_FILL_OLD, G_FILL_NEW), ('source', G_SOURCE_OLD, G_SOURCE_NEW),
             ('rd', G_RD_OLD, G_RD_NEW), ('state', G_STATE_OLD, G_STATE_NEW), ('reset', G_RESET_OLD, G_RESET_NEW),
             ('write', G_WRITE_OLD, G_WRITE_NEW), ('use', G_USE_OLD, G_USE_NEW), ('case', G_CASE_OLD, G_CASE_NEW))
EDITS = {'gpu_vram2vram.vhd': VRAM2VRAM_EDITS, 'gpu.vhd': GPU_EDITS}


def transform(name, text):
    if TAG in text:
        raise ValueError(f'{name} already carries {TAG}')
    for what, old, new in EDITS[name]:
        if text.count(old) != 1:
            raise ValueError(f'{name}: {what}: expected exactly one match, found {text.count(old)}')
        text = text.replace(old, new)
    if name == 'gpu.vhd':
        text, count = G_BE_OLD.subn(G_BE_NEW, text)
        if count != 1:
            raise ValueError(f'{name}: byte enables: expected exactly one match, found {count}')
    return text


def apply(project):
    project = Path(project).resolve()
    qsf = (project / 'apf_top.qsf').read_text()
    done = []
    for name in EDITS:
        found = sorted(set(re.findall(rf'VHDL_FILE "([^"]*/{re.escape(name)})"', qsf)))
        if len(found) != 1:
            raise SystemExit(f'{TAG}: expected one {name}, found {found}')
        path = Path(found[0])
        path.write_text(transform(name, path.read_text()))
        done.append(path)
    return done


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.split('Usage: ')[1])
    for p in apply(sys.argv[1]):
        print('patched', p)
