#!/usr/bin/env python3
"""Memory card buffer without its two 64-bit copy registers (joypad_mem).

MiSTer's joypad_mem moves a 128-byte sector between its buffer RAM and the
card memory as sixteen 64-bit words on clk2x. Two 64-bit registers sit in
that path: mem_DataInB copies each read word before it is written into the
buffer, and mem_DIN copies each buffer word before it is offered for a card
write. Neither is needed:

- A read word is written straight from mem_DOUT on the clock it arrives
  (port B address and write enable come from the same state, so the buffer
  is written one clk2x edge earlier, with the same address and data).
- The buffer's port-B address stays put from the MEMCPUWRITE_READDATA state
  until the write is acknowledged, so its output already holds the word for
  the whole request; mem_DIN is that output.

The guest-side (clk1x) protocol and every card memory request are unchanged.
mem_DIN differs only while no write is requested.
"""
import argparse
from pathlib import Path


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('Pinned joypad_mem source changed near: ' + old.strip().splitlines()[0])
    return text.replace(old, new)


def memcard_lean_text(text):
    text = _replace(text, '''   signal mem_wrenB     : std_logic := '0';
''', '''   signal mem_wrenB     : std_logic := '0';
   -- memcard-lean: card read words go straight into the buffer.
   signal mem_addrB_in  : std_logic_vector(3 downto 0);
   signal mem_wrenB_in  : std_logic;
''')
    text = _replace(text, '''      address_b   => mem_addrB,
      data_b      => mem_DataInB,
      wren_b      => mem_wrenB,
''', '''      address_b   => mem_addrB_in,
      data_b      => mem_DOUT,
      wren_b      => mem_wrenB_in,
''')
    text = _replace(text, '''   process (clk2x)
   begin
      if rising_edge(clk2x) then
''', '''   mem_wrenB_in <= '1' when (reset = '0' and memstate = MEMCPUREAD_READDATA and mem_DOUT_READY = '1') else '0';
   mem_addrB_in <= readCnt2X when memstate = MEMCPUREAD_READDATA else mem_addrB;
   -- The buffer address is held from MEMCPUWRITE_READDATA until the ack.
   mem_DIN      <= mem_DataOutB;

   process (clk2x)
   begin
      if rising_edge(clk2x) then
''')
    text = _replace(text, '''                  mem_DIN      <= mem_DataOutB;
''', '')
    text = _replace(text, '''                     mem_addrB   <= readCnt2X;
                     mem_DataInB <= mem_DOUT;
                     mem_wrenB   <= '1';
''', '')
    text = _replace(text, "      mem_DIN              : out std_logic_vector(63 downto 0) := (others => '0');",
                    "      mem_DIN              : out std_logic_vector(63 downto 0);")
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.write_text(memcard_lean_text(args.source.read_text()))


if __name__ == '__main__':
    main()
