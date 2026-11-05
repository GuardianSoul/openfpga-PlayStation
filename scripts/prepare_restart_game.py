"""prepare --restart-game: a "Restart Game" action in Core Settings (issue #25).

Pressing it writes bridge 0x50000088 (interact.json action, package
--restart-game). core_top then holds psx_boot_gate's host_run low for 2^16
clk_74a cycles (0.88 ms), so the whole console is reset as at launch: the
CPU, GPU, SPU, CD controller and MDEC start over and the BIOS boots the disc
in the drive. Nothing the launch loaded is reloaded: the BIOS (with Skip BIOS
Logo's patch, which psx_bios_patch made once) and the memory card stay as
they are, and the disc stream, the cue reader and Swap Discs (their resets
are cd_stream_reset, not the console's) keep the disc picked in Swap Discs.
So with a cheat disc that waits for a disc swap, pick the disc and restart.

The hold is a register in clk_74a, like APF's reset_n it replaces, so it
reaches psx_boot_gate's asynchronous reset with no glitch; pocket.sdc gets
the same false path as reset_n.
"""
import re
import sys
from pathlib import Path

ADDRESS = 0x50000088
HOLD_BITS = 16

BLOCK = f'''    // --restart-game: "Restart Game" (interact.json action, bridge 0x{ADDRESS:08X}):
    // the console is reset as at launch while restart_hold is high (2^{HOLD_BITS} clk_74a cycles).
    reg [{HOLD_BITS - 1}:0] restart_count={HOLD_BITS}'d0;
    reg restart_hold=1'b0;
    always @(posedge clk_74a) begin
        if (bridge_wr && bridge_addr==32'h{ADDRESS:08X}) restart_count<={{{HOLD_BITS}{{1'b1}}}};
        else if (|restart_count) restart_count<=restart_count-1'b1;
        restart_hold<=|restart_count;
    end
'''

SDC = ('\n# --restart-game: the Restart Game hold reaches psx_boot_gate\'s reset pipe as reset_n does.\n'
       'set_false_path -from [get_registers {*|restart_hold}] -to [get_registers {*|*boot_gate*|reset_pipe[*]}]\n')


def patch_core_top(text):
    gate = re.findall(r'\.host_run\(reset_n\),\.bios_ready\(', text)
    if len(gate) != 1: raise ValueError('--restart-game: psx_boot_gate host_run(reset_n) not found once')
    text = text.replace('.host_run(reset_n),.bios_ready(', '.host_run(reset_n && !restart_hold),.bios_ready(', 1)
    anchor = '    psx_boot_gate boot_gate ('
    if text.count(anchor) != 1: raise ValueError('--restart-game: psx_boot_gate instance not found')
    return text.replace(anchor, BLOCK + anchor, 1)


def prepare_project(out):
    """Patch a frozen project's core_top and pocket.sdc in place."""
    out = Path(out).resolve()
    q = (out/'apf_top.qsf').read_text()
    cores = re.findall(r'SYSTEMVERILOG_FILE "([^"]*/core_top\.sv)"', q)
    if len(cores) != 1: raise ValueError('--restart-game: expected one core_top in the project')
    core = Path(cores[0])
    core.write_text(patch_core_top(core.read_text()))
    sdc = out/'pocket.sdc'
    sdc.write_text(sdc.read_text() + SDC)


if __name__ == '__main__':
    prepare_project(sys.argv[1])
