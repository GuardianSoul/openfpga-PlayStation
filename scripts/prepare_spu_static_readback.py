"""Read the SPU's static control registers back from block RAM.

Nineteen of the control registers at 0x1F801D80-DBF only ever hold what the
guest last wrote (main volume, reverb output volume and base, pitch/noise/
reverb enable masks, IRQ and transfer addresses, SPUCNT, transfer control,
CD and external volume). Their 16-bit guest readback multiplexer goes: every
guest write of those addresses is also stored in a 32 x 16 block RAM, read
on the falling clk1x edge like the reverb guest copy (spu-guest-m10k), with
a valid bit per entry for the reset clear. The registers themselves stay for
the engine. KEYON/KEYOFF/ENDX (changed by the voice loop), SPUSTAT and the
current volumes keep their multiplexer. Requires spu-guest-m10k (and so
spu-reverb-table) and savestate transfers off, like those variants.
"""

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest

# bus_addr(5 downto 1) of 0x180/2/4/6, 0x190-0x19A, 0x1A2/4/6, 0x1AA/C, 0x1B0-0x1B6
STATIC = (0, 1, 2, 3, 8, 9, 10, 11, 12, 13, 17, 18, 19, 21, 22, 24, 25, 26, 27)
STATIC_READS = ('VOLUME_LEFT', 'VOLUME_RIGHT', 'PITCHMODENA(15 downto 0)', 'PITCHMODENA(31 downto 16)',
    'NOISEMODE(15 downto 0)', 'NOISEMODE(31 downto 16)', 'REVERBON(15 downto 0)', 'REVERBON(31 downto 16)',
    'IRQ_ADDR', 'TRANSFERADDR', 'CNT', 'TRANSFER_CNT', 'CDAUDIO_VOL_L', 'CDAUDIO_VOL_R', 'EXT_VOL_L', 'EXT_VOL_R',
    'REVERB_vLOUT', 'REVERB_vROUT', 'REVERB_mBASE')


def _replace(text, old, new, count=1):
    if text.count(old) != count: raise ValueError('Pinned SPU source changed near: ' + old.strip()[:60])
    return text.replace(old, new)


def static_readback_text(text):
    if 'rv_guest_ram_q <= rvTable_guest(rv_guest_addr);' not in text:
        raise ValueError('spu-static-readback requires spu-guest-m10k')
    mask = ''.join('1' if i in STATIC else '0' for i in range(31, -1, -1))
    text = _replace(text, '   signal rv_guest_ram_q : std_logic_vector(15 downto 0);\n',
        '   signal rv_guest_ram_q : std_logic_vector(15 downto 0);\n'
        '   -- Static control register readback copy (spu-static-readback).\n'
        '   constant STATIC_RB    : std_logic_vector(31 downto 0) := "' + mask + '";\n'
        '   signal staticRb       : trvTable;\n'
        '   attribute ramstyle of staticRb : signal is "M10K, no_rw_check";\n'
        "   signal staticValid    : std_logic_vector(31 downto 0) := (others => '0');\n"
        '   signal static_ram_q   : std_logic_vector(15 downto 0);\n'
        '   signal static_q       : std_logic_vector(15 downto 0);\n')
    text = _replace(text, "               rvValid              <= (others => '0');\n",
        "               rvValid              <= (others => '0');\n"
        "               staticValid          <= (others => '0');\n")
    text = _replace(text, '''                  if (bus_addr(9 downto 6) = "0111" and bus_addr(0) = '0') then
                     rvTable_guest(rv_guest_addr)  <= bus_dataWrite;
''', '''                  if (bus_addr(9 downto 6) = "0110" and bus_addr(0) = '0' and STATIC_RB(rv_guest_addr) = '1') then
                     staticRb(rv_guest_addr)       <= bus_dataWrite;
                     staticValid(rv_guest_addr)    <= '1';
                  end if;
                  if (bus_addr(9 downto 6) = "0111" and bus_addr(0) = '0') then
                     rvTable_guest(rv_guest_addr)  <= bus_dataWrite;
''')
    text = _replace(text, '''               elsif (bus_addr(9 downto 6) = "0111" and bus_addr(0) = '0') then
                  bus_dataRead <= rv_guest_q;
''', '''               elsif (bus_addr(9 downto 6) = "0111" and bus_addr(0) = '0') then
                  bus_dataRead <= rv_guest_q;
               elsif (bus_addr(9 downto 6) = "0110" and bus_addr(0) = '0' and STATIC_RB(rv_guest_addr) = '1') then
                  bus_dataRead <= static_q;
''')
    head = text.index("               elsif (bus_addr(9 downto 6) = \"0110\" and bus_addr(0) = '0' and STATIC_RB")
    tail = text.index('                     when others => null;', head)
    block = text[head:tail]
    for name in STATIC_READS:
        lines = [l for l in block.splitlines(True) if l.rstrip().endswith('=> bus_dataRead <= ' + name + ';')]
        if len(lines) != 1: raise ValueError('SPU readback arm not found: ' + name)
        block = block.replace(lines[0], '')
    text = text[:head] + block + text[tail:]
    text = _replace(text, '''         rv_guest_ram_q <= rvTable_guest(rv_guest_addr);
''', '''         rv_guest_ram_q <= rvTable_guest(rv_guest_addr);
         static_ram_q   <= staticRb(rv_guest_addr);
''')
    return _replace(text, "   rv_guest_q     <= rv_guest_ram_q when rvValid(rv_guest_addr) = '1'  else (others => '0');\n",
        "   rv_guest_q     <= rv_guest_ram_q when rvValid(rv_guest_addr) = '1'  else (others => '0');\n"
        "   static_q       <= static_ram_q when staticValid(rv_guest_addr) = '1' else (others => '0');\n")
