"""gpu_poly colour/texture stepping through one adder per channel.

On top of gpu-recip (prepare_gpu_recip.py, 20-bit colour and texture
arithmetic), each of R, G, B, U, V has three adders: base += product
(CALCCOLOR4/CALCTEXTURE4), work += product (CALCLINEADD) and work += dx
(PROCPIXELS, one pixel per clock). The three never run in the same state, so
one adder per channel serves them all with muxed operands: A is base in the
two setup states and work otherwise, B is dx in PROCPIXELS and the product
otherwise. Every register takes the same value in the same clock as before
(cycle-identical, so pixel throughput is unchanged). Opt-in
(prepare_pocket_dualshock_fit.py --gpu-poly-stepadd, needs --gpu-recip).
"""
from pathlib import Path

from prepare_gpu_recip import _replace

CHANNELS = 'RGBUV'


def stepadd_poly_text(text):
    w = 'gpu_poly'
    marker = "   signal work_V              : unsigned(19 downto 0) := (others => '0'); \n"
    text = _replace(text, marker, marker +
                    '   -- gpu-poly-stepadd: one adder per channel for base and work\n'
                    "   signal stepFromBase        : std_logic;\n"
                    "   signal stepAddDx           : std_logic;\n" +
                    ''.join(f'   signal stepA_{c}, stepB_{c}, sum_{c} : unsigned(19 downto 0);\n' for c in CHANNELS), what=w)
    marker = '   mulResult32clk <= unsigned(mulProduct(19 downto 0));\n'
    adders = ''.join(f"   stepA_{c} <= base_{c} when stepFromBase = '1' else work_{c};\n"
                     f"   stepB_{c} <= dx{c} when stepAddDx = '1' else mulResult32clk;\n"
                     f"   sum_{c}   <= stepA_{c} + stepB_{c};\n" for c in CHANNELS)
    text = _replace(text, marker, marker + '\n'
                    "   stepFromBase <= '1' when (state = CALCCOLOR4 or state = CALCTEXTURE4) else '0';\n"
                    "   stepAddDx    <= '1' when state = PROCPIXELS else '0';\n" + adders, what=w)
    for c in CHANNELS:
        text = _replace(text, f'base_{c} <= base_{c} + mulResult32clk;', f'base_{c} <= sum_{c};', count=2, what=w)
        text = _replace(text, f'work_{c} <= work_{c} + mulResult32clk;', f'work_{c} <= sum_{c};', what=w)
        text = _replace(text, f'work_{c} <= work_{c} + dx{c};', f'work_{c} <= sum_{c};', what=w)
    return text


def apply_project(project):
    """Rewrite project/gpu-recip/gpu_poly.vhd (written by prepare_gpu_recip)."""
    path = Path(project) / 'gpu-recip/gpu_poly.vhd'
    path.write_text(stepadd_poly_text(path.read_text()))
    return dict(path=str(path))
