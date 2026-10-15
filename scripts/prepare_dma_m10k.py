"""DMA channel tables in block RAM read on the falling clk1x edge (dma-m10k).

dma-channel-ram and dma-chcr-ram keep MADR, BCR and the static CHCR fields
in six asynchronous-read MLABs (a guest-read copy and a trigger-read copy of
each). An MLAB costs ten ALMs of logic area. Their read addresses are
bus_addr(6 downto 4) and triggerchannel, both clk1x registers, and every
write is on the rising clk1x edge. A block RAM that samples the same
address on the falling edge delivers, before the next rising edge, exactly
the word the MLAB read showed at that edge (a write on the rising edge is
visible to the following falling-edge read, as it was to the asynchronous
read), the same technique as spu-guest-m10k. The valid bits stay in
flip-flops. Requires dma-chcr-ram. No cycle changes; the reads become
half-cycle paths that the full fit must meet.
"""
import re

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest

TABLES = (('madrMem_bus', 'bus_MADR', 'bus_channel', 'madrValid', 23),
          ('madrMem_trig', 'trig_MADR', 'triggerchannel', 'madrValid', 23),
          ('bcrMem_bus', 'bus_BCR', 'bus_channel', 'bcrValid', 31),
          ('bcrMem_trig', 'trig_BCR', 'triggerchannel', 'bcrValid', 31),
          ('chcrMem_bus', 'bus_CHCRs', 'bus_channel', 'chcrValid', 12),
          ('chcrMem_trig', 'trig_CHCRs', 'triggerchannel', 'chcrValid', 12))


def _replace(text, old, new, count=1):
    if text.count(old) != count: raise ValueError('Pinned DMA source changed near: ' + old.strip()[:60])
    return text.replace(old, new)


def dma_m10k_text(text):
    if 'chcrMem_trig' not in text: raise ValueError('dma-m10k requires dma-chcr-ram')
    declarations = '   -- Falling-edge block RAM reads (dma-m10k).\n'
    reads = '   process (clk1x)\n   begin\n      if falling_edge(clk1x) then\n'
    for table, output, address, valid, top in TABLES:
        text, n = re.subn(r'(   attribute ramstyle of ' + table + r' +: signal is )"MLAB, no_rw_check";', r'\1"M10K, no_rw_check";', text)
        if n != 1: raise ValueError('DMA table attribute not found: ' + table)
        declarations += f'   signal {table}_q : unsigned({top} downto 0);\n'
        reads += f'         {table}_q <= {table}({address});\n'
        old = [l for l in text.splitlines(True) if l.startswith(f'   {output} ') and f'<= {table}({address})' in l]
        if len(old) != 1: raise ValueError('DMA table read not found: ' + table)
        text = text.replace(old[0], old[0].replace(f'{table}({address})', f'{table}_q'))
    reads += '      end if;\n   end process;\n'
    text = _replace(text, '   signal trig_CHCRs   : unsigned(12 downto 0);\n',
                    '   signal trig_CHCRs   : unsigned(12 downto 0);\n' + declarations)
    return _replace(text, "   bus_channel <= to_integer(unsigned(bus_addr(6 downto 4)));\n",
                    "   bus_channel <= to_integer(unsigned(bus_addr(6 downto 4)));\n" + reads)
