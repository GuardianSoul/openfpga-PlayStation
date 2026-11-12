-- SPDX-License-Identifier: MIT
-- Minimal behavioural stand-in for altera_mf.altsyncram, for benches run where
-- the Quartus simulation library is not installed. It covers only what
-- dpram.vhd, dpram_dif and pocket_dpram_sdp.vhd use: two ports with their own
-- widths (mixed-width little-endian packing), registered address, unregistered
-- output, clock enables, same-port read-during-write returns new data and
-- mixed-port read-during-write returns old data. It is not a timing model of
-- the M10K; a lockstep bench that compares two designs on the same model is
-- unaffected by that.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

package altera_mf_components is
   component altsyncram
      generic (
         address_reg_b, clock_enable_input_a, clock_enable_input_b, clock_enable_output_a,
         clock_enable_output_b, indata_reg_b, intended_device_family, lpm_type, operation_mode,
         ram_block_type, outdata_aclr_a, outdata_aclr_b, outdata_reg_a, outdata_reg_b,
         power_up_uninitialized, read_during_write_mode_port_a, read_during_write_mode_port_b,
         read_during_write_mode_mixed_ports, wrcontrol_wraddress_reg_b, init_file : string := "UNUSED";
         numwords_a, numwords_b, widthad_a, widthad_b, width_a, width_b : natural := 1;
         width_byteena_a, width_byteena_b : natural := 1);
      port (
         address_a : in std_logic_vector(widthad_a - 1 downto 0);
         address_b : in std_logic_vector(widthad_b - 1 downto 0) := (others => '1');
         clock0    : in std_logic := '1';
         clock1    : in std_logic := '1';
         clocken0  : in std_logic := '1';
         clocken1  : in std_logic := '1';
         data_a    : in std_logic_vector(width_a - 1 downto 0) := (others => '1');
         data_b    : in std_logic_vector(width_b - 1 downto 0) := (others => '1');
         wren_a    : in std_logic := '0';
         wren_b    : in std_logic := '0';
         q_a       : out std_logic_vector(width_a - 1 downto 0);
         q_b       : out std_logic_vector(width_b - 1 downto 0));
   end component;
end package;

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity altsyncram is
   generic (
      address_reg_b, clock_enable_input_a, clock_enable_input_b, clock_enable_output_a,
      clock_enable_output_b, indata_reg_b, intended_device_family, lpm_type, operation_mode,
      ram_block_type, outdata_aclr_a, outdata_aclr_b, outdata_reg_a, outdata_reg_b,
      power_up_uninitialized, read_during_write_mode_port_a, read_during_write_mode_port_b,
      read_during_write_mode_mixed_ports, wrcontrol_wraddress_reg_b, init_file : string := "UNUSED";
      numwords_a, numwords_b, widthad_a, widthad_b, width_a, width_b : natural := 1;
      width_byteena_a, width_byteena_b : natural := 1);
   port (
      address_a : in std_logic_vector(widthad_a - 1 downto 0);
      address_b : in std_logic_vector(widthad_b - 1 downto 0) := (others => '1');
      clock0    : in std_logic := '1';
      clock1    : in std_logic := '1';
      clocken0  : in std_logic := '1';
      clocken1  : in std_logic := '1';
      data_a    : in std_logic_vector(width_a - 1 downto 0) := (others => '1');
      data_b    : in std_logic_vector(width_b - 1 downto 0) := (others => '1');
      wren_a    : in std_logic := '0';
      wren_b    : in std_logic := '0';
      q_a       : out std_logic_vector(width_a - 1 downto 0);
      q_b       : out std_logic_vector(width_b - 1 downto 0));
end entity;

architecture model of altsyncram is
   constant BITS : natural := numwords_a * width_a;
begin
   process(clock0, clock1)
      variable mem : std_logic_vector(BITS - 1 downto 0) := (others => '0');
      variable oa, ob : natural;
      variable rb : std_logic_vector(width_b - 1 downto 0);
      variable doa, dob : boolean;
   begin
      doa := rising_edge(clock0) and clocken0 = '1';
      dob := rising_edge(clock1) and clocken1 = '1';
      if not is_x(address_a) then oa := (to_integer(unsigned(address_a)) * width_a) mod BITS; else oa := 0; end if;
      if not is_x(address_b) then ob := (to_integer(unsigned(address_b)) * width_b) mod BITS; else ob := 0; end if;
      rb := mem(ob + width_b - 1 downto ob);   -- mixed port: old data
      if dob and wren_b = '1' then mem(ob + width_b - 1 downto ob) := data_b; rb := data_b; end if;
      if doa and wren_a = '1' then mem(oa + width_a - 1 downto oa) := data_a; end if;
      if doa then q_a <= mem(oa + width_a - 1 downto oa); end if;
      if dob then q_b <= rb; end if;
   end process;
end architecture;
