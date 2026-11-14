#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Release form of the main-RAM face, stages 1-2 (docs/memctrl-redesign.md), on K6's chain.

Applied to a frozen snapshot that already carries CPU/mem's K4 RAM-wait set (K6 = k6_9-pacereg).
Rig form: prepare_ram_face.py (face=m4 / decode). Text functions, one per file:

memorymux_text  memorymux.vhd: the PS1 face.
  - upstream MiSTer load countdown (the load-window step's readram hold is removed);
  - early send: a RAM read that memorymux parks in WAITFORRAMREAD gets ram_ena on the edge
    that parks it, not one clk1x later;
  - fixed release: inside memorymux, ram_done of a scalar RAM read is held back to the
    clk1x MiSTer's fixed-latency controller would give it (3 clk1x after ram_ena, 4 after
    an early send), or passed when the data is later. Never earlier.
psx_top_text    psx_top.vhd: the registered read hint (K4) now takes the CPU's pending
  stage-4 read (mem_isData and mem_rnw, not gated by mem_request: the next load waits
  there while the previous access is served), and the DMA's next read address while the
  DMA asks for or holds the bus.
dma_text        dma.vhd: two outputs, ramHint_ena / ramHint_adr (trigger channel's MADR
  while a request waits in OFF, the advanced ram_Adr while the channel reads RAM).
controller_text psx_sdram_ctrl.sv: prepare_ram_face.controller_text (hint ACTIVE, hint
  PRECHARGE from hint_st, tRAS guard, hint survival).

Usage: prepare_ram_face_release.py SOURCE --output OUTPUT [--seed N] [--fault NAME] [--no-early-refresh]
Faults (rig/bench names): late-release, early-release, no-ras, act-no-open.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_ram_face as rig

TAG = 'ram-face'


def once(s, old, new, count=1):
    if s.count(old) != count:
        raise ValueError(f'{TAG}: expected {count} of {old[:80]!r}, found {s.count(old)}')
    return s.replace(old, new)


def memorymux_text(text, fault=None):
    if TAG in text:
        raise ValueError('memorymux already carries ' + TAG)
    head, sep, body = text.partition('\nbegin \n')
    if not sep:
        head, sep, body = text.partition('\nbegin\n')
    if 'architecture arch of memorymux' not in head or not sep:
        raise ValueError(f'{TAG}: memorymux architecture begin not found')
    # Everything in the architecture sees the released done; only the face sees the port.
    n = body.count('ram_done')
    if n != 9:
        raise ValueError(f'{TAG}: expected 9 ram_done uses in the memorymux body, found {n}')
    body = body.replace('ram_done', 'ram_done_ps1')
    head = once(head, '   signal readram                : std_logic := \'0\';\n',
                '   signal readram                : std_logic := \'0\';\n'
                f'   -- {TAG}: PS1 face (scalar RAM reads released at the fixed MiSTer time)\n'
                '   signal face_pend              : std_logic := \'0\';\n'
                '   signal face_got               : std_logic := \'0\';\n'
                '   signal face_rel               : unsigned(2 downto 0) := (others => \'0\');\n'
                '   signal face_early_now         : std_logic;\n'
                '   signal face_win_now           : std_logic;\n'
                '   signal face_done              : std_logic;\n'
                '   signal ram_done_ps1           : std_logic;\n')
    due_early, due_win = {'early-release': (3, 2), 'late-release': (5, 4)}.get(fault, (4, 3))
    face = f"""
   -- {TAG}: a read parked in WAITFORRAMREAD was sent on the parking edge (early send);
   -- any other scalar RAM read starts its release time when ram_ena is seen.
   face_early_now <= '1' when (state = WAITFORRAMREAD and face_pend = '0') else '0';
   face_win_now   <= '1' when (ram_ena = '1' and ram_rnw = '1' and ram_cache = '0' and state /= WAITFORRAMREAD) else '0';
   face_done      <= '1' when (face_pend = '1' and face_rel = 1 and (face_got = '1' or ram_done = '1')) else '0';
   ram_done_ps1   <= face_done when (face_pend = '1') else ram_done;

   process (clk1x)
   begin
      if rising_edge(clk1x) then
         if (face_early_now = '1' or face_win_now = '1') then
            face_pend <= '1';
            face_got  <= '0';
            if (face_early_now = '1') then face_rel <= to_unsigned({due_early}, 3); else face_rel <= to_unsigned({due_win}, 3); end if;
         else
            if (face_rel > 1) then face_rel <= face_rel - 1; end if;
            if (face_pend = '1' and ram_done = '1') then face_got <= '1'; end if;
            if (face_done = '1') then
               face_pend <= '0';
               face_got  <= '0';
               face_rel  <= (others => '0');
            end if;
         end if;
         if (reset = '1') then
            face_pend <= '0';
            face_got  <= '0';
            face_rel  <= (others => '0');
         end if;
      end if;
   end process;
"""
    body = face + body
    # upstream countdown
    body = once(body, "         if (ram_load_last > 0 and readram = '0') then -- load-window: hold while a read is in flight\n",
                f"         if (ram_load_last > 0) then -- {TAG}: upstream countdown\n")
    # early send: instruction fetch (uncached) and data load parked in WAITFORRAMREAD
    body = once(body,
                "                                 state   <= WAITFORRAMREAD;\n"
                "                                 waitcnt <= 0;\n"
                "                                 ram_ena <= '0';\n"
                "                                 readram <= '0';\n",
                "                                 state   <= WAITFORRAMREAD;\n"
                "                                 waitcnt <= 0;\n"
                f"                                 ram_ena <= '1'; -- {TAG}: early send\n"
                "                                 readram <= '0';\n")
    body = once(body,
                "                              else\n"
                "                                 state   <= WAITFORRAMREAD;\n"
                "                                 waitcnt <= 0;\n"
                "                              end if;\n",
                "                              else\n"
                "                                 state   <= WAITFORRAMREAD;\n"
                "                                 waitcnt <= 0;\n"
                f"                                 ram_ena <= '1'; -- {TAG}: early send\n"
                "                              end if;\n")
    body = once(body,
                "               when WAITFORRAMREAD =>\n"
                "                  if (waitcnt > 0) then\n"
                "                     waitcnt <= waitcnt - 1;\n"
                "                  else\n"
                "                     state   <= IDLE;\n"
                "                     ram_ena <= '1';\n"
                "                     readram <= '1';\n",
                "               when WAITFORRAMREAD =>\n"
                "                  if (waitcnt > 0) then\n"
                "                     waitcnt <= waitcnt - 1;\n"
                "                  else\n"
                "                     state   <= IDLE;\n"
                f"                     -- {TAG}: sent on the parking edge already\n"
                "                     readram <= '1';\n")
    return head + sep + body


PSX_TOP_HINT = ("         if (mem_request = '1' and mem_rnw = '1' and mem_isData = '1' and dmaOn = '0' and mem_addressData(28 downto 0) < 16#800000#) then\n"
                "            ram_hint_req <= '1';\n"
                "         else\n"
                "            ram_hint_req <= '0';\n"
                "         end if;\n"
                "         ram_hint_adr <= \"00\" & (std_logic_vector(mem_addressData(22 downto 21)) and (save_mode & save_mode))\n"
                "                         & std_logic_vector(mem_addressData(20 downto 2)) & \"00\";\n")


def psx_top_text(text, fault=None, dma_direct=False):
    """The hint port. CPU side: the CPU's stage-3 load registers (executeReadEnable,
    executeReadAddress), the clk1x before memorymux forwards the load (registering again, as
    K4 did, loses that lead); not the stage-4 outputs mem_isData/mem_rnw/mem_addressData,
    which are combinational decodes of them (scratchpad compare; F4 sign-off -0.27 ns into
    hint_st). A main-RAM range check excludes scratchpad and cache control. DMA side registered."""
    if TAG in text:
        raise ValueError('psx_top already carries ' + TAG)
    text = once(text, '   signal dmaRequest             : std_logic;\n',
                '   signal dmaRequest             : std_logic;\n'
                f'   signal dma_hint_ena           : std_logic; -- {TAG}\n'
                '   signal dma_hint_adr           : std_logic_vector(22 downto 0);\n'
                "   signal dma_side_q             : std_logic := '0';\n"
                "   signal dma_hint_q             : std_logic := '0';\n"
                "   signal dma_hint_adr_q         : std_logic_vector(22 downto 0) := (others => '0');\n"
                "   signal cpu_hint_ena           : std_logic;\n"
                "   signal cpu_hint_adr           : unsigned(31 downto 0);\n")
    text = once(text, '      mem_addressData   => mem_addressData,  \n',
                '      mem_addressData   => mem_addressData,  \n'
                f'      hint_readEna      => cpu_hint_ena, -- {TAG}\n'
                '      hint_readAddr     => cpu_hint_adr,\n')
    text = once(text, '      dmaRequest           => dmaRequest,\n      dmaStallCPU',
                '      dmaRequest           => dmaRequest,\n'
                '      ramHint_ena          => dma_hint_ena,\n'
                '      ramHint_adr          => dma_hint_adr,\n'
                '      dmaStallCPU')
    old_process = ("   process (clk1x)\n   begin\n      if rising_edge(clk1x) then\n" + PSX_TOP_HINT +
                   "      end if;\n   end process;\n")
    new_process = (f"   -- {TAG}: DMA side registered (it knows its next address early); CPU side combinational\n"
                   "   process (clk1x)\n   begin\n      if rising_edge(clk1x) then\n"
                   "         dma_side_q     <= dmaOn or dmaRequest;\n"
                   "         dma_hint_q     <= dma_hint_ena and (dmaOn or dmaRequest);\n"
                   "         dma_hint_adr_q <= dma_hint_adr;\n"
                   "      end if;\n   end process;\n"
                   "   ram_hint_req <= dma_hint_q when (dma_side_q = '1') else\n"
                   "                   '1' when (dmaOn = '0' and dmaRequest = '0' and cpu_hint_ena = '1' and cpu_hint_adr(28 downto 0) < 16#800000#) else '0';\n"
                   "   ram_hint_adr <= \"00\" & (dma_hint_adr_q(22 downto 21) and (save_mode & save_mode)) & dma_hint_adr_q(20 downto 2) & \"00\" when (dma_side_q = '1') else\n"
                   "                   \"00\" & (std_logic_vector(cpu_hint_adr(22 downto 21)) and (save_mode & save_mode)) & std_logic_vector(cpu_hint_adr(20 downto 2)) & \"00\";\n")
    if dma_direct:  # DMA side straight from dma.vhd (rig: the registered DMA hint loses 3 BL LEN 0/3 and 4 BL LEN 1/2)
        new_process = new_process.replace("ram_hint_req <= dma_hint_q when (dma_side_q = '1') else", "ram_hint_req <= dma_hint_ena when (dmaOn = '1' or dmaRequest = '1') else")
        new_process = new_process.replace('ram_hint_adr <= "00" & (dma_hint_adr_q(22 downto 21) and (save_mode & save_mode)) & dma_hint_adr_q(20 downto 2) & "00" when (dma_side_q = \'1\') else',
                                          'ram_hint_adr <= "00" & (dma_hint_adr(22 downto 21) and (save_mode & save_mode)) & dma_hint_adr(20 downto 2) & "00" when (dmaOn = \'1\' or dmaRequest = \'1\') else')
        assert 'dma_side_q' not in new_process.split('end process;')[1], new_process
    return once(text, old_process, new_process)


def cpu_text(text, fault=None):
    """Two outputs wired from existing stage-3 registers: no logic."""
    if TAG in text:
        raise ValueError('cpu already carries ' + TAG)
    text = once(text, '      mem_addressData       : out unsigned(31 downto 0); \n',
                '      mem_addressData       : out unsigned(31 downto 0); \n'
                f'      hint_readEna          : out std_logic; -- {TAG}: the stage-3 instruction loads\n'
                '      hint_readAddr         : out unsigned(31 downto 0);\n')
    text = once(text, '   mem_isData        <= mem4_request;\n',
                '   mem_isData        <= mem4_request;\n'
                f'   hint_readEna      <= executeReadEnable;  -- {TAG}\n'
                '   hint_readAddr     <= executeReadAddress;\n')
    return text


def dma_text(text, fault=None):
    if TAG in text:
        raise ValueError('dma already carries ' + TAG)
    text = once(text, '      ram_ena              : out std_logic := \'0\';\n',
                '      ram_ena              : out std_logic := \'0\';\n'
                f'      ramHint_ena          : out std_logic; -- {TAG}: next main-RAM read address, a hint only\n'
                '      ramHint_adr          : out std_logic_vector(22 downto 0);\n')
    text = once(text, '   trig_MADR <= madrMem_trig_q;\n',
                '   trig_MADR <= madrMem_trig_q;\n'
                f'   -- {TAG}: a request waiting in OFF starts at the trigger channel\'s MADR; a working\n'
                '   -- channel that reads RAM continues at ram_Adr (advanced at each request)\n'
                "   ramHint_ena <= '1' when ((dmaState = OFF and dmaRequest = '1') or (dmaState /= OFF and toDevice = '1')) else '0';\n"
                "   ramHint_adr <= std_logic_vector(trig_MADR(22 downto 2)) & \"00\" when (dmaState = OFF) else ram_Adr;\n")
    return text


def dma_armed_text(text, fault=None):
    """dma.vhd for the armed DMA hint: the last MADR value written on the bus and an 'armed' flag (a
    CHCR write with start set that reads RAM), cleared when the channel runs; all registers."""
    text = dma_text(text, fault)
    text = once(text, '      ramHint_adr          : out std_logic_vector(22 downto 0);\n',
                '      ramHint_adr          : out std_logic_vector(22 downto 0);\n'
                '      ramHint_armed        : out std_logic;\n'
                '      ramHint_madr         : out std_logic_vector(22 downto 0);\n')
    old_hint = ("   ramHint_ena <= '1' when ((dmaState = OFF and dmaRequest = '1') or (dmaState /= OFF and toDevice = '1')) else '0';\n"
                "   ramHint_adr <= std_logic_vector(trig_MADR(22 downto 2)) & \"00\" when (dmaState = OFF) else ram_Adr;\n")
    new_hint = (f"   -- {TAG} (armed form): on the bus side, the start address from registers; while the DMA holds\n"
                "   -- the bus, OFF (between blocks or before the first) hints the armed start or the advanced\n"
                "   -- ram_Adr, a working channel that reads RAM hints ram_Adr\n"
                "   ramHint_ena   <= '1' when (dmaState = OFF or toDevice = '1') else '0';\n"
                "   ramHint_adr   <= hint_madr when (dmaState = OFF and hint_armed = '1' and hint_run = '0') else ram_Adr;\n"
                "   ramHint_armed <= hint_armed;\n"
                "   ramHint_madr  <= ram_Adr when (hint_run = '1') else hint_madr;\n"
                "   process (clk1x)\n   begin\n      if rising_edge(clk1x) then\n"
                "         if (bus_write = '1' and bus_addr(6 downto 4) /= \"111\" and bus_addr(3 downto 2) = \"00\") then\n"
                "            hint_madr <= bus_dataWrite(22 downto 2) & \"00\";\n         end if;\n"
                "         if (bus_write = '1' and bus_addr(6 downto 4) /= \"111\" and bus_addr(3 downto 2) = \"10\" and bus_dataWrite(24) = '1' and bus_dataWrite(0) = '1') then\n"
                "            hint_armed <= '1'; hint_run <= '0'; hint_idle <= 0;\n"
                "         elsif (hint_armed = '1') then\n"
                "            -- the transfer runs in blocks with CPU time between them: stay armed until the DMA\n"
                "            -- has been idle for 64 clk1x\n"
                "            if (dmaState /= OFF or dmaRequest = '1' or dmaOn = '1') then hint_run <= '1'; hint_idle <= 0;\n"
                "            elsif (hint_idle = 63) then hint_armed <= '0';\n"
                "            else hint_idle <= hint_idle + 1; end if;\n"
                "         end if;\n"
                "      end if;\n   end process;\n")
    text = once(text, old_hint, new_hint)
    text = once(text, '   signal trig_MADR    : unsigned(23 downto 0);\n',
                '   signal trig_MADR    : unsigned(23 downto 0);\n'
                f"   signal hint_madr    : std_logic_vector(22 downto 0) := (others => '0'); -- {TAG}\n"
                "   signal hint_armed   : std_logic := '0';\n"
                "   signal hint_run     : std_logic := '0';\n"
                "   signal hint_idle    : integer range 0 to 63 := 0;\n")
    return text


def psx_top_armed_text(text, fault=None):
    text = psx_top_text(text, fault)
    text = once(text, '   signal dma_hint_adr           : std_logic_vector(22 downto 0);\n',
                '   signal dma_hint_adr           : std_logic_vector(22 downto 0);\n'
                '   signal dma_hint_armed         : std_logic;\n'
                '   signal dma_hint_madr          : std_logic_vector(22 downto 0);\n')
    text = once(text, '      ramHint_adr          => dma_hint_adr,\n',
                '      ramHint_adr          => dma_hint_adr,\n'
                '      ramHint_armed        => dma_hint_armed,\n'
                '      ramHint_madr         => dma_hint_madr,\n')
    i = text.index("   -- " + TAG + ": DMA side registered")
    j = text.index('                   "00" & (std_logic_vector(cpu_hint_adr(22 downto 21))', i)
    j = text.index('\n', j) + 1
    new = (f"   -- {TAG} (armed form): DMA side from dma.vhd's registers; otherwise the CPU's pending load,\n"
           "   -- else an armed DMA start address\n"
           "   ram_hint_req <= dma_hint_ena when (dmaOn = '1' or dmaRequest = '1') else\n"
           "                   '1' when (cpu_hint_ena = '1' and cpu_hint_adr(28 downto 0) < 16#800000#) else\n"
           "                   dma_hint_armed;\n"
           "   ram_hint_adr <= \"00\" & (dma_hint_adr(22 downto 21) and (save_mode & save_mode)) & dma_hint_adr(20 downto 2) & \"00\" when (dmaOn = '1' or dmaRequest = '1') else\n"
           "                   \"00\" & (std_logic_vector(cpu_hint_adr(22 downto 21)) and (save_mode & save_mode)) & std_logic_vector(cpu_hint_adr(20 downto 2)) & \"00\" when (cpu_hint_ena = '1' and cpu_hint_adr(28 downto 0) < 16#800000#) else\n"
           "                   \"00\" & (dma_hint_madr(22 downto 21) and (save_mode & save_mode)) & dma_hint_madr(20 downto 2) & \"00\";\n")
    return text[:i] + new + text[j:]


def controller_text(text, fault=None, early_refresh=True, miss_return=True, eqgate=False):
    # lean: no hint survival (the stage-4 hint never meets another request's edge) and the hint
    # reaches the pins through the idle address mux (pin-equivalent on the golden bench)
    text = rig.controller_text(text, fault if fault in ('no-ras', 'act-no-open') else 'leannmeq' if (eqgate and not miss_return) else 'lean' if miss_return else 'leannm')
    if not early_refresh:  # room: K's early refresh off (its logic folds away)
        text = once(text, '    wire early_quiet_c = OPEN_ROW != 0 && // sdram-early-refresh\n',
                    f"    wire early_quiet_c = 1'b0 && OPEN_ROW != 0 && // sdram-early-refresh ({TAG}: off)\n")
    return text


def transforms(fault=None, early_refresh=True, miss_return=True, dma_direct=False, dma_armed=False):
    return {'memorymux.vhd': lambda t: memorymux_text(t, fault),
            'psx_top.vhd': (lambda t: psx_top_armed_text(t, fault)) if dma_armed else (lambda t: psx_top_text(t, fault, dma_direct)),
            'dma.vhd': (lambda t: dma_armed_text(t, fault)) if dma_armed else (lambda t: dma_text(t, fault)),
            'cpu.vhd': lambda t: cpu_text(t, fault),
            'psx_sdram_ctrl.sv': lambda t: controller_text(t, fault, early_refresh, miss_return, dma_armed)}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('source', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--seed', type=int)
    p.add_argument('--fault')
    p.add_argument('--no-early-refresh', action='store_true', help='drop K\'s early refresh (room)')
    p.add_argument('--dma-hint-armed', action='store_true', help='DMA hint from registers: armed start MADR, ram_Adr (rig rel7b)')
    p.add_argument('--dma-hint-direct', action='store_true', help='DMA side of the hint unregistered (rig 161)')
    p.add_argument('--no-miss-return', action='store_true', help='drop K\'s row-conflict fast return (its clk3x ready crossing)')
    a = p.parse_args()
    import prepare_save_checksum as cs
    seed, changed = cs.prepare(a.source, a.output, a.seed, transforms(a.fault, not a.no_early_refresh, not a.no_miss_return, a.dma_hint_direct, a.dma_hint_armed))
    print(f'{TAG}: snapshot {a.output.resolve()} (seed {seed})')
    for path, (old, new) in changed.items():
        print(f'{TAG}: patched {path} (sha256 {old[:16]} -> {new[:16]})')
