-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 PSX Pocket contributors
--
-- Simple dual-port block RAM (one write port, one read port) placed in M10K
-- through altsyncram. Write port: registered address/data/enable on the
-- rising edge of wrclock. Read port: registered address, UNREGISTERED output
-- (q follows the read clock edge by the block's clock-to-output delay).
--
-- DUAL_CLOCK = false: both ports on wrclock (rdclock ignored), mixed-port
--   read-during-write DONT_CARE. A caller that reads the address being
--   written on the same edge must not use q for that read.
-- DUAL_CLOCK = true: the read address register is clocked by rdclock.
--   altsyncram only allows DONT_CARE across clocks; the caller must keep a
--   read of a just-written address at least one write-cycle after the write.
--
-- Cyclone V M10K simple dual-port is at most 40 bits wide (256 x 40), so a
-- WIDTH above 40 occupies ceil(WIDTH / 40) blocks; Quartus splits the width.
--
-- Simulation uses sim/memory/psx_m10k_sdp_model.vhd (same entity), which
-- models the synchronous read and X on a mixed-port collision.

library ieee;
use ieee.std_logic_1164.all;

library altera_mf;
use altera_mf.altera_mf_components.all;

entity psx_m10k_sdp is
   generic
   (
      WIDTH      : natural;
      WIDTHAD    : natural;
      DUAL_CLOCK : boolean := false
   );
   port
   (
      wrclock    : in  std_logic;
      rdclock    : in  std_logic;
      wren       : in  std_logic;
      wraddress  : in  std_logic_vector(WIDTHAD - 1 downto 0);
      data       : in  std_logic_vector(WIDTH - 1 downto 0);
      rdaddress  : in  std_logic_vector(WIDTHAD - 1 downto 0);
      q          : out std_logic_vector(WIDTH - 1 downto 0)
   );
end entity;

architecture rtl of psx_m10k_sdp is
begin

   gsingle : if not DUAL_CLOCK generate
      iram : altsyncram
      generic map
      (
         operation_mode                     => "DUAL_PORT",
         intended_device_family             => "Cyclone V",
         lpm_type                           => "altsyncram",
         ram_block_type                     => "M10K",
         width_a                            => WIDTH,
         widthad_a                          => WIDTHAD,
         numwords_a                         => 2**WIDTHAD,
         width_b                            => WIDTH,
         widthad_b                          => WIDTHAD,
         numwords_b                         => 2**WIDTHAD,
         width_byteena_a                    => 1,
         address_reg_b                      => "CLOCK0",
         outdata_reg_b                      => "UNREGISTERED",
         address_aclr_b                     => "NONE",
         outdata_aclr_b                     => "NONE",
         clock_enable_input_a               => "BYPASS",
         clock_enable_input_b               => "BYPASS",
         clock_enable_output_b              => "BYPASS",
         power_up_uninitialized             => "FALSE",
         read_during_write_mode_mixed_ports => "DONT_CARE"
      )
      port map
      (
         clock0    => wrclock,
         address_a => wraddress,
         data_a    => data,
         wren_a    => wren,
         address_b => rdaddress,
         q_b       => q
      );
   end generate;

   gdual : if DUAL_CLOCK generate
      iram : altsyncram
      generic map
      (
         operation_mode                     => "DUAL_PORT",
         intended_device_family             => "Cyclone V",
         lpm_type                           => "altsyncram",
         ram_block_type                     => "M10K",
         width_a                            => WIDTH,
         widthad_a                          => WIDTHAD,
         numwords_a                         => 2**WIDTHAD,
         width_b                            => WIDTH,
         widthad_b                          => WIDTHAD,
         numwords_b                         => 2**WIDTHAD,
         width_byteena_a                    => 1,
         address_reg_b                      => "CLOCK1",
         outdata_reg_b                      => "UNREGISTERED",
         address_aclr_b                     => "NONE",
         outdata_aclr_b                     => "NONE",
         clock_enable_input_a               => "BYPASS",
         clock_enable_input_b               => "BYPASS",
         clock_enable_output_b              => "BYPASS",
         power_up_uninitialized             => "FALSE",
         read_during_write_mode_mixed_ports => "DONT_CARE"
      )
      port map
      (
         clock0    => wrclock,
         clock1    => rdclock,
         address_a => wraddress,
         data_a    => data,
         wren_a    => wren,
         address_b => rdaddress,
         q_b       => q
      );
   end generate;

end architecture;
