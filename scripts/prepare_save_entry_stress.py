"""Add targeted observations to our authored save-entry simulation fixture.

No native DMA engine or physical clock/CDC model is added.
"""
from pathlib import Path


def stress_bench(text, disconnect_dma=False):
    replacements = [
        ('      SAVE_AT    : natural  := 0;\n',
         '      SAVE_AT    : natural  := 0;\n'
         '      DMA_AT     : natural  := 0;\n      DMA_FOR    : natural  := 0;\n'
         '      IRQ_AT     : natural  := 0;\n      IRQ_FOR    : natural  := 0;\n'),
        ("   signal ce : std_logic := '1';\n",
         "   signal ce : std_logic := '1';\n"
         "   signal stress_dma, stress_irq, previous_dma : std_logic := '0';\n"
         '   signal stress_dma_cycles, stress_irq_cycles : natural := 0;\n'),
        ('   pause_next <= cpuPaused;\n',
         '   pause_next <= cpuPaused;\n'
         "   stress_dma <= '1' when DMA_AT > 0 and cycle_count >= DMA_AT and cycle_count < DMA_AT + DMA_FOR else '0';\n"
         "   stress_irq <= '1' when IRQ_AT > 0 and cycle_count >= IRQ_AT and cycle_count < IRQ_AT + IRQ_FOR else '0';\n"
         '   process(clk1x) begin\n      if rising_edge(clk1x) then\n'
         '         previous_dma <= stress_dma;\n'
         "         if stress_dma = '1' then stress_dma_cycles <= stress_dma_cycles + 1; end if;\n"
         "         if stress_irq = '1' then stress_irq_cycles <= stress_irq_cycles + 1; end if;\n"
         "         if reset = '0' and previous_dma = '1' then\n"
         "            assert cpu_cpu_done = '0' report \"CPU retired while DMA stall input held\" severity failure;\n"
         '         end if;\n      end if;\n   end process;\n'),
        ("      irqRequest => '0',\n", '      irqRequest => stress_irq,\n'),
        ("      dmaStallCPU => '0',\n",
         "      dmaStallCPU => '0', -- deliberate disconnected input\n" if disconnect_dma else
         '      dmaStallCPU => stress_dma,\n'),
        ('" retries=" & integer\'image(retries) & " spu_writes="',
         '" retries=" & integer\'image(retries) & " stress_dma=" & integer\'image(stress_dma_cycles) & '
         '" stress_irq=" & integer\'image(stress_irq_cycles) & " spu_writes="'),
    ]
    for before, after in replacements:
        if text.count(before) != 1:
            raise ValueError('save-entry stress fixture anchor changed: ' + before.strip())
        text = text.replace(before, after)
    return text


def irq_enabled_boot(text):
    if text.splitlines().count('35080001') != 1:
        raise ValueError('expected one bootstrap SR immediate')
    return text.replace('35080001\n', '35080401\n')


def kernel_guest(directory):
    """Run an authored prefix setting k0/k1, with all normal IRQ masks clear."""
    directory = Path(directory)
    rom = directory / 'rom.hex'
    text = rom.read_text()
    if text.splitlines().count('375a1000') != 1 or text.splitlines().count('35080001') != 1:
        raise ValueError('expected masked-IRQ bootstrap and one game jump')
    ram = directory / 'ram.hex'
    if any(0xF00 <= int(line.split()[0], 16) < 0xF18 for line in ram.read_text().splitlines()):
        raise ValueError('kernel-GPR prefix overlaps the authored guest')
    prefix = (0x3C1A1357, 0x375A9BDF, 0x3C1B2468, 0x377BACE0, 0x08000400, 0)
    words = {int(a, 16): int(w, 16) for a, w in (line.split() for line in ram.read_text().splitlines())}
    finish = [a for a, w in words.items() if w == 0xAF19FF00]
    if len(finish) != 1 or words.get(finish[0]+4) != 0x1000FFFF:
        raise ValueError('expected one finish store and loop')
    if any(0xE00 <= a < 0xE10 for a in words):
        raise ValueError('kernel-GPR exit witness overlaps guest')
    words[finish[0]], words[finish[0]+4] = 0x08000380, 0  # trampoline; safe delay slot
    # This ordinary zero-base store must reach guest RAM after hidden RFE.
    # A stuck hidden-context flag redirects it into EXP1 ROM, caught by dump.
    for i, word in enumerate((0xAC1A0800, 0xAF19FF00, 0x1000FFFF, 0)):
        words[0xE00+4*i] = word
    for i, word in enumerate(prefix):
        words[0xF00+4*i] = word
    ram.write_text(''.join(f'{a:08x} {w:08x}\n' for a, w in sorted(words.items())))
    rom.write_text(text.replace('375a1000\n', '375a0f00\n'))


def kernel_bench(text):
    """Observe kernel GPRs at the guest's finish marker, independently of its stores."""
    anchor = '                  for k in 0 to 6 loop\n'
    stats = '" retries=" & integer\'image(retries) & " spu_writes="'
    if text.count(anchor) != 1 or text.count(stats) != 1:
        raise ValueError('kernel-GPR dump/statistics fixture anchors changed')
    text = text.replace(anchor,
        '                  hwrite(row, std_logic_vector(cpu_cpu_export.regs(26))); writeline(dump, row);\n'
        '                  hwrite(row, std_logic_vector(cpu_cpu_export.regs(27))); writeline(dump, row);\n' + anchor)
    return text.replace(stats,
        '" retries=" & integer\'image(retries) & " kernel_k0=" & to_hstring(cpu_cpu_export.regs(26)) & '
        '" kernel_k1=" & to_hstring(cpu_cpu_export.regs(27)) & " spu_writes="')


def kernel_guard_guest(directory, unusable=False):
    """Ordinary zero-base SW/LW and load-delay JR, or COP0 unusable exceptions."""
    directory = Path(directory)
    rom = directory / 'rom.hex'
    text = rom.read_text()
    if text.splitlines().count('3c084000') != 1:
        raise ValueError('expected one bootstrap SR high immediate')
    if not unusable:
        rom.write_text(text.replace('3c084000\n', '3c085000\n'))  # CU0 makes LWC0/SWC0 ordinary NOPs
    words = {}
    def put(start, values):
        for i, value in enumerate(values): words[start + 4*i] = value
    put(0, [0x3C088000, 0x8D091068, 0, 0x3C18A001, 0x3C19600D,
            0x3739F00D, 0xAF19FF00, 0x1000FFFF, 0])
    put(0x80, [0x401A7000, 0, 0x275A0004, 0x03400008, 0x42000010])
    if unusable:
        put(0x1000, [0xE01A1068, 0xC01A1068, 0x3C1AA5A5, 0x375A5A5A,
                     0x3C1B2468, 0x377BACE0, 0x409A7000, 0x00000008, 0])
    else:
        put(0x1000, [0x0000000C, 0x3C1A1357, 0x375A9BDF, 0x3C1B2468, 0x377BACE0,
                     0xAC1A1068, 0x3C1A0000, 0x8C1A1068, 0x03400008, 0])
    (directory / 'ram.hex').write_text(''.join(f'{a:08x} {w:08x}\n' for a, w in sorted(words.items())))


def kernel_guard_bench(text):
    text = kernel_bench(text)
    anchor = "   signal ce : std_logic := '1';\n"
    if text.count(anchor) != 1:
        raise ValueError('kernel guard declaration anchor changed')
    text = text.replace(anchor, anchor + '   signal normal_exceptions : natural := 0;\n')
    anchor = '            retired <= retired + 1;\n'
    if text.count(anchor) != 1:
        raise ValueError('kernel guard retirement anchor changed')
    text = text.replace(anchor, anchor +
        '            if cpu_cpu_export.pc = x"80000080" then normal_exceptions <= normal_exceptions + 1; end if;\n')
    anchor = 'assert cpu_cpu_export.pc /= x"80000080" and cpu_cpu_export.pc /= x"BFC00180"'
    if text.count(anchor) != 1:
        raise ValueError('kernel guard normal exception anchor changed')
    text = text.replace(anchor, 'assert cpu_cpu_export.pc /= x"BFC00180"')
    return text.replace('" spu_writes=" & integer\'image(spu_guest_writes)',
        '" normal_exceptions=" & integer\'image(normal_exceptions) & '
        '" kernel_alias=" & to_hstring(cpu_cpu_export.regs(9)) & " spu_writes=" & integer\'image(spu_guest_writes)')
