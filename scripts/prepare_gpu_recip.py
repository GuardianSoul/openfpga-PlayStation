"""GPU gradient divisions through one reciprocal and a pipelined multiply.

Upstream gives the GPU six 46-clock restoring dividers (45-bit dividend,
25-bit divisor). gpu_poly uses three for the edge slopes and then all six,
twice, for the colour and texture gradients, which all divide by the same
denom; gpu_line uses five for one line, three of them for colour steps that
all divide by points. Here (rtl/pocket/psx_gpu_recip_unit.vhd) one reciprocal
of the shared divisor starts with the edge divisions and the gradients then
come out of a pipelined multiply with one correction step, one per clock,
bit-exact with the upstream quotient(31 downto 0) (proof in the unit).
Three dividers remain, with an 11-bit divisor (edge dy < 512, line points
and |dy| < 1024 after culling); gpu_line's points/|dy| division moves from
the sixth divider to the third, whose colour division it no longer needs.

Pixels and memory traffic are unchanged. Each Gouraud or textured triangle
finishes setup earlier: the gradients wait about 5 clocks after the last
numerator instead of 46 (twice for a Gouraud textured triangle). Lines keep
their timing: the reciprocal (33 clocks) and colour steps finish before the
46-clock slope dividers. Opt-in (prepare_pocket_dualshock_fit.py --gpu-recip).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / 'rtl/pocket/psx_gpu_recip_unit.vhd'


def _replace(text, old, new, count=1, what='GPU'):
    if text.count(old) != count:
        raise ValueError(f'{what} source changed ({text.count(old)} matches): ' + old.strip()[:80])
    return text.replace(old, new)


def _sub(text, pattern, new, count=1, what='GPU'):
    text, n = re.subn(pattern, new, text)
    if n != count:
        raise ValueError(f'{what} source changed ({n} matches): ' + pattern[:80])
    return text


def _ports(line):
    ports = '''      -- gpu-recip: shared-divisor gradient unit (psx_gpu_recip_unit)
      recipStart           : out std_logic := '0';
      recipDivisor         : out signed(20 downto 0);
      gradValid            : out std_logic := '0';
      gradN                : out signed(20 downto 0) := (others => '0');
      gradIdx              : out unsigned(3 downto 0) := (others => '0');
      gradOutValid         : in  std_logic;
      gradOutIdx           : in  unsigned(3 downto 0);
      gradOutQ             : in  signed(31 downto 0);
'''
    if line:
        ports += '      recipReady           : in  std_logic;\n'
    return ports


def recip_poly_text(text):
    w = 'gpu_poly'
    text = _sub(text, r'(      div6\s*: inout div_type;[ \t]*\n)', lambda m: m.group(1) + _ports(False), what=w)
    text = _replace(text, '   signal denom               : integer range -16777216 to 16777215;\n',
                    '   signal denom               : integer range -16777216 to 16777215;\n'
                    '   signal recipStartReg       : std_logic := \'0\';\n', what=w)
    # The unit samples the divisor with the start pulse (denom is registered with it).
    marker = '   -- synthesis translate_off\n   assert REPRODUCIBLEGPUTIMING'
    text = _replace(text, marker,
                    '   recipStart   <= recipStartReg;\n'
                    '   recipDivisor <= resize(to_signed(denom, 25), 21) when recipStartReg = \'1\' else (others => \'0\');\n\n'
                    + marker, what=w)
    text = _sub(text, r"            div6\.start           <= '0';\n[ \t]*\n(            if \(state /= CALCCOLOR2 and state /= CALCTEXTURE2\) then)",
                lambda m: '''            div6.start           <= '0';
            recipStartReg        <= '0';
            gradValid            <= '0';
            dyWe                 <= '0';

            -- gpu-recip: gradients from the shared unit, in index order
            -- (0 dxR, 1 dyR, 2 dxG, 3 dyG, 4 dxB, 5 dyB, 6 dxU, 7 dyU, 8 dxV, 9 dyV)
            if (gradOutValid = '1' and (state = CALCCOLOR2 or state = CALCCOLOR3 or state = CALCTEXTURE2 or state = CALCTEXTURE3)) then
               case (to_integer(gradOutIdx)) is
                  when 0 => dxR <= unsigned(gradOutQ);
                  when 2 => dxG <= unsigned(gradOutQ);
                  when 4 => dxB <= unsigned(gradOutQ);
                  when 6 => dxU <= unsigned(gradOutQ);
                  when 8 => dxV <= unsigned(gradOutQ);
                  when others =>
                     dyWe <= '1'; dyWaddr <= to_integer(gradOutIdx) / 2; dyWdata <= unsigned(gradOutQ);
               end case;
               if (gradOutIdx = 7) then
                  if (gradOutQ = 0) then dyU_zero <= '1'; else dyU_zero <= '0'; end if;
               end if;
               if (gradOutIdx = 9) then
                  if (unsigned(gradOutQ) = 4096) then dyV_4096 <= '1'; else dyV_4096 <= '0'; end if;
               end if;
            end if;

'''+m.group(1), what=w)
    text = _replace(text, '''                  div3.divisor   <= x"000" & dy3;
                  ''', '''                  div3.divisor   <= x"000" & dy3;
                  recipStartReg  <= '1';
                  ''', what=w)
    text = _replace(text, '''                     div3.start <= '0';
                     if (nextQuad = '1') then''', '''                     div3.start <= '0';
                     recipStartReg <= '0';
                     if (nextQuad = '1') then''', what=w)

    grad = 'gradValid <= \'1\'; gradN <= resize(muldivResult1clk - muldivResult2clk, 21);'
    for state, lanes, base in (('CALCCOLOR', 6, 0), ('CALCTEXTURE', 4, 6)):
        start, end = text.index(f'               when {state}2 =>'), text.index(f'               when {state}3 =>')
        block = text[start:end]
        divisors = ''.join(f'                  div{i}.divisor   <= to_signed(denom, 25);\n' for i in range(1, lanes + 1))
        block = _replace(block, divisors, '', what=w)
        for i in range(1, lanes):
            block = _sub(block, rf'(muldivResult2clk <= muldivResult2;) div{i}\.dividend <= resize\(muldivResult1clk - muldivResult2clk, 33\) & x"000";',
                         rf'\1 {grad} gradIdx <= to_unsigned({base + i - 1}, 4);', what=w)
        block = _sub(block, rf'\n( *)div{lanes}\.dividend <= resize\(muldivResult1clk - muldivResult2clk, 33\) & x"000";\n',
                     rf'\n\1{grad} gradIdx <= to_unsigned({base + lanes - 1}, 4);\n', what=w)
        starts = ''.join(f'                        div{i}.start     <= \'1\';\n' for i in range(1, lanes + 1))
        block = _replace(block, starts, '', what=w)
        text = text[:start] + block + text[end:]
    text = _replace(text, '''               when CALCCOLOR3 =>
                  dxR <= unsigned(div1.quotient(31 downto 0));
                  dxG <= unsigned(div3.quotient(31 downto 0));
                  dxB <= unsigned(div5.quotient(31 downto 0));
                  mulStep <= 0;
                  if (div1.done = '1') then''', '''               when CALCCOLOR3 =>
                  mulStep <= 0;
                  if (gradOutValid = '1' and gradOutIdx = 5) then''', what=w)
    text = _replace(text, '''               when CALCTEXTURE3 =>
                  dxU <= unsigned(div1.quotient(31 downto 0));
                  dxV <= unsigned(div3.quotient(31 downto 0));
                  mulStep <= 0;
                  if (div1.done = '1') then''', '''               when CALCTEXTURE3 =>
                  mulStep <= 0;
                  if (gradOutValid = '1' and gradOutIdx = 9) then''', what=w)
    text = _replace(text, '''                  dyWe <= '0';
                  if (mulStep = 0) then dyWe <= '1'; dyWaddr <= 0; dyWdata <= unsigned(div2.quotient(31 downto 0)); end if;
                  if (mulStep = 1) then dyWe <= '1'; dyWaddr <= 1; dyWdata <= unsigned(div4.quotient(31 downto 0)); end if;
                  if (mulStep = 2) then dyWe <= '1'; dyWaddr <= 2; dyWdata <= unsigned(div6.quotient(31 downto 0)); end if;
''', '', what=w)
    text = _replace(text, '''                  dyWe <= '0';
                  if (mulStep = 0) then
                     dyWe <= '1'; dyWaddr <= 3; dyWdata <= unsigned(div2.quotient(31 downto 0));
                     if (div2.quotient(31 downto 0) = 0) then dyU_zero <= '1'; else dyU_zero <= '0'; end if;
                  end if;
                  if (mulStep = 1) then
                     dyWe <= '1'; dyWaddr <= 4; dyWdata <= unsigned(div4.quotient(31 downto 0));
                     if (unsigned(div4.quotient(31 downto 0)) = 4096) then dyV_4096 <= '1'; else dyV_4096 <= '0'; end if;
                  end if;
''', '', what=w)
    if re.search(r'div[4-6]\.(quotient|done)', text) or re.search(r"div[4-6]\.start *<= '1'", text):
        raise ValueError('gpu_poly still uses dividers 4-6')
    return _narrow_poly(text)


def _narrow_poly(text):
    """Keep the colour and texture gradients, bases and accumulators to 20 bits.

    They only ever add, and multiply by an integer, modulo 2^32; the pixel
    reads bits 19..0 (colour and U/V bits 19..12, U/V accumulators 11..4). Bit
    k of a sum or product depends only on operand bits k..0, so bits 31..20
    never reach a pixel. The product keeps its true low bits (upstream's
    resize to 32 replaces bit 31 with the sign, which no pixel reads). The two
    filter checks that compare whole dxU and dxV move to flags captured with
    the quotient.
    """
    w = 'gpu_poly'
    zero = "(others => '0')"
    for name in ('dxR', 'dxG', 'dxB', 'dxU', 'dxV'):
        text = _replace(text, f'   signal {name:<20}: unsigned(31 downto 0) := {zero}; \n',
                        f'   signal {name:<20}: unsigned(19 downto 0) := {zero}; \n', what=w)
    for name in ('base_R', 'base_G', 'base_B', 'base_U', 'base_V', 'work_R', 'work_G', 'work_B', 'work_U', 'work_V'):
        text = _sub(text, rf"(   signal {name} +: unsigned\()31( downto 0\) := \(others => '0'\);)", r'\g<1>19\2', what=w)
    text = _replace(text, '   type tDyRam is array(0 to 7) of unsigned(31 downto 0);',
                    '   type tDyRam is array(0 to 7) of unsigned(19 downto 0);', what=w)
    for name in ('dyRead', 'dyWdata'):
        text = _replace(text, f'   signal {name:<20}: unsigned(31 downto 0) := {zero};',
                        f'   signal {name:<20}: unsigned(19 downto 0) := {zero};', what=w)
    flags = "   signal dyV_4096            : std_logic := '0';\n"
    text = _replace(text, flags, flags + "   signal dxU_4096            : std_logic := '0';\n"
                                         "   signal dxV_zero            : std_logic := '0';\n", what=w)
    for name in ('mulIn1', 'lineGradient'):
        text = _replace(text, f'   signal {name:<20}: unsigned(31 downto 0);',
                        f'   signal {name:<20}: unsigned(19 downto 0);', what=w)
    text = _replace(text, f'   signal mulResult32         : unsigned(31 downto 0) := {zero}; \n', '', what=w)
    text = _replace(text, '   signal mulResult32clk      : unsigned(31 downto 0);',
                    '   signal mulProduct          : signed(32 downto 0);\n'
                    '   signal mulResult32clk      : unsigned(19 downto 0);', what=w)
    text = _replace(text, f'   signal mulOp1              : unsigned(31 downto 0) := {zero};',
                    f'   signal mulOp1              : unsigned(19 downto 0) := {zero};', what=w)
    text = _sub(text, r'   mulResult32 <= unsigned\(resize\(signed\(mulIn1\) \* signed\(mulIn2\(12 downto 0\)\), 32\)\);[ \t]*\n', '', what=w)
    # mulOp1 as signed differs from the 32-bit gradient by a multiple of 2^20,
    # so the product's bits 19..0 are the upstream product's.
    text = _replace(text, '   mulResult32clk <= unsigned(resize(signed(mulOp1) * signed(mulOp2), 32));',
                    '   mulProduct     <= signed(mulOp1) * signed(mulOp2);\n'
                    '   mulResult32clk <= unsigned(mulProduct(19 downto 0));', what=w)
    for name in 'RGBUV':
        text = _replace(text, f'base_{name} <= x"000" & vt(coreVertex).{name.lower()} & x"800";',
                        f'base_{name} <= vt(coreVertex).{name.lower()} & x"800";', what=w)
    for i, name in ((0, 'dxR'), (2, 'dxG'), (4, 'dxB')):
        text = _replace(text, f'                  when {i} => {name} <= unsigned(gradOutQ);',
                        f'                  when {i} => {name} <= unsigned(gradOutQ(19 downto 0));', what=w)
    text = _replace(text, '                  when 6 => dxU <= unsigned(gradOutQ);\n',
                    '                  when 6 =>\n'
                    '                     dxU <= unsigned(gradOutQ(19 downto 0));\n'
                    "                     if (unsigned(gradOutQ) = 4096) then dxU_4096 <= '1'; else dxU_4096 <= '0'; end if;\n", what=w)
    text = _replace(text, '                  when 8 => dxV <= unsigned(gradOutQ);\n',
                    '                  when 8 =>\n'
                    '                     dxV <= unsigned(gradOutQ(19 downto 0));\n'
                    "                     if (gradOutQ = 0) then dxV_zero <= '1'; else dxV_zero <= '0'; end if;\n", what=w)
    text = _replace(text, 'dyWdata <= unsigned(gradOutQ);', 'dyWdata <= unsigned(gradOutQ(19 downto 0));', what=w)
    text = _replace(text, "if (textureFilter2DOff = '1' and dyU_zero = '1' and dxV = 0) then\n"
                          "                     if (dxU = 4096 or dyV_4096 = '1') then",
                    "if (textureFilter2DOff = '1' and dyU_zero = '1' and dxV_zero = '1') then\n"
                    "                     if (dxU_4096 = '1' or dyV_4096 = '1') then", what=w)
    return text


def recip_line_text(text):
    w = 'gpu_line'
    text = _sub(text, r'(      div6\s*: inout div_type;[ \t]*\n)', lambda m: m.group(1) + _ports(True), what=w)
    text = _sub(text, r'(   signal stepDb\s*: signed\(19 downto 0\) := \(others => \'0\'\);[ \t]*\n)',
                lambda m: m.group(1) + '   signal recipStartReg       : std_logic := \'0\';\n'
                '   signal gradStep            : integer range 0 to 3 := 3;\n'
                '   signal gradColor1, gradColor2 : std_logic_vector(7 downto 0);\n', what=w)
    text = _sub(text, r'\nbegin[ \t]*\n', lambda m: m.group(0) +
                '\n   recipStart   <= recipStartReg;\n'
                '   recipDivisor <= signed(resize(points, 21)) when recipStartReg = \'1\' else (others => \'0\');\n'
                '   -- one colour channel per step, through one subtractor\n'
                '   gradColor1 <= proc_color1(7 downto 0) when gradStep = 0 else proc_color1(15 downto 8) when gradStep = 1 else proc_color1(23 downto 16);\n'
                '   gradColor2 <= proc_color2(7 downto 0) when gradStep = 0 else proc_color2(15 downto 8) when gradStep = 1 else proc_color2(23 downto 16);\n', what=w)
    text = _replace(text, '''            div6.start           <= '0';
''', '''            div6.start           <= '0';
            recipStartReg        <= '0';
            gradValid            <= '0';
''', what=w)
    # Colour steps through the unit; the points/|dy| division moves to divider 3.
    for i in (3, 4, 5):
        text = _sub(text, rf'\n *div{i}\.start     <= \'1\';[ \t]*\n *div{i}\.dividend  <= [^\n]*\n *div{i}\.divisor   <= [^\n]*\n',
                    '\n', what=w)
    text = _sub(text, r'( *)div6\.start     <= \'1\';', r"\1div3.start     <= '1';\n\1recipStartReg  <= '1';\n\1gradStep       <= 0;", what=w)
    text = _replace(text, 'div6.dividend  <=', 'div3.dividend  <=', what=w)
    text = _replace(text, 'div6.divisor   <=', 'div3.divisor   <=', count=2, what=w)
    text = _replace(text, "to_signed(1, div6.divisor'length)", "to_signed(1, div3.divisor'length)", what=w)
    text = _replace(text, 'div6.quotient(9 downto 0)', 'div3.quotient(9 downto 0)', what=w)
    text = _sub(text, r'\n *stepDr   <= div3\.quotient\(19 downto 0\);[ \t]*\n *stepDg   <= div4\.quotient\(19 downto 0\);[ \t]*\n *stepDb   <= div5\.quotient\(19 downto 0\);[ \t]*\n',
                '\n', what=w)
    text = _sub(text, r'(               when PROCCALC3 =>[ \t]*\n)', lambda m: m.group(1) + '''                  -- gpu-recip: colour steps once the reciprocal of points is ready
                  if (recipReady = '1' and gradStep < 3) then
                     gradStep  <= gradStep + 1;
                     gradValid <= '1';
                     gradIdx   <= to_unsigned(gradStep, 4);
                     gradN     <= resize(('0' & signed(gradColor2)) - ('0' & signed(gradColor1)), 21);
                  end if;
                  if (gradOutValid = '1') then
                     case (to_integer(gradOutIdx)) is
                        when 0      => stepDr <= gradOutQ(19 downto 0);
                        when 1      => stepDg <= gradOutQ(19 downto 0);
                        when others => stepDb <= gradOutQ(19 downto 0);
                     end case;
                  end if;
''', what=w)
    if re.search(r'div[4-6]\.(quotient|done)', text) or re.search(r"div[4-6]\.start *<= '1'", text):
        raise ValueError('gpu_line still uses dividers 4-6')
    return text


def recip_gpu_text(text):
    w = 'gpu'
    text = _replace(text, '   signal div_array                 : t_div_array;\n',
                    '   signal div_array                 : t_div_array;\n'
                    '   -- gpu-recip: the shared-divisor gradient unit, driven by line or polygon\n'
                    '   signal line_recipStart, poly_recipStart, recipStart : std_logic;\n'
                    '   signal line_recipDivisor, poly_recipDivisor, recipDivisor : signed(20 downto 0);\n'
                    '   signal line_gradValid, poly_gradValid, gradValid : std_logic;\n'
                    '   signal line_gradN, poly_gradN, gradN : signed(20 downto 0);\n'
                    '   signal line_gradIdx, poly_gradIdx, gradIdx : unsigned(3 downto 0);\n'
                    '   signal recipReady, gradOutValid : std_logic;\n'
                    '   signal gradOutIdx : unsigned(3 downto 0);\n'
                    '   signal gradOutQ : signed(31 downto 0);\n', what=w)
    for kind in ('line', 'poly'):
        extra = (f'      recipStart           => {kind}_recipStart,\n'
                 f'      recipDivisor         => {kind}_recipDivisor,\n'
                 f'      gradValid            => {kind}_gradValid,\n'
                 f'      gradN                => {kind}_gradN,\n'
                 f'      gradIdx              => {kind}_gradIdx,\n'
                 '      gradOutValid         => gradOutValid,\n'
                 '      gradOutIdx           => gradOutIdx,\n'
                 '      gradOutQ             => gradOutQ,\n')
        if kind == 'line':
            extra += '      recipReady           => recipReady,\n'
        text = _sub(text, rf'(      div6\s*=> {kind}_div\(5\),[ \t]*\n)', lambda m, e=extra: m.group(1) + e, what=w)
    new = '''      -- gpu-recip: three dividers with an 11-bit divisor (edge dy < 512,
      -- line points and |dy| < 1024); the gradients use the shared unit.
      glane : if i < 3 generate
         signal laneRemainder : signed(10 downto 0);
      begin
         idivider : entity work.divider
         port map
         (
            clk       => clk2x,
            start     => div_array(i).start,
            done      => div_array(i).done,
            dividend  => div_array(i).dividend,
            divisor   => div_array(i).divisor(10 downto 0),
            quotient  => div_array(i).quotient,
            remainder => laneRemainder
         );
         div_array(i).remainder <= resize(laneRemainder, 25);
      end generate;
      gnolane : if i >= 3 generate
         div_array(i).done      <= '0';
         div_array(i).quotient  <= (others => '0');
         div_array(i).remainder <= (others => '0');
      end generate;
   end generate;

   recipStart   <= line_recipStart or poly_recipStart;
   recipDivisor <= line_recipDivisor or poly_recipDivisor;
   gradValid    <= line_gradValid or poly_gradValid;
   -- the numerator registers hold their last value; select by valid
   gradN        <= poly_gradN when poly_gradValid = '1' else line_gradN;
   gradIdx      <= poly_gradIdx when poly_gradValid = '1' else line_gradIdx;

   irecip : entity work.psx_gpu_recip_unit
   port map
   (
      clk          => clk2x,
      ce           => ce,
      reset        => softreset or SS_reset,
      recipStart   => recipStart,
      recipDivisor => recipDivisor,
      recipReady   => recipReady,
      inValid      => gradValid,
      inN          => gradN,
      inIdx        => gradIdx,
      outValid     => gradOutValid,
      outIdx       => gradOutIdx,
      outQ         => gradOutQ
   );'''
    text = _sub(text, r'(?s)      idivider : entity work\.divider\n.*?\n      \);[ \t]*\n   end generate;', lambda m: new, what=w)
    return text


def apply_project(project, assign):
    """Write the three transformed GPU sources into project/gpu-recip and point
    the qsf at them and at the unit. assign(name) returns the selected path."""
    project = Path(project)
    qsf = project / 'apf_top.qsf'
    work = project / 'gpu-recip'
    work.mkdir()
    q = qsf.read_text()
    rows = {}
    for name, fn in (('gpu.vhd', recip_gpu_text), ('gpu_poly.vhd', recip_poly_text), ('gpu_line.vhd', recip_line_text)):
        source = assign(name)
        out = work / name
        out.write_text(fn(source.read_text()))
        if q.count(f'"{source}"') != 1: raise ValueError('Ambiguous GPU source assignment: ' + name)
        q = q.replace(f'"{source}"', f'"{out}"')
        rows[name] = dict(source=str(source), path=str(out))
    line = next(l for l in q.splitlines() if f'"{work / "gpu.vhd"}"' in l)
    unit_line = line.replace(f'"{work / "gpu.vhd"}"', f'"{UNIT}"')
    q = q.replace(line + '\n', unit_line + '\n' + line + '\n', 1)
    qsf.write_text(q)
    rows['unit'] = str(UNIT)
    return rows
