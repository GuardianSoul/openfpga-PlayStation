"""prepare --fast-bios: an opt-in "Fast BIOS Boot" setting (docs/pocket-launch-timing.md).

From reset the BIOS copies itself out of ROM with a byte loop run uncached
from ROM (bfc02b68 in SCPH-1001/5501): MiSTer's memorymux waits 25-26 CPU
clocks per BIOS instruction fetch, the 8-bit ROM's speed, so the copy takes
2.6 s of the 5.3 s of black before the Sony screen (launch timing probe,
2026-10-22). With the setting on, a BIOS read ends as soon as its data is in
(memorymux's WAITING countdown is skipped; only BIOS reads use it), but only
until the CPU first fetches an instruction from RAM at 0x10000 or above,
the BIOS shell taking over, about 3 s before the Sony screen. From then
until the next reset every access has MiSTer's timing, so the shell, its
screens and every game run as before; only the BIOS's early start-up, which
no game sees, is faster than on the console.

The switch is psx_top's fastboot input, which this core does not use for
MiSTer's own shell patch (Skip BIOS Logo is psx_bios_patch), so memorymux's
0x18000 patch is disabled and fastboot means "fast BIOS" here. core_top
holds the setting (bridge 0x50000070, written before Reset Exit like the
logo skip) and passes it to the CPU clock domain through two registers.
"""
import re
import shutil
from pathlib import Path

UPSTREAM = Path(__file__).resolve().parents[1]/'references/PSX_MiSTer'
SETTING = 0x50000070

MUX_EDITS = [
    ('   signal bios_page_open         : std_logic;\n',
     '   signal bios_page_open         : std_logic;\n'
     "   signal fast_bios              : std_logic := '1';  -- PSXEarly --fast-bios: BIOS start-up before the shell\n"),
    ("            ext_lastactive   <= '0';\n\n         elsif (ce = '1') then",
     "            ext_lastactive   <= '0';\n            fast_bios        <= '1';  -- PSXEarly --fast-bios\n\n         elsif (ce = '1') then"),
    ('                        if (mem_addressInstr(28 downto 0) < 16#800000#) then -- RAM\n',
     '                        if (mem_addressInstr(28 downto 0) < 16#800000#) then -- RAM\n'
     "                           if (mem_addressInstr(22 downto 16) /= \"0000000\") then fast_bios <= '0'; end if;  -- PSXEarly --fast-bios: the shell runs\n"),
    # Only READBIOS enters WAITING: while fast, its wait ends at once (the
    # read itself still waits for ram_done).
    ("               when WAITING =>\n                  if (waitcnt > 0) then\n",
     "               when WAITING =>\n                  if (waitcnt > 0 and (fastboot = '0' or fast_bios = '0')) then  -- PSXEarly --fast-bios\n"),
    # MiSTer's fastboot shell patch: off (this core's logo skip is psx_bios_patch).
    ("                     if (fastboot = '1' and to_integer(addressBIOS_buf) >= 16#18000#",
     "                     if (false and to_integer(addressBIOS_buf) >= 16#18000#"),
]
def patch_memorymux(text):
    for old, new in MUX_EDITS:
        if text.count(old) != 1: raise ValueError(f'--fast-bios: memorymux changed: {old.strip()[:70]}')
        text = text.replace(old, new)
    return text


def patch_wrapper(text):
    if text.count("        fastboot => '0',\n") != 1: raise ValueError('--fast-bios: wrapper fastboot tie not found')
    text = text.replace("        fastboot => '0',\n", '        fastboot => fast_bios_en,  -- PSXEarly --fast-bios\n')
    head = re.search(r'entity psx_mister_psx_top_resource is\nport \(\n', text)
    if not head: raise ValueError('--fast-bios: wrapper entity port list not found')
    return text[:head.end()] + '    fast_bios_en : in std_logic;  -- PSXEarly --fast-bios\n' + text[head.end():]


def patch_core_top(text):
    old = '    psx_mister_psx_top_resource psx ('
    if text.count(old) != 1: raise ValueError('--fast-bios: core_top psx instance not found')
    text = text.replace(old, old + '.fast_bios_en(fast_bios_sync[1]),')
    anchor = '    reg logo_skip=1\'b0;\n'
    if text.count(anchor) != 1: raise ValueError('--fast-bios: core_top logo_skip register not found')
    return text.replace(anchor, anchor + (
        f"    // --fast-bios: \"Fast BIOS Boot\" (interact.json, bridge 0x{SETTING:08X}, written before\n"
        "    // Reset Exit): memorymux's BIOS start-up reads wait 1 clock until the shell runs.\n"
        "    reg fast_bios_set=1'b0;\n"
        f"    always @(posedge clk_74a) if (bridge_wr && bridge_addr==32'h{SETTING:08X}) fast_bios_set<=|bridge_wr_data;\n"
        "    (* async_reg=\"true\" *) reg [1:0] fast_bios_sync=2'b00;\n"
        "    always @(posedge clk1x) fast_bios_sync<={fast_bios_sync[0],fast_bios_set};\n"))


SDC = ('\n# --fast-bios: the setting reaches clk1x through two registers.\n'
       'set_false_path -to [get_registers {*|fast_bios_sync[0]}]\n')


def prepare_project(out):
    """Patch a frozen project in place: copies of memorymux and the psx_top wrapper, core_top, pocket.sdc."""
    out = Path(out).resolve(); qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
    mux = re.findall(r'"([^"]*/memorymux\.vhd)"', q)
    wrap = re.findall(r'"([^"]*/psx_mister_psx_top_resource\.vhd)"', q)
    core = re.findall(r'"([^"]*/core_top\.sv)"', q)
    if len(mux) != 1 or len(wrap) != 1 or len(core) != 1: raise ValueError('--fast-bios: expected one memorymux, wrapper and core_top in the project')
    folder = out/'fast-bios'; folder.mkdir(exist_ok=True)
    (folder/'memorymux.vhd').write_text(patch_memorymux(Path(mux[0]).read_text()))
    (folder/'psx_mister_psx_top_resource.vhd').write_text(patch_wrapper(Path(wrap[0]).read_text()))
    shutil.copyfile(UPSTREAM/'LICENSE', folder/'COPYING.GPL2')
    Path(core[0]).write_text(patch_core_top(Path(core[0]).read_text()))
    q = q.replace(f'"{mux[0]}"', f'"{folder/"memorymux.vhd"}"').replace(f'"{wrap[0]}"', f'"{folder/"psx_mister_psx_top_resource.vhd"}"')
    qsf_path.write_text(q)
    sdc = out/'pocket.sdc'; sdc.write_text(sdc.read_text() + SDC)


if __name__ == '__main__':
    import sys
    prepare_project(sys.argv[1])
