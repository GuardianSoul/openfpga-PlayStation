"""MDEC arithmetic of Mednafen / DuckStation's default routines (mdec-mednafen).

The pinned MDEC follows psx-spx: coefficients quantised to 11 bits with
(v*q*qs+4)/8, a full-precision IDCT truncated once, floored YUV products,
clamping without the 9-bit wrap, the colour quant table for monochrome and
no signed output. This variant changes only the arithmetic, not the
schedule:

- Run-length decode keeps 4 extra bits: qscale*q = 0 gives v<<5, otherwise
  ((v*qs*q)>>3)<<4 (DC: (v*q)<<4) plus 8 toward zero, clamped to 15 bits,
  always in zigzag order.
- Each IDCT pass is rounded, (sum + 0x20000) >> 18, and the intermediate is
  16 bits, so the multipliers shrink from 30x16 to 16x16 and the temporary
  rows from 30 to 16 bits. The rounding constant seeds the accumulator.
- IDCT and YUV results wrap to 9 bits before clamping to -128..127.
- G = Y + (((-88*Cb) & ~0x1F) + ((-183*Cr) & ~0x07) + 0x80) >> 8, and R and
  B round with + 0x80.
- Monochrome uses the luminance quant table; bit 26 selects signed output.

Every cycle and port timing is unchanged. `make test-mdec-mednafen` checks
it word for word against the "mednafen" model in run_mdec_reference.py.
"""
import json
from pathlib import Path

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('Pinned MDEC source changed near: ' + old.strip().splitlines()[0][:70])
    return text.replace(old, new)


def mednafen_text(text):
    # Run-length FIFO: 15-bit coefficient, block at 17..15, index at 23..18, done at 24.
    text = _replace(text, 'signal FifoRL_Din          : std_logic_vector(20 downto 0)', 'signal FifoRL_Din          : std_logic_vector(24 downto 0)')
    text = _replace(text, 'signal FifoRL_Dout         : std_logic_vector(20 downto 0)', 'signal FifoRL_Dout         : std_logic_vector(24 downto 0)')
    text = _replace(text, 'type tidct_input is array(0 to 63) of signed(10 downto 0);', 'type tidct_input is array(0 to 63) of signed(14 downto 0);')
    for name in ('IDCTi_data_a ', 'IDCTi_dataO_a', 'IDCTi_dataO_b'):
        text = _replace(text, f'signal {name}       : std_logic_vector(10 downto 0)', f'signal {name}       : std_logic_vector(14 downto 0)')
    start = text.index('            calcvalue := to_integer(calcData);')
    end = text.index("FifoRL_Din(20) <= '0'; -- block not finished") + len("FifoRL_Din(20) <= '0'; -- block not finished")
    if text.count('calcvalue := calcvalue / 8;') != 1: raise ValueError('Pinned MDEC run-length arithmetic changed')
    text = text[:start] + '''            -- Mednafen arithmetic (mdec-mednafen): 4 extra bits, bias toward zero.
            calcvalue := to_integer(calcData);
            if (calcCoeff = 0) then
               scq := to_integer(unsigned(RamYUVdataB));
            else
               scq := to_integer(calcQScale) * to_integer(unsigned(RamYUVdataB));
            end if;
            if (calcQScale = 0 or (calcCoeff /= 0 and scq = 0)) then
               calcvalue := calcvalue * 32;
            else
               if (calcCoeff = 0) then
                  product := to_signed(calcvalue * scq, 25);
               else
                  product := shift_right(to_signed(calcvalue * scq, 25), 3);
               end if;
               calcvalue := to_integer(product) * 16;
               if (calcData < 0) then calcvalue := calcvalue + 8; elsif (calcData > 0) then calcvalue := calcvalue - 8; end if;
            end if;

            if (calcvalue < -16384) then
               calcvalue := -16384;
            elsif (calcvalue > 16383) then
               calcvalue := 16383;
            end if;

            FifoRL_Din(14 downto 0) <= std_logic_vector(to_signed(calcvalue, 15));

            FifoRL_Din(17 downto 15) <= std_logic_vector(calcBlock);
            FifoRL_Din(23 downto 18) <= std_logic_vector(to_unsigned(calcZigzag, 6));

            FifoRL_Din(24) <= '0'; -- block not finished''' + text[end:]
    text = _replace(text, '''            FifoRL_Din(20) <= '1';''', '''            FifoRL_Din(24) <= '1';''')
    text = _replace(text, '''      variable calcvalue : integer;
   begin''', '''      variable calcvalue : integer;
      variable scq       : integer range 0 to 16065;
      variable product   : signed(24 downto 0);
   begin''')
    text = _replace(text, 'DATAWIDTH        => 21,', 'DATAWIDTH        => 25,')
    text = _replace(text, "else FifoRL_Dout(19 downto 14);", "else FifoRL_Dout(23 downto 18);")
    text = _replace(text, "and FifoRL_Dout(20) = '0') else '0';", "and FifoRL_Dout(24) = '0') else '0';")
    text = _replace(text, 'IDCTi_data_a    <= FifoRL_Dout(10 downto 0);', 'IDCTi_data_a    <= FifoRL_Dout(14 downto 0);')
    text = _replace(text, 'generic map ( addr_width => 6, data_width => 11)', 'generic map ( addr_width => 6, data_width => 15)')
    text = _replace(text, 'data_b      => (10 downto 0 => \'0\'),', 'data_b      => (14 downto 0 => \'0\'),')
    text = _replace(text, "if (FifoRL_Dout(20) = '1') then", "if (FifoRL_Dout(24) = '1') then")
    text = _replace(text, 'if (unsigned(FifoRL_Dout(19 downto 14)) = 0) then', 'if (unsigned(FifoRL_Dout(23 downto 18)) = 0) then')
    text = _replace(text, 'idct_block <= to_integer(unsigned(FifoRL_Dout(13 downto 11)));', 'idct_block <= to_integer(unsigned(FifoRL_Dout(17 downto 15)));')
    text = _replace(text, 'idct_input(to_integer(unsigned(FifoRL_Dout(19 downto 14)))) <= signed(FifoRL_Dout(10 downto 0));',
                    'idct_input(to_integer(unsigned(FifoRL_Dout(23 downto 18)))) <= signed(FifoRL_Dout(14 downto 0));')
    # Monochrome uses the luminance table.
    text = _replace(text, "RamYUVaddrB <= '0' & currentCoeff(5 downto 0) when (currentBlock >= 2) else",
                    "RamYUVaddrB <= '0' & currentCoeff(5 downto 0) when (currentBlock >= 2 or rec_depth(1) = '0') else")
    # IDCT: 16-bit operands and temporaries, rounded passes.
    text = _replace(text, 'signal idct_sum            : signed(48 downto 0);', 'signal idct_sum            : signed(35 downto 0);')
    text = _replace(text, 'signal IDCTt_data_a        : std_logic_vector(29 downto 0)', 'signal IDCTt_data_a        : std_logic_vector(15 downto 0)')
    text = _replace(text, 'signal IDCTt_dataO_b1      : std_logic_vector(29 downto 0)', 'signal IDCTt_dataO_b1      : std_logic_vector(15 downto 0)')
    text = _replace(text, 'signal IDCTt_dataO_b2      : std_logic_vector(29 downto 0)', 'signal IDCTt_dataO_b2      : std_logic_vector(15 downto 0)')
    text = _replace(text, 'generic map ( addr_width => 6, data_width => 30)', 'generic map ( addr_width => 6, data_width => 16)', count=2)
    text = _replace(text, 'data_b      => (29 downto 0 => \'0\'),', 'data_b      => (15 downto 0 => \'0\'),', count=2)
    text = _replace(text, 'IDCTt_data_a    <= std_logic_vector(resize(idct_sum, 30));', 'IDCTt_data_a    <= std_logic_vector(idct_sum(33 downto 18));')
    text = _replace(text, 'signal idct_calc0_mul11    : signed(29 downto 0);', 'signal idct_calc0_mul11    : signed(15 downto 0);')
    text = _replace(text, 'signal idct_calc0_mul21    : signed(29 downto 0);', 'signal idct_calc0_mul21    : signed(15 downto 0);')
    text = _replace(text, 'signal idct_calc1_mul1     : signed(45 downto 0); ', 'signal idct_calc1_mul1     : signed(31 downto 0); ')
    text = _replace(text, 'signal idct_calc1_mul2     : signed(45 downto 0); ', 'signal idct_calc1_mul2     : signed(31 downto 0); ')
    text = _replace(text, 'idct_sum <= to_signed(0, idct_sum\'length) + idct_calc1_mul1 + idct_calc1_mul2;',
                    'idct_sum <= to_signed(16#20000#, idct_sum\'length) + idct_calc1_mul1 + idct_calc1_mul2; -- rounding')
    text = _replace(text, '''                  shifted := idct_sum(48 downto 32);
                  if (shifted < -128) then
                     idct_resultClip <= to_signed(-128, 8);
                  elsif (shifted > 127) then
                     idct_resultClip <= to_signed(127, 8);
                  else
                     idct_resultClip <= resize(shifted, 8);
                  end if;''', '''                  shifted := resize(idct_sum(26 downto 18), 17); -- 9-bit wrap
                  if (shifted < -128) then
                     idct_resultClip <= to_signed(-128, 8);
                  elsif (shifted > 127) then
                     idct_resultClip <= to_signed(127, 8);
                  else
                     idct_resultClip <= resize(shifted, 8);
                  end if;''')
    # YUV: rounded products, masked G terms, 9-bit wrap, signed output.
    text = _replace(text, '''                  conv_R := to_signed((to_integer(color_read_R) * 359), 17);
                  conv_G := to_signed(((to_integer(color_read_B) * (-88)) + (to_integer(color_read_R) * (-183))), 17);
                  conv_B := to_signed((to_integer(color_read_B) * 454), 17);''', '''                  conv_R := to_signed((to_integer(color_read_R) * 359) + 128, 17);
                  conv_G := (to_signed(to_integer(color_read_B) * (-88), 17) and to_signed(-32, 17)) +
                            (to_signed(to_integer(color_read_R) * (-183), 17) and to_signed(-8, 17)) + 128;
                  conv_B := to_signed((to_integer(color_read_B) * 454) + 128, 17);''')
    for c in 'RGB':
        text = _replace(text, f'if (mix_{c} < -128) then final_{c} := to_signed(-128, 9); elsif (mix_{c} > 127) then final_{c} := to_signed(127, 9); else final_{c} := resize(mix_{c}, 9); end if;',
                        f'mix_{c} := resize(mix_{c}(8 downto 0), 10); if (mix_{c} < -128) then final_{c} := to_signed(-128, 9); elsif (mix_{c} > 127) then final_{c} := to_signed(127, 9); else final_{c} := resize(mix_{c}, 9); end if;')
        text = _replace(text, f'final_{c} := final_{c} + 128;', f'if (rec_signed = \'0\') then final_{c} := final_{c} + 128; end if;')
    text = _replace(text, "color_result <= x\"0000\" & std_logic_vector(to_unsigned(to_integer(signed(idct_readData)) + 128, 8));",
                    "if (rec_signed = '1') then color_result <= x\"0000\" & idct_readData; else color_result <= x\"0000\" & std_logic_vector(to_unsigned(to_integer(signed(idct_readData)) + 128, 8)); end if;")
    return text


def apply_mdec_mednafen(manifest):
    target = manifest['targets']['psx_top']
    if 'mdec_idct_pipe' in target: raise ValueError('Apply mdec-mednafen before mdec-idct-pipe')
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'mdec.vhd')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'mdec-mednafen'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'mdec.vhd'; path.write_text(mednafen_text(previous.read_text()))
    target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
        original=str(previous), original_sha256=digest(previous), transform='MDEC arithmetic of Mednafen/DuckStation')
    qsf = directory / (target['top'] + '.qsf'); q = qsf.read_text()
    if q.count(str(previous)) != 1: raise ValueError('Expected one MDEC assignment')
    qsf.write_text(q.replace(str(previous), str(path))); target['project_sha256']['.qsf'] = digest(qsf)
    target['mdec_mednafen'] = True; manifest['mdec_mednafen'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
