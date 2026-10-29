"""prepare --turbo-setting: a "Turbo" core setting, Off (default) / On (package --turbo-setting).

MiSTer's PSX core offers turbo modes that skip PS1 wait states; BR2's
alternating-field ghosting is gone with them on (hardware test 2026-10-28, BR2Turbo2
test core). On is MiSTer's "Turbo: High" without the GTE part:
  TURBO_MEM  -> memorymux skips its PS1 RAM read/write waits; psx_top derives
                ignoreDMACDTiming from it, as upstream;
  TURBO_COMP -> the CPU drops its load/decode stalls and DMA its REP pacing.
The GTE keeps TURBO '0' (psx_top's igte port is tied in a copy): the Laxer GTE
adapter asserts TURBO='0' and has no turbo timing. TURBO_CACHE/TURBO_CACHE50
stay '0' (the turbo data cache is removed; the CPU asserts both off).
Off drives both ports '0', the ties every other build has, so a game sees
exactly the PS1 timing of a build without the setting.

core_top holds the setting (bridge 0x50000080, the next free setting address
after the Analogizer's 0x50000078/0x5000007C) and passes it to clk1x through
two registers, like the Fast BIOS Boot setting. It may change while a game
runs; the four inputs it drives are plain wait-skip conditions.
"""
import json
import re
from pathlib import Path

SETTING = 0x50000080
MARK = '-- PSXEarly --turbo-setting'


def sub(text, old, new, what, count=1):
    if text.count(old) != count:
        raise ValueError(f'--turbo-setting: {what} anchor found {text.count(old)} times, expected {count}')
    return text.replace(old, new)


def patch_wrapper(text):
    text = sub(text, "        TURBO_MEM => '0',\n", f'        TURBO_MEM => turbo_en,  {MARK}\n', 'wrapper TURBO_MEM tie')
    text = sub(text, "        TURBO_COMP => '0',\n", f'        TURBO_COMP => turbo_en,  {MARK}\n', 'wrapper TURBO_COMP tie')
    for name in ('TURBO_CACHE', 'TURBO_CACHE50'):
        sub(text, f"        {name} => '0',\n", f"        {name} => '0',\n", f'wrapper {name} tie (stays off)')
    head = re.search(r'entity psx_mister_psx_top_resource is\nport \(\n', text)
    if not head:
        raise ValueError('--turbo-setting: wrapper entity port list not found')
    return text[:head.end()] + f'    turbo_en : in std_logic;  {MARK}\n' + text[head.end():]


def patch_top(text):
    head = '   igte : entity work.gte\n   port map\n   (\n'
    if text.count(head) != 1:
        raise ValueError(f'--turbo-setting: psx_top igte instance found {text.count(head)} times, expected 1')
    start = text.index(head); end = text.index(');', start)
    body = sub(text[start:end], '      TURBO                => TURBO_COMP,\n',
               f"      TURBO                => '0',   {MARK}: the Laxer GTE adapter has no turbo\n", 'igte TURBO')
    return text[:start] + body + text[end:]


def patch_core_top(text):
    inst = '    psx_mister_psx_top_resource psx ('
    text = sub(text, inst,
               f"    // --turbo-setting: \"Turbo\" (interact.json, bridge 0x{SETTING:08X}): Off (0, default) / On.\n"
               "    reg turbo_set=1'b0;\n"
               f"    always @(posedge clk_74a) if (bridge_wr && bridge_addr==32'h{SETTING:08X}) turbo_set<=|bridge_wr_data;\n"
               "    (* async_reg=\"true\" *) reg [1:0] turbo_sync=2'b00;\n"
               "    always @(posedge clk1x) turbo_sync<={turbo_sync[0],turbo_set};\n"
               + inst + '.turbo_en(turbo_sync[1]),', 'core_top psx instance')
    return text


SDC = ('\n# --turbo-setting: the Turbo setting reaches clk1x through two registers.\n'
       'set_false_path -to [get_registers {*|turbo_sync[0]}]\n')


def prepare_project(out):
    """Patch a prepared project: copies of the psx_top wrapper and psx_top, core_top in place, pocket.sdc."""
    out = Path(out).resolve(); qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
    picks = {name: re.findall(r'"([^"]*/' + re.escape(name) + r')"', q)
             for name in ('psx_mister_psx_top_resource.vhd', 'psx_top.vhd', 'core_top.sv')}
    if any(len(v) != 1 for v in picks.values()):
        raise ValueError(f'--turbo-setting: expected one wrapper, psx_top and core_top, found {picks}')
    folder = out/'turbo-setting'; folder.mkdir(exist_ok=True)
    for name, fn in (('psx_mister_psx_top_resource.vhd', patch_wrapper), ('psx_top.vhd', patch_top)):
        copy = folder/name
        copy.write_text(fn(Path(picks[name][0]).read_text()))
        q = q.replace(f'"{picks[name][0]}"', f'"{copy}"')
    core = Path(picks['core_top.sv'][0]); core.write_text(patch_core_top(core.read_text()))
    qsf_path.write_text(q)
    sdc = out/'pocket.sdc'; sdc.write_text(sdc.read_text() + SDC)
    manifest = out/'manifest.json'
    if manifest.exists():
        m = json.loads(manifest.read_text())
        m['turbo_setting'] = dict(bridge_address=f'0x{SETTING:08X}', default='Off',
                                  live_ports={'TURBO_MEM': 'turbo_en', 'TURBO_COMP': 'turbo_en'},
                                  fixed_ports={'TURBO_CACHE': "'0'", 'TURBO_CACHE50': "'0'", 'igte.TURBO': "'0'"},
                                  off_equals_fixed_zero=True, hardware_verified=False)
        manifest.write_text(json.dumps(m, indent=2) + '\n')
    return folder


if __name__ == '__main__':
    import sys
    prepare_project(sys.argv[1])
