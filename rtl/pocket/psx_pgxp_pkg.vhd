-- SPDX-License-Identifier: MIT
-- Exact halfword transport for PGXP L3. Unsupported arithmetic invalidates
-- precision; it never changes guest data or invents a fractional coordinate.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

package psx_pgxp_pkg is
   subtype pgxp_meta is std_logic_vector(9 downto 0);
   constant PGXP_NONE : pgxp_meta := (others => '0');
   function pgxp_clean(m : pgxp_meta) return pgxp_meta;
   function pgxp_execute(insn : unsigned(31 downto 0); a,b : pgxp_meta)
      return pgxp_meta;
   -- kind uses the selected CPU_LOADTYPE enumeration's position:
   -- signed byte, signed halfword, left, word, byte, halfword, right.
   function pgxp_load(kind : natural; offset : unsigned(1 downto 0);
                      memory_meta, old_meta : pgxp_meta) return pgxp_meta;
   -- Metadata is placed beside the already aligned store data/mask.
   function pgxp_store(op : unsigned(5 downto 0); offset : unsigned(1 downto 0);
                       source_meta : pgxp_meta) return pgxp_meta;
end package;

package body psx_pgxp_pkg is
   function pgxp_clean(m : pgxp_meta) return pgxp_meta is
      variable r : pgxp_meta := m;
   begin
      if m(8)='0' then r(3 downto 0):=(others=>'0'); end if;
      if m(9)='0' then r(7 downto 4):=(others=>'0'); end if;
      return r;
   end function;

   function pgxp_execute(insn : unsigned(31 downto 0); a,b : pgxp_meta)
      return pgxp_meta is
      variable op,fn : natural;
      variable r : pgxp_meta := PGXP_NONE;
   begin
      op:=to_integer(insn(31 downto 26)); fn:=to_integer(insn(5 downto 0));
      if op=0 then
         if (fn=0 or fn=2 or fn=3) and insn(10 downto 6)=0 then
            r:=b;
         elsif fn=16#20# or fn=16#21# or fn=16#25# or fn=16#26# then
            if insn(20 downto 16)=0 then r:=a;
            elsif insn(25 downto 21)=0 then r:=b; end if;
         elsif (fn=16#22# or fn=16#23#) and insn(20 downto 16)=0 then
            r:=a;
         end if;
      elsif (op=8 or op=9 or op=13 or op=14) and insn(15 downto 0)=0 then
         r:=a;
      elsif op=12 and insn(15 downto 0)=16#FFFF# then
         r(8):=a(8); r(3 downto 0):=a(3 downto 0);
      elsif op=16#22# or op=16#26# then
         -- LWL/LWR preserve the old target for their later byte merge.
         r:=b;
      end if;
      return pgxp_clean(r);
   end function;

   function pgxp_load(kind : natural; offset : unsigned(1 downto 0);
                      memory_meta, old_meta : pgxp_meta) return pgxp_meta is
      variable r : pgxp_meta := PGXP_NONE;
      variable n : natural := to_integer(offset);
   begin
      case kind is
         when 1 | 5 =>
            if offset(1)='0' then
               r(8):=memory_meta(8); r(3 downto 0):=memory_meta(3 downto 0);
            else
               r(8):=memory_meta(9); r(3 downto 0):=memory_meta(7 downto 4);
            end if;
         when 3 => r:=memory_meta;
         when 2 =>
            case n is
               when 0 => r(8):=old_meta(8); r(3 downto 0):=old_meta(3 downto 0);
               when 1 =>
                  r(8):=old_meta(8); r(3 downto 0):=old_meta(3 downto 0);
                  r(9):=memory_meta(8); r(7 downto 4):=memory_meta(3 downto 0);
               when 3 => r:=memory_meta;
               when others => null;
            end case;
         when 6 =>
            case n is
               when 0 => r:=memory_meta;
               when 2 =>
                  r(8):=memory_meta(9); r(3 downto 0):=memory_meta(7 downto 4);
                  r(9):=old_meta(9); r(7 downto 4):=old_meta(7 downto 4);
               when 3 => r(9):=old_meta(9); r(7 downto 4):=old_meta(7 downto 4);
               when others => null;
            end case;
         when others => null;
      end case;
      return pgxp_clean(r);
   end function;

   function pgxp_store(op : unsigned(5 downto 0); offset : unsigned(1 downto 0);
                       source_meta : pgxp_meta) return pgxp_meta is
      variable r : pgxp_meta := PGXP_NONE;
   begin
      case to_integer(op) is
         when 16#2B# | 16#3A# => r:=source_meta;
         when 16#29# =>
            if offset(1)='0' then
               r(8):=source_meta(8); r(3 downto 0):=source_meta(3 downto 0);
            else
               r(9):=source_meta(8); r(7 downto 4):=source_meta(3 downto 0);
            end if;
         when 16#2A# =>
            if offset=1 then
               r(8):=source_meta(9); r(3 downto 0):=source_meta(7 downto 4);
            elsif offset=3 then r:=source_meta; end if;
         when 16#2E# =>
            if offset=0 then r:=source_meta;
            elsif offset=2 then
               r(9):=source_meta(8); r(7 downto 4):=source_meta(3 downto 0);
            end if;
         when others => null;
      end case;
      return pgxp_clean(r);
   end function;
end package body;
