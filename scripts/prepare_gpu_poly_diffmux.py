"""gpu_poly gradient differences computed after the muldiv input mux.

On top of gpu-recip (prepare_gpu_recip.py), gpu_poly stores fourteen vertex
differences: x and y (13 bits, once per polygon) and R, G, B, U, V (9 bits, in
CALCCOLOR1/CALCTEXTURE1), from fourteen subtractors. Four 10:1 muxes then feed
them to the mul9s pair in CALCCOLOR2/CALCTEXTURE2. The vertices do not change
between those states, and the products are only latched in the *2 states. So
the same values come from muxing the selected field of each of the three
vertices first (the channel for In1, y or x for In2) and subtracting after:
four subtractors, swapped between the two multipliers, and no difference
registers. Every product is the same in every clock it is used:
cycle-identical. Opt-in
(prepare_pocket_dualshock_fit.py --gpu-poly-diffmux, needs --gpu-recip).
"""
import re
from pathlib import Path

from prepare_gpu_recip import _replace, _sub

PAIRS = [f'{c}{p}' for c in 'RGBUVXY' for p in ('10', '21')]


def diffmux_poly_text(text):
    w = 'gpu_poly'
    for name in PAIRS:
        text = _sub(text, rf'   signal diff{name} +: signed\((8|12) downto 0\);[^\n]*\n', '', what=w)
    marker = '   signal muldiv1In1          : signed(8 downto 0);\n'
    text = _replace(text, marker, marker +
                    '   -- gpu-poly-diffmux: select the channel first, subtract after\n'
                    "   signal dmTex, dmEven       : std_logic;\n"
                    "   signal dmV0, dmV1, dmV2    : unsigned(7 downto 0);\n"
                    "   signal dmD10, dmD21        : signed(8 downto 0);\n"
                    "   signal dmP0, dmP1, dmP2    : signed(12 downto 0);\n"
                    "   signal dmE10, dmE21        : signed(12 downto 0);\n", what=w)
    mux = ("   dmTex  <= '1' when (state = CALCTEXTURE2 and mulStep < 4) else '0';\n"
           "   dmEven <= '1' when (mulStep = 0 or mulStep = 2 or mulStep = 4) else '0';\n" +
           ''.join(f"   dmV{k} <= vt({k}).u when (dmTex = '1' and mulStep < 2) else\n"
                   f"            vt({k}).v when dmTex = '1' else\n"
                   f"            vt({k}).r when mulStep < 2 else\n"
                   f"            vt({k}).g when mulStep < 4 else\n"
                   f"            vt({k}).b;\n" for k in range(3)) +
           "   dmD10 <= ('0' & signed(dmV1)) - ('0' & signed(dmV0));\n"
           "   dmD21 <= ('0' & signed(dmV2)) - ('0' & signed(dmV1));\n"
           "   muldiv1In1 <= dmD10 when dmEven = '1' else dmD21;\n" +
           ''.join(f"   dmP{k} <= to_signed(vt({k}).y, 13) when dmEven = '1' else to_signed(vt({k}).x, 13);\n" for k in range(3)) +
           "   dmE10 <= dmP1 - dmP0;\n"
           "   dmE21 <= dmP2 - dmP1;\n")
    text = _sub(text, r'(?s)   muldiv1In1 <= diffU10 when .*?diffB21;\n', lambda m: mux, what=w)
    text = _sub(text, r'(?s)   muldiv2In1 <= diffU21 when .*?diffB10;\n',
                lambda m: "   muldiv2In1 <= dmD21 when dmEven = '1' else dmD10;\n", what=w)
    text = _sub(text, r'(?s)   muldiv1In2 <= diffY21 when .*?diffX10;\n',
                lambda m: "   muldiv1In2 <= dmE21 when dmEven = '1' else dmE10;\n", what=w)
    text = _sub(text, r'(?s)   muldiv2In2 <= diffY10 when .*?diffX21;\n',
                lambda m: "   muldiv2In2 <= dmE10 when dmEven = '1' else dmE21;\n", what=w)
    text = _sub(text, r"                  diff[XY](10|21) <= to_signed\(vt\(\d\)\.[xy] - vt\(\d\)\.[xy], 13\);\n", '', count=4, what=w)
    text = _sub(text, r"                  diff[RGBUV](10|21) <= +\('0' & signed\(vt\(\d\)\.[rgbuv]\)\) - \('0' & signed\(vt\(\d\)\.[rgbuv]\)\);\n",
                '', count=10, what=w)
    if re.search(r'diff[RGBUVXY](10|21)', text): raise ValueError('gpu_poly still reads a difference register')
    return text


def apply_project(project):
    """Rewrite project/gpu-recip/gpu_poly.vhd (written by prepare_gpu_recip)."""
    path = Path(project) / 'gpu-recip/gpu_poly.vhd'
    path.write_text(diffmux_poly_text(path.read_text()))
    return dict(path=str(path))
