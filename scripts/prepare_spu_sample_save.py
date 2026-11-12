#!/usr/bin/env python3
"""Experimental decoded-SPU sample scan; no new RAM or external ports.

Reserved scan word63 selects one of four quadrants; words128..255 expose
128 packed pairs of 16-bit decoded samples. Existing context addresses are
unchanged. The normal guest datapath keeps its original edges and addresses.
"""
from pathlib import Path


def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('SPU sample save anchor changed: ' + before[:100])
    return text.replace(before, after)


def transform(text):
    if 'sample_save_page' in text:
        raise ValueError('SPU sample scan already installed')
    anchor='   signal adpcm_ram_q_b       : tadpcm_data; \n'
    declarations='''   signal sample_save_page : unsigned(1 downto 0) := (others => '0');
   signal sample_save_scan : std_logic;
   signal sample_save_addr_a : unsigned(7 downto 0);
   signal sample_save_addr_b : tadpcm_addr;
   signal sample_save_data_a : tadpcm_data;
   signal sample_save_we_a : std_logic_vector(3 downto 0);
'''
    text=once(text,anchor,anchor+declarations)
    start=text.index('-- adpcmSamples\n')
    end=text.index('-- envelopecounters',start)
    original=text[start:end]
    replacement=original.replace('std_logic_vector(adpcm_ram_address_a)', 'std_logic_vector(sample_save_addr_a)')
    replacement=replacement.replace('adpcm_ram_data_a(i)', 'sample_save_data_a(i)')
    replacement=replacement.replace('adpcm_ram_wren_a(i)', 'sample_save_we_a(i)')
    replacement=replacement.replace('std_logic_vector(adpcm_ram_address_b(i))', 'std_logic_vector(sample_save_addr_b(i))')
    if replacement==original:
        raise ValueError('SPU decoded sample RAM instance changed')
    routing='''   sample_save_scan <= '1' when save_spu_quiet = '1' and SS_Adr(8 downto 7) = "01" else '0';
   sample_save_addr_a <= sample_save_page(0) & SS_Adr(6 downto 0) when sample_save_scan = '1' else adpcm_ram_address_a;
   sample_save_ports : for bank in 0 to 3 generate
      sample_save_addr_b(bank) <= sample_save_page(0) & SS_Adr(6 downto 0) when sample_save_scan = '1' else adpcm_ram_address_b(bank);
      sample_save_data_a(bank) <= SS_DataWrite(15 downto 0) when sample_save_scan = '1' and bank mod 2 = 0 else
                                 SS_DataWrite(31 downto 16) when sample_save_scan = '1' else adpcm_ram_data_a(bank);
      sample_save_we_a(bank) <= SS_wren when sample_save_scan = '1' and to_integer(sample_save_page(1 downto 1)) = bank / 2 else
                               '0' when sample_save_scan = '1' else adpcm_ram_wren_a(bank);
   end generate;
'''
    text=text[:start]+routing+replacement+text[end:]
    text=once(text,"            sampleticks          <= to_unsigned(512, 10); --unsigned(ss_in(1)(9 downto 0));",
        "            sample_save_page <= (others => '0');\n            sampleticks          <= to_unsigned(512, 10); --unsigned(ss_in(1)(9 downto 0));")
    text=once(text,"         elsif (SS_wren = '1') then\n",'''         elsif (SS_wren = '1') then
            if SS_Adr = 63 then sample_save_page <= unsigned(SS_DataWrite(1 downto 0)); end if;
            if sample_save_scan = '0' then
''')
    text=once(text,"         elsif (ce = '1') then\n", "            end if; -- legacy context write, not decoded sample memory\n         elsif (ce = '1') then\n")
    text=once(text,"            if (SS_Adr(8) = '0') then\n",'''            if SS_Adr(8 downto 7) = "01" then
               if sample_save_page(1) = '0' then
                  SS_DataRead <= adpcm_ram_q_b(1) & adpcm_ram_q_b(0);
               else
                  SS_DataRead <= adpcm_ram_q_b(3) & adpcm_ram_q_b(2);
               end if;
            elsif (SS_Adr(8) = '0') then
''')
    # Last DAC value must survive the pause as well as the decode history.
    text=once(text, "   ss_out(6)(31 downto 0)   <= std_logic_vector(noiseCnt);", 
              "   ss_out(6)(31 downto 0)   <= std_logic_vector(noiseCnt);\n   ss_out(7) <= sound_out_right & sound_out_left;")
    text=once(text, "            if (to_integer(SS_Adr) =  2) then capturePosition",
              "            if SS_Adr = 7 then sound_out_left <= SS_DataWrite(15 downto 0); sound_out_right <= SS_DataWrite(31 downto 16); end if;\n            if (to_integer(SS_Adr) =  2) then capturePosition")
    # Guest register replay must not synthesize new ADSR/repeat writes.
    for flag in ('voice_adsrWrite', 'voice_repeatWrite'):
        old = flag + "(to_integer(bus_addr(8 downto 4))) <= '1';"
        text=once(text, old, "if save_spu_hold = '0' then " + old + ' end if;')
    text=once(text, '   ss_out(7) <= sound_out_right & sound_out_left;',
        '   ss_out(7) <= sound_out_right & sound_out_left;\n'
        '   ss_out(8) <= x"00" & voice_adsrWrite;\n'
        '   ss_out(9) <= x"00" & voice_repeatWrite;')
    text=once(text, "               SS_DataRead <= ss_out(to_integer(SS_Adr(2 downto 0)));",
              "               SS_DataRead <= ss_out(to_integer(SS_Adr(5 downto 0)));")
    text=once(text, '            if SS_Adr = 7 then sound_out_left',
        '            if SS_Adr = 8 then voice_adsrWrite <= SS_DataWrite(23 downto 0); end if;\n'
        '            if SS_Adr = 9 then voice_repeatWrite <= SS_DataWrite(23 downto 0); end if;\n'
        '            if SS_Adr = 7 then sound_out_left')
    # Resampling histories span sample boundaries even at the quiet point.
    reads=[]
    writes=[]
    for word, low, high in ((10,'reverbL_d1','reverbL_d2'),
                           (11,'reverbL_d3','reverbL_d4'),
                           (12,'reverbR_d1','reverbR_d2'),
                           (13,'reverbR_d3','reverbR_d4'),
                           (14,'reverbDownLeft','reverbDownRight')):
        reads.append(f'   ss_out({word}) <= std_logic_vector({high}) & std_logic_vector({low});')
        writes.append(f'            if SS_Adr = {word} then {low} <= signed(SS_DataWrite(15 downto 0)); {high} <= signed(SS_DataWrite(31 downto 16)); end if;')
    for word, name in enumerate(('reverbPartA_L','reverbPartB_L','reverbPartA_R','reverbPartB_R'),15):
        reads.append(f'   ss_out({word}) <= "0000000" & std_logic_vector({name});')
        writes.append(f'            if SS_Adr = {word} then {name} <= signed(SS_DataWrite(24 downto 0)); end if;')
    reads.append('   ss_out(19) <= (31 downto 2 => \'0\') & rvLastValid;')
    writes.append('            if SS_Adr = 19 then rvLastValid <= SS_DataWrite(1 downto 0); end if;')
    for word, name in ((20, 'cd_next_left'), (21, 'cd_next_right')):
        reads.append(f"   ss_out({word}) <= (31 downto 17 => '0') & std_logic_vector({name});")
        writes.append(f'            if SS_Adr = {word} then {name} <= signed(SS_DataWrite(16 downto 0)); end if;')
    reads.append("   ss_out(22) <= (31 downto 1 => '0') & irqSaved;")
    writes.append('            if SS_Adr = 22 then irqSaved <= SS_DataWrite(0); end if;')
    text=once(text,'   ss_out(7) <= sound_out_right & sound_out_left;',
              '   ss_out(7) <= sound_out_right & sound_out_left;\n'+'\n'.join(reads))
    text=once(text,'            if SS_Adr = 7 then sound_out_left',
              '\n'.join(writes)+'\n            if SS_Adr = 7 then sound_out_left')
    # Reuse the original rvWork write port; never infer a second RAM port.
    start=text.index('   process (state, reverb_count)\n   begin\n      rv_work_raddr <= 0;')
    end=text.index('   end process;',start)+len('   end process;')
    text=text[:start]+text[start:end].replace('rv_work_raddr','rv_work_engine_raddr')+text[end:]
    text=once(text,'   signal rv_work_raddr  : integer range 0 to 15;',
              '   signal rv_work_raddr  : integer range 0 to 15;\n   signal rv_work_engine_raddr : integer range 0 to 15;')
    text=once(text,'   rv_work_q <= rvWork(rv_work_raddr);',
              "   rv_work_raddr <= to_integer(SS_Adr(3 downto 0)) when save_spu_quiet = '1' and SS_Adr >= 48 and SS_Adr <= 62 else rv_work_engine_raddr;\n   rv_work_q <= rvWork(rv_work_raddr);")
    text=once(text,"            elsif (SS_Adr(8) = '0') then",
              "            elsif SS_Adr >= 48 and SS_Adr <= 62 then\n               SS_DataRead <= x\"0000\" & rv_work_q;\n            elsif (SS_Adr(8) = '0') then")
    text=once(text,"            if SS_Adr = 63 then sample_save_page",
              "            if SS_Adr >= 48 and SS_Adr <= 62 then\n               rvw_we := '1'; rvw_addr := to_integer(SS_Adr(3 downto 0)); rvw_data := SS_DataWrite(15 downto 0);\n            end if;\n            if SS_Adr = 63 then sample_save_page")
    return text


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Fresh output required')
    args.output.write_text(transform(args.source.read_text()))
