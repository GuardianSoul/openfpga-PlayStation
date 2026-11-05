"""MDEC arithmetic of Mednafen's PSX MDEC (mdec-mednafen), written for this core.

Source: Beetle PSX (Mednafen PSX) mednafen/psx/mdec.c @ e57860d8 (GPL-2;
its scalar paths) and psx-spx. The pinned MiSTer mdec keeps its block parsing,
its pipeline and every cycle; only the arithmetic changes:

- run-length values: Mednafen's dequantization. The DC term uses the quant
  byte alone, an AC term (value * qscale * quant) >> 3; either is shifted left
  4 with an 8 toward zero added (none for 0), or value * 32 when the factor is
  0; clamped to -16384..16383; always placed through the zigzag table. The
  coefficient path and its RAM are 16 bits wide (11 before).
- IDCT: matrix = scale >> 3; each pass rounds (sum + 0x4000) >> 15; the first
  pass keeps 16 bits (two's complement wrap, as Mednafen's int16 store), the
  second wraps to 9 bits and clamps to -128..127. The temporary RAM is 16 bits.
  Mednafen sums in int32 and keeps only bits 30..15 (pass 1) or 23..15 (pass
  2) of sum + 0x4000, so the accumulator is 31 bits, modular, and starts at
  0x4000: both results are bit slices.
- colour: R = y + ((359 cr + 128) >> 8), G = y + ((((-88 cb) & ~31) +
  ((-183 cr) & ~7) + 128) >> 8), B = y + ((454 cb + 128) >> 8), each wrapped to
  9 bits and clamped, stored unsigned (+128); (p + 128) >> 8 is taken as
  (p >> 8) + bit 7 of p, the bit a carry into y's adder; 24-bit signed output flips the
  top bit back; 15-bit signed output flips each channel's top bit (0x4210).
- monochrome: the luminance quant table; 8-bit y XOR 0x80 unless signed;
  4-bit (y + 8) capped at 127, its top nibble, XOR 8 unless signed.
Output order, block parsing and all timing are the pinned core's.
"""
import json
from pathlib import Path

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest


def _rstrip(text):
    return '\n'.join(line.rstrip() for line in text.split('\n'))


def _replace(text, old, new, count=1):
    # trailing blanks differ between copies; anchors match without them
    old, new = _rstrip(old), _rstrip(new)
    if text.count(old) != count: raise ValueError('Pinned MDEC source changed near: ' + old.strip()[:60])
    return text.replace(old, new)


def mednafen_text(text):
    text_box = [_rstrip(text)]
    def sub(old, new, count=1): text_box[0] = _replace(text_box[0], old, new, count)
    # widths: run-length FIFO word {done, coeff(6), block(3), value(16)}, coefficient and temporary RAMs 16 bits
    sub('   signal FifoRL_Din          : std_logic_vector(20 downto 0) := (others => \'0\');',
        '   signal FifoRL_Din          : std_logic_vector(25 downto 0) := (others => \'0\');   -- mdec-mednafen: 16-bit values')
    sub('   signal FifoRL_Dout         : std_logic_vector(20 downto 0);', '   signal FifoRL_Dout         : std_logic_vector(25 downto 0);')
    sub('      DATAWIDTH        => 21,', '      DATAWIDTH        => 26,')
    sub('   signal IDCTi_data_a        : std_logic_vector(10 downto 0) := (others => \'0\');',
        '   signal IDCTi_data_a        : std_logic_vector(15 downto 0) := (others => \'0\');')
    sub('   signal IDCTi_dataO_a       : std_logic_vector(10 downto 0) := (others => \'0\');',
        '   signal IDCTi_dataO_a       : std_logic_vector(15 downto 0) := (others => \'0\');')
    sub('   signal IDCTi_dataO_b       : std_logic_vector(10 downto 0) := (others => \'0\');',
        '   signal IDCTi_dataO_b       : std_logic_vector(15 downto 0) := (others => \'0\');')
    sub('   generic map ( addr_width => 6, data_width => 11)', '   generic map ( addr_width => 6, data_width => 16)')
    sub('      data_b      => (10 downto 0 => \'0\'),', '      data_b      => (15 downto 0 => \'0\'),')
    sub('   signal IDCTt_data_a        : std_logic_vector(29 downto 0) := (others => \'0\');',
        '   signal IDCTt_data_a        : std_logic_vector(15 downto 0) := (others => \'0\');')
    sub('   signal IDCTt_dataO_b1      : std_logic_vector(29 downto 0) := (others => \'0\');',
        '   signal IDCTt_dataO_b1      : std_logic_vector(15 downto 0) := (others => \'0\');')
    sub('   signal IDCTt_dataO_b2      : std_logic_vector(29 downto 0) := (others => \'0\');',
        '   signal IDCTt_dataO_b2      : std_logic_vector(15 downto 0) := (others => \'0\');')
    sub('   generic map ( addr_width => 6, data_width => 30)', '   generic map ( addr_width => 6, data_width => 16)', 2)
    sub('      data_b      => (29 downto 0 => \'0\'),', '      data_b      => (15 downto 0 => \'0\'),', 2)
    sub('   signal idct_calc0_mul11    : signed(29 downto 0);', '   signal idct_calc0_mul11    : signed(15 downto 0);')
    sub('   signal idct_calc0_mul21    : signed(29 downto 0);', '   signal idct_calc0_mul21    : signed(15 downto 0);')
    sub('   signal idct_calc1_mul1     : signed(45 downto 0); ', '   signal idct_calc1_mul1     : signed(31 downto 0); ')
    sub('   signal idct_calc1_mul2     : signed(45 downto 0); ', '   signal idct_calc1_mul2     : signed(31 downto 0); ')
    sub('   signal idct_sum            : signed(48 downto 0);',
        '   signal idct_sum            : signed(30 downto 0);   -- mdec-mednafen: int32 sum, bits 30..0 used')
    sub("""                  idct_sum <= to_signed(0, idct_sum'length) + idct_calc1_mul1 + idct_calc1_mul2;
               else
                  idct_sum <= idct_sum + idct_calc1_mul1 + idct_calc1_mul2;""",
        """                  idct_sum <= to_signed(16#4000#, 31) + resize(idct_calc1_mul1, 31) + resize(idct_calc1_mul2, 31);   -- mdec-mednafen: rounding constant up front
               else
                  idct_sum <= idct_sum + resize(idct_calc1_mul1, 31) + resize(idct_calc1_mul2, 31);""")
    # FIFO fields
    sub("            FifoRL_Din(20) <= '1';", "            FifoRL_Din(25) <= '1';")
    sub("            FifoRL_Din(20) <= '0'; -- block not finished", "            FifoRL_Din(25) <= '0'; -- block not finished")
    sub("            FifoRL_Din(13 downto 11) <= std_logic_vector(calcBlock);", "            FifoRL_Din(18 downto 16) <= std_logic_vector(calcBlock);")
    sub("""            if (calcQScale = 0) then
               FifoRL_Din(19 downto 14) <= std_logic_vector(calcCoeff);
            else
               FifoRL_Din(19 downto 14) <= std_logic_vector(to_unsigned(calcZigzag, 6));
            end if;""", """            FifoRL_Din(24 downto 19) <= std_logic_vector(to_unsigned(calcZigzag, 6));   -- mdec-mednafen: always zigzag""")
    sub("FifoRL_Dout(19 downto 14)", "FifoRL_Dout(24 downto 19)", 3)
    sub("FifoRL_Dout(20)", "FifoRL_Dout(25)", 2)
    sub("idct_block <= to_integer(unsigned(FifoRL_Dout(13 downto 11)));", "idct_block <= to_integer(unsigned(FifoRL_Dout(18 downto 16)));")
    sub("<= signed(FifoRL_Dout(10 downto 0));", "<= resize(signed(FifoRL_Dout(15 downto 0)), 11);")
    sub("   IDCTi_data_a    <= FifoRL_Dout(10 downto 0);", "   IDCTi_data_a    <= FifoRL_Dout(15 downto 0);")
    # dequantization
    sub("""      variable calcvalue : integer;
   begin""", """      variable calcvalue : integer;
      variable dqFactor  : integer;                -- mdec-mednafen
      variable dqProduct : signed(23 downto 0);
      variable dqValue   : signed(15 downto 0);
   begin""")
    sub("""            calcvalue := to_integer(calcData);
            if (calcQScale = 0) then
               calcvalue := calcvalue * 2;
            else
               calcvalue := calcvalue * to_integer(unsigned(RamYUVdataB));
               if (calcCoeff > 0) then
                  calcvalue := calcvalue * to_integer(calcQScale);
                  calcvalue := calcvalue + 4;
                  calcvalue := calcvalue / 8;
               end if;
            end if;

            if (calcvalue < -1024) then
               calcvalue := -1024;
            elsif (calcvalue > 1023) then
               calcvalue := 1023;
            end if;

            FifoRL_Din(10 downto 0) <= std_logic_vector(to_signed(calcvalue, 11));""",
"""            -- mdec-mednafen: DC factor = quant byte, AC factor = quant byte * qscale
            dqFactor := to_integer(unsigned(RamYUVdataB));
            if (calcCoeff > 0) then
               dqFactor := dqFactor * to_integer(calcQScale);
            end if;
            -- p * 16 + 8 (value < 0) or - 8 (value > 0), clamped to -16384..16383, as
            -- (p << 4) | 8 or ((p - 1) << 4) | 8 with the clamp tested on p; value * 32
            -- (never clamped) when the factor is 0
            dqProduct := to_signed(to_integer(calcData) * dqFactor, 24);
            if (calcCoeff > 0) then
               dqProduct := shift_right(dqProduct, 3);
            end if;
            if (dqFactor = 0) then
               dqValue := resize(calcData, 11) & "00000";
            elsif (calcData < 0) then
               if (dqProduct < -1024) then dqValue := to_signed(-16384, 16); else dqValue := dqProduct(11 downto 0) & "1000"; end if;
            elsif (calcData > 0) then
               if (dqProduct > 1024) then dqValue := to_signed(16383, 16); else dqValue := (dqProduct(11 downto 0) - 1) & "1000"; end if;
            else
               dqValue := (others => '0');
            end if;

            FifoRL_Din(15 downto 0) <= std_logic_vector(dqValue);""")
    # monochrome uses the luminance quant table
    sub("   RamYUVaddrB <= '0' & currentCoeff(5 downto 0) when (currentBlock >= 2) else '1' & currentCoeff(5 downto 0);",
        "   RamYUVaddrB <= '0' & currentCoeff(5 downto 0) when (currentBlock >= 2 or rec_depth(1) = '0') else '1' & currentCoeff(5 downto 0);   -- mdec-mednafen: luminance table for monochrome")
    # IDCT: matrix = scale >> 3
    sub("   idct_calc0_mul12  <= signed(scaleT1_data_b);", "   idct_calc0_mul12  <= shift_right(signed(scaleT1_data_b), 3);   -- mdec-mednafen")
    sub("   idct_calc0_mul22  <= signed(scaleT2_data_b);", "   idct_calc0_mul22  <= shift_right(signed(scaleT2_data_b), 3);")
    # first pass: round to 16 bits
    sub("   IDCTt_data_a    <= std_logic_vector(resize(idct_sum, 30));",
        "   IDCTt_data_a    <= std_logic_vector(idct_sum(30 downto 15));   -- mdec-mednafen: int16 store of (sum + 0x4000) >> 15")
    # second pass: round, wrap to 9 bits, clamp
    sub("""      variable shifted : signed(16 downto 0);
   begin""", """      variable shifted : signed(16 downto 0);
      variable wrapped : signed(8 downto 0);   -- mdec-mednafen
   begin""")
    sub("""                  shifted := idct_sum(48 downto 32);
                  if (shifted < -128) then
                     idct_resultClip <= to_signed(-128, 8);
                  elsif (shifted > 127) then
                     idct_resultClip <= to_signed(127, 8);
                  else
                     idct_resultClip <= resize(shifted, 8);
                  end if;""", """                  wrapped := idct_sum(23 downto 15);   -- mdec-mednafen: (sum + 0x4000) >> 15, low 9 bits
                  if (wrapped < -128) then
                     idct_resultClip <= to_signed(-128, 8);
                  elsif (wrapped > 127) then
                     idct_resultClip <= to_signed(127, 8);
                  else
                     idct_resultClip <= resize(wrapped, 8);
                  end if;""")
    # colour conversion
    sub("""                  conv_R := to_signed((to_integer(color_read_R) * 359), 17);
                  conv_G := to_signed(((to_integer(color_read_B) * (-88)) + (to_integer(color_read_R) * (-183))), 17);
                  conv_B := to_signed((to_integer(color_read_B) * 454), 17);""",
"""                  -- mdec-mednafen: green's terms floored to 32 and 8 first; bit 7 kept for rounding
                  conv_R := to_signed(to_integer(color_read_R) * 359, 17);
                  conv_G := to_signed((to_integer(shift_right(to_signed(to_integer(color_read_B) * (-88), 17), 5)) * 32)
                                    + (to_integer(shift_right(to_signed(to_integer(color_read_R) * (-183), 17), 3)) * 8), 17);
                  conv_B := to_signed(to_integer(color_read_B) * 454, 17);
                  color_conv_R <= conv_R(16 downto 7);
                  color_conv_G <= conv_G(16 downto 7);
                  color_conv_B <= conv_B(16 downto 7);""")
    sub("""                  color_conv_R <= conv_R(16 downto 8);
                  color_conv_G <= conv_G(16 downto 8);
                  color_conv_B <= conv_B(16 downto 8);
""", """""")
    for c in 'RGB':
        sub(f"   signal color_conv_{c}        : signed(8 downto 0) := (others => '0');",
            f"   signal color_conv_{c}        : signed(9 downto 0) := (others => '0');   -- mdec-mednafen: product bits 16..7")
        sub(f"                  mix_{c} := (resize(color_read_Y,10) + resize(color_conv_{c},10));",
            f"                  mix_{c} := resize(color_read_Y,10) + resize(color_conv_{c}(9 downto 1),10) + resize(signed('0' & color_conv_{c}(0 downto 0)),10);   -- + bit 7: (p + 128) >> 8")
    sub("""                  if (mix_R < -128) then final_R := to_signed(-128, 9); elsif (mix_R > 127) then final_R := to_signed(127, 9); else final_R := resize(mix_R, 9); end if;
                  if (mix_G < -128) then final_G := to_signed(-128, 9); elsif (mix_G > 127) then final_G := to_signed(127, 9); else final_G := resize(mix_G, 9); end if;
                  if (mix_B < -128) then final_B := to_signed(-128, 9); elsif (mix_B > 127) then final_B := to_signed(127, 9); else final_B := resize(mix_B, 9); end if;
                  final_R := final_R + 128;
                  final_G := final_G + 128;
                  final_B := final_B + 128;""", """                  -- mdec-mednafen: wrap to 9 bits, then clamp
                  final_R := mix_R(8 downto 0); final_G := mix_G(8 downto 0); final_B := mix_B(8 downto 0);
                  if (final_R < -128) then final_R := to_signed(-128, 9); elsif (final_R > 127) then final_R := to_signed(127, 9); end if;
                  if (final_G < -128) then final_G := to_signed(-128, 9); elsif (final_G > 127) then final_G := to_signed(127, 9); end if;
                  if (final_B < -128) then final_B := to_signed(-128, 9); elsif (final_B > 127) then final_B := to_signed(127, 9); end if;
                  if (rec_depth = "11" or rec_signed = '0') then   -- unsigned (15-bit output flips its channel top bits later)
                     final_R := final_R + 128;
                     final_G := final_G + 128;
                     final_B := final_B + 128;
                  end if;""")
    sub("""                  color_result <= x"0000" & std_logic_vector(to_unsigned(to_integer(signed(idct_readData)) + 128, 8));""",
"""                  -- mdec-mednafen: 8-bit y, 4-bit (y + 8) capped at 127; top bit flipped unless signed
                  bwValue := resize(signed(idct_readData), 9);
                  if (rec_depth(0) = '0') then
                     bwValue := bwValue + 8;
                     if (bwValue > 127) then bwValue := to_signed(127, 9); end if;
                  end if;
                  if (rec_signed = '0') then bwValue(7) := not bwValue(7); end if;
                  color_result <= x"0000" & std_logic_vector(bwValue(7 downto 0));""")
    sub("""      variable final_B  : signed(8 downto 0);
   begin""", """      variable final_B  : signed(8 downto 0);
      variable bwValue  : signed(8 downto 0);   -- mdec-mednafen
   begin""")
    # 15-bit signed output: each channel's top bit flipped (Mednafen's XOR 0x4210)
    sub("""                     color_round16_b := roundUp8to5(color_readData(23 downto 16));""",
        """                     color_round16_b := roundUp8to5(color_readData(23 downto 16));
                     if (rec_signed = '1') then   -- mdec-mednafen
                        color_round16_r(4) := not color_round16_r(4);
                        color_round16_g(4) := not color_round16_g(4);
                        color_round16_b(4) := not color_round16_b(4);
                     end if;""")
    return text_box[0]


def apply_mdec_mednafen(manifest):
    target = manifest['targets']['psx_top']
    if 'mdec_idct_pipe' in target: raise ValueError('Apply mdec-mednafen before mdec-idct-pipe')
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'mdec.vhd')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'mdec-mednafen'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'mdec.vhd'; path.write_text(mednafen_text(previous.read_text()))
    target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
        original=str(previous), original_sha256=digest(previous), transform='MDEC arithmetic of Mednafen PSX (Beetle PSX mdec.c @ e57860d8)')
    qsf = directory / (target['top'] + '.qsf'); q = qsf.read_text()
    if q.count(str(previous)) != 1: raise ValueError('Expected one MDEC assignment')
    qsf.write_text(q.replace(str(previous), str(path))); target['project_sha256']['.qsf'] = digest(qsf)
    target['mdec_mednafen'] = True; manifest['mdec_mednafen'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
