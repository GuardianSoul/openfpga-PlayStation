-- SPDX-License-Identifier: GPL-2.0-only
-- Fixed coefficient products sampled on the original Cr/Cb capture edges.
library ieee;
use ieee.std_logic_1164.all;
library altera_mf;
use altera_mf.altera_mf_components.all;
entity psx_mdec_chroma_rom is
   port(clk,cr_enable,cb_enable: in std_logic;
        address: in std_logic_vector(7 downto 0);
        cr_q: out std_logic_vector(22 downto 0);
        cb_q: out std_logic_vector(19 downto 0));
end entity;
architecture rtl of psx_mdec_chroma_rom is
begin
   cr: altsyncram generic map(
      operation_mode=>"ROM",width_a=>23,widthad_a=>8,numwords_a=>256,
      outdata_reg_a=>"UNREGISTERED",clock_enable_input_a=>"NORMAL",
      clock_enable_output_a=>"BYPASS",ram_block_type=>"M10K",
      intended_device_family=>"Cyclone V",init_file=>"psx_mdec_chroma_cr.mif")
      port map(clock0=>clk,clocken0=>cr_enable,address_a=>address,q_a=>cr_q);
   cb: altsyncram generic map(
      operation_mode=>"ROM",width_a=>20,widthad_a=>8,numwords_a=>256,
      outdata_reg_a=>"UNREGISTERED",clock_enable_input_a=>"NORMAL",
      clock_enable_output_a=>"BYPASS",ram_block_type=>"M10K",
      intended_device_family=>"Cyclone V",init_file=>"psx_mdec_chroma_cb.mif")
      port map(clock0=>clk,clocken0=>cb_enable,address_a=>address,q_a=>cb_q);
end architecture;
