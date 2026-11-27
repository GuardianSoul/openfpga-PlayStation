-- cd_fifo_mlab: PSX_MiSTer SyncFifoFallThrough (GPL-2.0, Robert Peip and contributors), unchanged except that the
-- memory is held in MLAB (cd-rewrite-061 frees M10K for the CD sub-CPU program). Same ports and behaviour.
library IEEE;
use IEEE.std_logic_1164.all;  
use IEEE.numeric_std.all; 
use ieee.math_real.all;   

entity cd_fifo_mlab is
   generic 
   (
      SIZE              : integer;
      DATAWIDTH         : integer;
      NEARFULLDISTANCE  : integer;
      NEAREMPTYDISTANCE : integer := 0
   );
   port 
   ( 
      clk         : in  std_logic;
      reset       : in  std_logic;
                  
      Din         : in  std_logic_vector(DATAWIDTH - 1 downto 0);
      Wr          : in  std_logic; 
      Full        : out std_logic := '0';
      NearFull    : out std_logic := '0';
         
      Dout        : out std_logic_vector(DATAWIDTH - 1 downto 0) := (others => '0');
      Rd          : in  std_logic;
      Empty       : out std_logic := '1';
      NearEmpty   : out std_logic := '0'
   );
end;

architecture arch of cd_fifo_mlab is

   constant SIZEBITS : integer := integer(ceil(log2(real(SIZE))));

   type t_memory is array(0 to SIZE - 1) of std_logic_vector(DATAWIDTH - 1 downto 0);
   signal memory : t_memory := (others => (others => '0'));
   attribute ramstyle : string;
   attribute ramstyle of memory : signal is "MLAB";   -- cd-rewrite-061: small FIFOs off the M10K  

   signal wrcnt   : unsigned(SIZEBITS - 1 downto 0) := (others => '0');
   signal rdcnt   : unsigned(SIZEBITS - 1 downto 0) := (others => '0');
 
   signal fifocnt : unsigned(SIZEBITS - 1 downto 0) := (others => '0');
 
   constant ONE_BEFORE_FULL : unsigned(SIZEBITS - 1 downto 0) := (0 => '0', others => '1');
   signal full_wire     : std_logic;
   signal empty_wire    : std_logic;

begin

   full_wire      <= '1' when fifocnt = (SIZEBITS - 1 downto 0 => '1')  else '0';
   empty_wire     <= '1' when fifocnt = 0                               else '0';

   process(clk)
      variable newCount : unsigned(SIZEBITS - 1 downto 0);
      variable accepted_write, accepted_read : std_logic;
      variable count_delta : unsigned(SIZEBITS - 1 downto 0);
   begin
      if rising_edge(clk) then
         if (reset = '1') then
            wrcnt   <= (others => '0');
            rdcnt   <= (others => '0');
            fifocnt <= (others => '0');
            Full    <= '0';
            Empty   <= '1';
         else
            accepted_write := Wr and not full_wire;
            accepted_read := Rd and not empty_wire;
            if SIZE = 32 then
               newCount := fifocnt;
               if (Wr = '1' and full_wire = '0') then
                  if (Rd = '0' or empty_wire = '1') then
                     newCount := newCount + 1;
                  end if;
               elsif (Rd = '1' and empty_wire = '0') then
                  newCount := newCount - 1;
               end if;
               fifocnt <= newCount;
            else
               if (accepted_write /= accepted_read) then
                  count_delta := (0 => '1', others => accepted_read);
                  fifocnt <= fifocnt + count_delta;
               end if;
            end if;
            
            if (fifocnt < NEARFULLDISTANCE) then
               NearFull <= '0';
            else
               NearFull <= '1';
            end if;            
            
            if (fifocnt >= NEAREMPTYDISTANCE) then
               NearEmpty <= '0';
            else
               NearEmpty <= '1';
            end if;
         
            if (Wr = '1' and full_wire = '0') then
               memory(to_integer(wrcnt)) <= Din;
               wrcnt <= wrcnt+1;
            end if;
            
            if (Rd = '1' and empty_wire = '0') then
               rdcnt <= rdcnt+1;
               Dout <= memory(to_integer(rdcnt + 1)); 
            else
               Dout <= memory(to_integer(rdcnt)); 
            end if;
            
            if SIZE = 32 then
               if (newCount = 0 or empty_wire = '1' or (fifocnt = 1 and Rd = '1')) then
                  Empty <= '1'; 
               else
                  Empty <= '0';
               end if;
               
               if (newCount = (SIZEBITS - 1 downto 0 => '1') or full_wire = '1') then
                  Full <= '1'; 
               else
                  Full <= '0';
               end if;
            else
               if (empty_wire = '1' or (fifocnt = 1 and accepted_read = '1')) then
                  Empty <= '1'; 
               else
                  Empty <= '0';
               end if;
               
               if (full_wire = '1' or (fifocnt = ONE_BEFORE_FULL and
                                       accepted_write = '1' and accepted_read = '0')) then
                  Full <= '1'; 
               else
                  Full <= '0';
               end if;
            end if;
            
            
         end if;
      end if;
   end process;

end architecture;