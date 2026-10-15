library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

-- Tag-only model of rtl/dpram.vhd's CLOCK1 address register and UNREGISTERED
-- q_b. Upstream's portable dpram instead registers the read data; exercise
-- both descriptions, including an A-port write while B retains its address.
-- This is a functional phase model, not an Intel collision/timing simulation.
entity cpu_tag_phase_model is
    generic (addr_width : positive := 8; data_width : positive := 24);
    port (clock_a : in std_logic; address_a : in std_logic_vector(addr_width-1 downto 0);
          data_a : in std_logic_vector(data_width-1 downto 0); wren_a : in std_logic;
          q_a : out std_logic_vector(data_width-1 downto 0);
          clock_b : in std_logic; address_b : in std_logic_vector(addr_width-1 downto 0);
          data_b : in std_logic_vector(data_width-1 downto 0); wren_b : in std_logic;
          q_b : out std_logic_vector(data_width-1 downto 0));
end entity;

architecture model of cpu_tag_phase_model is
    type memory is array(0 to 2**addr_width-1) of std_logic_vector(data_width-1 downto 0);
    signal ram : memory := (others=>(others=>'0'));
    signal held_address : std_logic_vector(addr_width-1 downto 0) := (others=>'0');
begin
    assert wren_b='0' report "tag phase model only implements B-port reads" severity failure;
    q_a<=(others=>'0'); -- unused by the tag instance
    process(clock_a) begin
        if rising_edge(clock_a) and wren_a='1' then
            ram(to_integer(unsigned(address_a)))<=data_a;
        end if;
    end process;
    process(clock_b) begin
        if rising_edge(clock_b) then held_address<=address_b; end if;
    end process;
    q_b<=ram(to_integer(unsigned(held_address)));
end architecture;
