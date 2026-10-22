"""prepare --bridge-cmd-lean: drop core_bridge_cmd's copies of held inputs (docs/pocket-launch-timing.md).

The APF template's command handler (references/MIRLO64/target/pocket/
core_bridge_cmd.v) copies the core's target command inputs into registers
when a command starts (target_20 id, target_24 slot offset or struct
pointer, target_28 bridge address, target_2C length: 112 live bits) and
keeps the target parameter/response pointers target_4 and target_8 (0x20,
0x40) in registers the host may overwrite (64 bits). It is register-bound
(429 registers in 215 ALMs on probe6b), so copies cost ALMs directly.

In this core every target command comes from psx_cue_mount, whose command
fields (fw_id, fw_off, fw_len, the stream's u_bridge) are registers that
hold from the command's start until it is done (the firmware writes them
only before a command and waits for its end). So the bridge reads them
directly: 0x20 the id, 0x24 the slot offset (read/write) or the struct
pointer (getfile: the response struct, openfile: the parameter struct, by
a 2-bit command kind), 0x28 the bridge address, 0x2C the length. Host reads
see the same values as before while a command runs. Two visible
differences, neither used by APF: 0x28/0x2C during getfile/openfile show
the current inputs instead of the previous read's, and host writes to
0xF8001004/8 are ignored (the pointers stay 0x20/0x40). Same clocks, same
bridge read timing (bridge_rd_data_out is still registered on bridge_rd).

Likewise the data slot request/update ids and sizes become views of the
host parameter words (see VIEWS): equal whenever their strobes are up,
which is when their consumers sample them.
"""
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'references/MIRLO64/target/pocket/core_bridge_cmd.v'

# The data slot request/update ids and sizes: views of the host parameter
# words instead of copies loaded in ST_PARSE. The host writes those words
# before the command word and never during a command (it waits for the
# result in host_0), so they are equal whenever the strobe is up; the
# consumers (psx_bios_loader at the request's rising edge, psx_cd_stream on
# the update pulse) sample them only then.
VIEWS = [('dataslot_requestread_id', '15:0', 'host_20[15:0]'), ('dataslot_requestwrite_id', '15:0', 'host_20[15:0]'),
         ('dataslot_requestwrite_size', '31:0', 'host_24'), ('dataslot_update_id', '15:0', 'host_20[15:0]'),
         ('dataslot_update_size', '31:0', 'host_24')]

EDITS = [
    # the pointers are constants: no host writes
    ("            8'h4: target_4 <= bridge_wr_data_in; // parameter data pointer\n", ''),
    ("            8'h8: target_8 <= bridge_wr_data_in; // response data pointer\n", ''),
    # bridge reads of the parameter words: the held inputs
    ("            8'h20: bridge_rd_data_out <= target_20; // parameter data regs\n",
     "            8'h20: bridge_rd_data_out <= {16'h0, target_dataslot_id}; // parameter data: the held inputs (--bridge-cmd-lean)\n"),
    ("            8'h24: bridge_rd_data_out <= target_24;\n",
     "            8'h24: bridge_rd_data_out <= !t_kind[1] ? target_dataslot_slotoffset : t_kind[0] ? target_buffer_param_struct : target_buffer_resp_struct;\n"),
    ("            8'h28: bridge_rd_data_out <= target_28;\n",
     "            8'h28: bridge_rd_data_out <= target_dataslot_bridgeaddr;\n"),
    ("            8'h2C: bridge_rd_data_out <= target_2C;\n",
     "            8'h2C: bridge_rd_data_out <= target_dataslot_length;\n"),
    # the command kind for 0x24
    ("            target_0[15:0] <= 16'h0180;\n", "            target_0[15:0] <= 16'h0180;\n            t_kind <= 2'd0;\n"),
    ("            target_0[15:0] <= 16'h0184;\n", "            target_0[15:0] <= 16'h0184;\n            t_kind <= 2'd0;\n"),
    ("            target_0[15:0] <= 16'h0190;\n", "            target_0[15:0] <= 16'h0190;\n            t_kind <= 2'd2;\n"),
    ("            target_0[15:0] <= 16'h0192;\n", "            target_0[15:0] <= 16'h0192;\n            t_kind <= 2'd3;\n"),
    ("    reg     [3:0]   tstate;\n",
     "    reg     [3:0]   tstate;\n    reg     [1:0]   t_kind = 2'd0; // --bridge-cmd-lean: 0 read/write, 2 getfile, 3 openfile\n"),
]


def patch(text):
    for name, width, src in VIEWS:
        w = f'[{width}]'.ljust(7)
        for old, new in ((f'output  reg     {w} {name},', f'output  wire    {w} {name},'),
                         (f'            {name} <= {src};\n', '')):
            if text.count(old) != 1: raise ValueError(f'--bridge-cmd-lean: core_bridge_cmd changed: {old.strip()}')
            text = text.replace(old, new)
    marker = '    reg     [3:0]   hstate;\n'
    if text.count(marker) != 1: raise ValueError('--bridge-cmd-lean: hstate declaration changed')
    text = text.replace(marker, marker + ''.join(f'    assign {name} = {src}; // --bridge-cmd-lean: equal whenever its strobe is up\n' for name, width, src in VIEWS))
    for old, new in EDITS:
        if text.count(old) != 1: raise ValueError(f'--bridge-cmd-lean: core_bridge_cmd changed: {old.strip()[:70]}')
        text = text.replace(old, new)
    # the copies are now write-only: drop them so nothing can read a stale one
    for reg in ('target_20', 'target_24', 'target_28', 'target_2C'):
        text, n = re.subn(rf'^\s*{reg} <= [^;]*;[^\n]*\n', '', text, flags=re.M)
        if not n: raise ValueError(f'--bridge-cmd-lean: no writes of {reg}')
        uses = [l for l in text.splitlines() if re.search(rf'\b{reg}\b', l) and not l.lstrip().startswith('reg ')]
        if uses: raise ValueError(f'--bridge-cmd-lean: {reg} still used: {uses[0].strip()}')
    return text


def lean_source(directory):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    out = directory/'core_bridge_cmd.v'
    out.write_text('// Modified by scripts/prepare_bridge_cmd_lean.py (--bridge-cmd-lean): see its docstring.\n' + patch(SOURCE.read_text()))
    return out


def prepare_project(project):
    project = Path(project).resolve(); qsf = project/'apf_top.qsf'; q = qsf.read_text()
    hits = re.findall(r'"([^"]*/core_bridge_cmd\.v)"', q)
    if len(hits) != 1: raise ValueError('--bridge-cmd-lean: expected one core_bridge_cmd.v in the project')
    out = lean_source(project/'bridge-cmd-lean')
    qsf.write_text(q.replace(f'"{hits[0]}"', f'"{out}"'))


if __name__ == '__main__':
    import sys
    prepare_project(sys.argv[1])
