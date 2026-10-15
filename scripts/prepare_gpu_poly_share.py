"""Share the polygon edge-stepping and half-setup adders in gpu_poly.

xStart/xEnd step by +/-xStep in three states, always subtracting exactly when
decMode = 1 (PREPAREDECMODE is entered only with decMode = 1), so each now
uses one add/subtract with a conditional complement and carry-in. The first
and second polygon halves compute `baseCoord + vo/vp_diff_base` and the edge
start from vt(vo)/vt(1+vp) in mutually exclusive branches of PREPAREHALF; one
adder serves both. Every assignment receives the same value in the same cycle.
"""
import json
from pathlib import Path
import re

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError(f'gpu_poly source changed ({text.count(old)} matches): ' + old.strip()[:70])
    return text.replace(old, new)


def poly_share_text(text):
    text = _replace(text, '   signal xStepEnd            : signed(44 downto 0) := (others => \'0\');\n',
                    '''   signal xStepEnd            : signed(44 downto 0) := (others => '0');
   -- Shared edge stepping: subtract exactly when decMode = 1.
   signal xStepSub            : std_logic;
   signal xStepMask           : signed(44 downto 0);
   signal xStepOne            : signed(1 downto 0);
   signal xStartNext          : signed(44 downto 0);
   signal xEndNext            : signed(44 downto 0);
   -- Shared half setup: first half uses vo, second half vp.
   signal halfDiffBase        : signed(44 downto 0);
   signal halfDiff            : integer range -4096 to 4095;
   signal halfCoord           : signed(44 downto 0);
   signal halfX               : integer range -2048 to 2047;
   signal halfStart           : signed(44 downto 0);
''')
    marker = 'begin \n'
    if marker not in text: marker = 'begin\n'
    concurrent = '''
   xStepSub   <= '1' when decMode = 1 else '0';
   xStepMask  <= (others => xStepSub);
   xStepOne   <= '0' & xStepSub;
   xStartNext <= xStart + (xStepStart xor xStepMask) + xStepOne;
   xEndNext   <= xEnd   + (xStepEnd   xor xStepMask) + xStepOne;
   halfDiff     <= vo_diff when firsthalf = '1' else vp_diff;
   halfDiffBase <= resize(halfDiff * baseStep, 45);
   halfCoord    <= baseCoord + halfDiffBase;
   halfX        <= vt(vo).x when firsthalf = '1' else vt(1 + vp).x;
   halfStart    <= HALFSTART;
'''
    arch = text.index('architecture')
    pos = text.index('\nbegin', arch) + len('\nbegin') 
    text = text[:pos] + '\n' + concurrent + text[pos:]
    for old in ('xStart <= xStart - xStepStart;', 'xStart <= xStart + xStepStart;'):
        text = text.replace(old, 'xStart <= xStartNext;')
    for old in ('xEnd   <= xEnd   - xStepEnd;', 'xEnd   <= xEnd   + xStepEnd;'):
        text = text.replace(old, 'xEnd   <= xEndNext;')
    if text.count('xStart <= xStartNext;') != 5 or text.count('xEnd   <= xEndNext;') != 5:
        raise ValueError('gpu_poly edge stepping changed')
    calc1 = re.findall(r'^                     calc1      := (.*);\n', text, re.M)
    calc3 = re.findall(r'^                     calc3      := (.*);\n', text, re.M)
    if len(calc1) != 1 or len(calc3) != 1 or calc1[0].replace('vt(vo).x', 'X') != calc3[0].replace('vt(1 + vp).x', 'X'):
        raise ValueError('gpu_poly half setup changed')
    text = text.replace('HALFSTART', calc1[0].replace('vt(vo).x', 'halfX'))
    text = _replace(text, '                     calc1      := ' + calc1[0] + ';\n', '                     calc1      := halfStart;\n')
    text = _replace(text, '                     calc3      := ' + calc3[0] + ';\n', '                     calc3      := halfStart;\n')
    text = _replace(text, '                     calc2      := baseCoord + vo_diff_base;\n', '                     calc2      := halfCoord;\n')
    text = _replace(text, '                     calc4      := baseCoord + vp_diff_base;\n', '                     calc4      := halfCoord;\n')
    # Edge slope dividends: (dx << 32) +/- (dy - 1). The low 32 bits of
    # dx << 32 are zero and |dy - 1| < 2**13, so the 45-bit add reduces to the
    # sign-extended small term plus a borrow into the 13-bit integer part.
    for n in (1, 2, 3):
        block = (f'                  if    (dx{n} < 0) then dx{n} := dx{n} - (dy{n} - 1); \n'
                 f'                  elsif (dx{n} > 0) then dx{n} := dx{n} + (dy{n} - 1); end if;\n')
        if text.count(block) != 1: raise ValueError('gpu_poly slope rounding changed')
        text = text.replace(block, f'                  dx{n} := slopeDividend(dx{n}, dy{n});\n')
    function = """
   -- Equal to: d < 0 -> d - (dy - 1); d > 0 -> d + (dy - 1); else d, where d = high << 32.
   function slopeDividend(d : signed(44 downto 0); dy : signed(12 downto 0)) return signed is
      variable term : signed(13 downto 0);
      variable high : signed(12 downto 0);
      variable result : signed(44 downto 0);
   begin
      term := (others => '0');
      if    (d < 0) then term := -resize(dy - 1, 14);
      elsif (d > 0) then term :=  resize(dy - 1, 14); end if;
      high := d(44 downto 32);
      if (term < 0) then high := high - 1; end if;
      result(44 downto 32) := high;
      result(31 downto 0)  := resize(term, 32);
      return result;
   end function;
"""
    arch = text.index('architecture')
    sig = text.index('\n   signal', arch)
    text = text[:sig] + '\n' + function + text[sig:]
    # The per-half edge offsets are formed when PREPAREHALF needs them: vo_diff,
    # vp_diff and baseStep are only written in CALCBOUNDARY2, which waits for
    # the divider batch, so the stored products equal the late ones.
    for half in ('vo', 'vp'):
        text = _replace(text, f'                  {half}_diff_base <= resize({half}_diff * baseStep, 45);\n', '')
    # One gradient multiplexer for both the per-line and the setup multiplies.
    start = text.index('   lineGradient <= dxR when lineMulStep=0')
    end = text.index('   mulIn2 <= lineCoordinate when state=CALCLINE else')
    if 'mulIn1 <= lineGradient when state=CALCLINE else' not in text[start:end]:
        raise ValueError('gpu_poly multiplier inputs changed')
    lc_start = text.index('   lineCoordinate <=', start)
    line_coordinate = text[lc_start:text.index('   mulIn1 <=', lc_start)]
    shared = """   gradientIndex <= lineMulStep when state = CALCLINE else
                    6 when (state = CALCTEXTURE4 and mulStep = 0) else
                    8 when (state = CALCTEXTURE4 and mulStep = 1) else
                    7 when (state = CALCTEXTURE4 and mulStep = 2) else
                    9 when (state = CALCTEXTURE4 and mulStep = 3) else
                    0 when (mulStep = 0) else
                    2 when (mulStep = 1) else
                    4 when (mulStep = 2) else
                    1 when (mulStep = 3) else
                    3 when (mulStep = 4) else
                    5;
   lineGradient <= dxR when gradientIndex=0 else dyR when gradientIndex=1 else
                   dxG when gradientIndex=2 else dyG when gradientIndex=3 else
                   dxB when gradientIndex=4 else dyB when gradientIndex=5 else
                   dxU when gradientIndex=6 else dyU when gradientIndex=7 else
                   dxV when gradientIndex=8 else dyV;
""" + line_coordinate + """   mulIn1 <= lineGradient;

"""
    text = text[:start] + shared + text[end:]
    text = _replace(text, '   signal lineGradient        : unsigned(31 downto 0);\n',
                    '   signal lineGradient        : unsigned(31 downto 0);\n   signal gradientIndex       : integer range 0 to 9;\n')
    return text


def apply_gpu_poly_share(manifest):
    target = manifest['targets']['psx_top']
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'gpu_poly.vhd')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'gpu-poly-share'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'gpu_poly.vhd'; path.write_text(poly_share_text(previous.read_text()))
    target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
        original=str(previous), original_sha256=digest(previous), transform='Shared edge-step and half-setup adders')
    qsf = directory / (target['top'] + '.qsf'); q = qsf.read_text()
    if q.count(str(previous)) != 1: raise ValueError('Expected one gpu_poly assignment')
    qsf.write_text(q.replace(str(previous), str(path))); target['project_sha256']['.qsf'] = digest(qsf)
    target['gpu_poly_share'] = True; manifest['gpu_poly_share'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
