#!/usr/bin/env python3
"""Run a synthetic CPU/CD/DMA/IRQ-pending integration check without game assets."""
import subprocess
import sys

from reference_cd_dma_program import write_program
from run_cd_controller import synthetic_sector
from tool_paths import ROOT


def main():
    directory=ROOT/'build/sim/system-cd-dma';directory.mkdir(parents=True,exist_ok=True)
    program=write_program(directory/'inputs')
    image=directory/'inputs/synthetic.bin'
    image.write_bytes(b''.join(synthetic_sector(lba) for lba in range(12)))
    cue=directory/'inputs/synthetic.cue'
    cue.write_text('FILE "synthetic.bin" BINARY\nTRACK 01 MODE2/2352\nINDEX 01 00:00:00\n')
    subprocess.run([sys.executable,str(ROOT/'scripts/run_system_disc.py'),'--bios',str(program['bios']),
        '--cue',str(cue),'--expected',str(program['expected']),'--trace-devices','--output',str(directory),
        '--max-cycles','64000000','--timeout','1200'],cwd=ROOT,check=True)


if __name__=='__main__':main()
