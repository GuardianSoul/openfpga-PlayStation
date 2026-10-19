#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""cd_top: every response FIFO byte through one byte mux.

The pinned cd_top writes each byte of the long responses (GetParam, GetLocL,
GetLocP, GetTN, GetTD, Test) and of the CDDA position reports with its own
`if (cmd_delay = k ...) then FifoResponse_Din <= source` statement: about 60
sequential assignments that synthesize into a long priority chain of 8-bit
2:1 muxes. Only one long response is active per clock (one nextCmd), and the
CDDA report bytes come after it in the process, so the same choice can be
made as: pick one source code and literal per clock, then one byte mux.
The GetID bytes and the acknowledge/error bytes use the same selection in
their original places, so program order keeps every priority.
Bytes, clocks, FIFO resets and the last-assignment-wins order against the
acknowledge/error writes before it are unchanged (run_cd_lockstep.py).
Usage: prepare_cd_response_mux.py PROJECT_OR_cd_top.vhd
"""
import sys
from pathlib import Path

START = '            -- long getparam response\n'
END_LONG = '            -- CDDA reporting\n'
CDDA_START = '            if (CDDA_outcnt > 0) then\n'
CDDA_END = "            if (newCD = '1' and newCD_1 = '0') then\n"


def region_string(name, text):
    """VHDL case arms: cmd_delay k (len..1) sends text[len-k]."""
    n = len(text)
    return ''.join(f'                           when {k} => resp_wr := \'1\'; resp_lit := x"{ord(text[n - k]):02X}";\n'
                   for k in range(n, 0, -1))


LONG = '''            -- long getparam response
            if (nextCmd = x"0F") then
               if (cmd_delay = 6 and FifoResponse_empty = '0') then
                  FifoResponse_reset <= '1';
               end if;
               case (cmd_delay) is
                  when 5 => resp_wr := '1'; resp_sel := 1;
                  when 4 => resp_wr := '1'; resp_sel := 2;
                  when 3 => resp_wr := '1';
                  when 2 => resp_wr := '1'; resp_sel := 3;
                  when 1 => resp_wr := '1'; resp_sel := 4;
                  when others => null;
               end case;
            end if;

            -- long GetLocL response
            if (nextCmd = x"10") then
               if (cmd_delay = 9 and FifoResponse_empty = '0') then
                  FifoResponse_reset <= '1';
               end if;
               if (lastSectorHeaderValid = '1') then
                  case (cmd_delay) is
                     when 8 => resp_wr := '1'; resp_sel := 5;
                     when 7 => resp_wr := '1'; resp_sel := 6;
                     when 6 => resp_wr := '1'; resp_sel := 7;
                     when 5 => resp_wr := '1'; resp_sel := 8;
                     when 4 => resp_wr := '1'; resp_sel := 9;
                     when 3 => resp_wr := '1'; resp_sel := 10;
                     when 2 => resp_wr := '1'; resp_sel := 11;
                     when 1 => resp_wr := '1'; resp_sel := 12;
                     when others => null;
                  end case;
               end if;
            end if;

            -- long GetLocP response
            if (nextCmd = x"11") then
               if (cmd_delay = 9 and FifoResponse_empty = '0') then
                  FifoResponse_reset <= '1';
               end if;
               if (hasCD = '1') then
                  case (cmd_delay) is
                     when 8 => resp_wr := '1'; resp_sel := 13; resp_sub := 1;
                     when 7 => resp_wr := '1'; resp_sel := 13; resp_sub := 2;
                     when 6 => resp_wr := '1'; resp_sel := 13; resp_sub := 3;
                     when 5 => resp_wr := '1'; resp_sel := 13; resp_sub := 4;
                     when 4 => resp_wr := '1'; resp_sel := 13; resp_sub := 5;
                     when 3 => resp_wr := '1'; resp_sel := 13; resp_sub := 7;
                     when 2 => resp_wr := '1'; resp_sel := 13; resp_sub := 8;
                     when 1 => resp_wr := '1'; resp_sel := 13; resp_sub := 9;
                     when others => null;
                  end case;
               end if;
            end if;

            -- long GetTN response
            if (nextCmd = x"13") then
               if (cmd_delay = 4 and FifoResponse_empty = '0') then
                  FifoResponse_reset <= '1';
               end if;
               if (hasCD = '1') then
                  case (cmd_delay) is
                     when 3 => resp_wr := '1'; resp_sel := 1;
                     when 2 => resp_wr := '1'; resp_lit := x"01"; -- todo:  first track number always 1?
                     when 1 => resp_wr := '1'; resp_sel := 14;
                     when others => null;
                  end case;
               end if;
            end if;

            -- long GetTD response
            if (nextCmd = x"14") then
               if (cmd_delay = 4 and FifoResponse_empty = '0') then
                  FifoResponse_reset <= '1';
               end if;
               if (hasCD = '1') then
                  if (FifoParam_Dout = x"00") then -- track 0 -> total size of CD
                     case (cmd_delay) is
                        when 3 => resp_wr := '1'; resp_sel := 1;
                        when 2 => resp_wr := '1'; resp_sel := 15;
                        when 1 => resp_wr := '1'; resp_sel := 16;
                        when others => null;
                     end case;
                  elsif (FifoParam_Dout <= trackcountBCD) then
                     case (cmd_delay) is
                        when 3 => resp_wr := '1'; resp_sel := 1;
                        when 2 => resp_wr := '1'; resp_sel := 17;
                        when 1 => resp_wr := '1'; resp_sel := 18;
                        when others => null;
                     end case;
                  end if;
               end if;
            end if;

            -- long test response
            if (nextCmd = x"19") then
               if (cmd_delay = 11 and FifoResponse_empty = '0') then
                  FifoResponse_reset <= '1';
               end if;
               case (FifoParam_Dout) is
                  when x"04" => -- Reset SCEx counters
                     if (cmd_delay = 1) then resp_wr := '1'; resp_sel := 1; startMotorCMD <= '1'; end if;
                  when x"05" => -- Read SCEx counters
                     case (cmd_delay) is
                        when 3 => resp_wr := '1'; resp_sel := 1;
                        when 2 => resp_wr := '1'; -- ?
                        when 1 => resp_wr := '1'; -- ?
                        when others => null;
                     end case;
                  when x"20" => -- Get CDROM BIOS Date/Version
                     case (cmd_delay) is
                        when 4 => resp_wr := '1'; resp_lit := x"95";
                        when 3 => resp_wr := '1'; resp_lit := x"05";
                        when 2 => resp_wr := '1'; resp_lit := x"16";
                        when 1 => resp_wr := '1'; resp_lit := x"C1";
                        when others => null;
                     end case;
                  when x"22" => -- region
                     if (region = "00") then -- ntsc-u
                        case (cmd_delay) is
''' + region_string('us', 'for U/C') + '''                           when others => null;
                        end case;
                     elsif (region = "01") then -- ntsc-j
                        case (cmd_delay) is
''' + region_string('jp', 'for Japan') + '''                           when others => null;
                        end case;
                     elsif (region = "10") then -- pal
                        case (cmd_delay) is
''' + region_string('eu', 'for Europe') + '''                           when others => null;
                        end case;
                     end if;
                  when others => null;
               end case;
            end if;

'''

CDDA = '''            if (CDDA_outcnt > 0) then
               CDDA_outcnt <= CDDA_outcnt - 1;
               resp_wr := '1';   -- every count 8..1 writes a byte
               case (CDDA_outcnt) is
                  when 8 => resp_sel := 1;
                  when 7 => resp_sel := 19; resp_sub := 1;
                  when 6 => resp_sel := 19; resp_sub := 2;
                  when 5 => resp_sel := 19; if (nextSubdata(9)(4) = '1') then resp_sub := 3; else resp_sub := 7; end if;
                  when 4 => if (nextSubdata(9)(4) = '1') then resp_sel := 20; else resp_sel := 19; resp_sub := 8; end if;
                  when 3 => resp_sel := 19; if (nextSubdata(9)(4) = '1') then resp_sub := 5; else resp_sub := 9; end if;
                  when 2 => if (nextSubdata(8)(0) = '1') then resp_sel := 21; else resp_sel := 23; end if;
                  when others => if (nextSubdata(8)(0) = '1') then resp_sel := 22; else resp_sel := 24; end if;
               end case;
            end if;

            if (resp_wr = '1') then
               FifoResponse_Wr <= '1';
               case (resp_sel) is
                  when 1      => FifoResponse_Din <= internalStatus;
                  when 2      => FifoResponse_Din <= modeReg;
                  when 3      => FifoResponse_Din <= XaFilterFile;
                  when 4      => FifoResponse_Din <= XaFilterChannel;
                  when 5      => FifoResponse_Din <= header( 7 downto  0);
                  when 6      => FifoResponse_Din <= header(15 downto  8);
                  when 7      => FifoResponse_Din <= header(23 downto 16);
                  when 8      => FifoResponse_Din <= header(31 downto 24);
                  when 9      => FifoResponse_Din <= subheader( 7 downto  0);
                  when 10     => FifoResponse_Din <= subheader(15 downto  8);
                  when 11     => FifoResponse_Din <= subheader(23 downto 16);
                  when 12     => FifoResponse_Din <= subheader(31 downto 24);
                  when 13     => FifoResponse_Din <= subdata(resp_sub);
                  when 14     => FifoResponse_Din <= trackcountBCD;
                  when 15     => FifoResponse_Din <= totalMinutesBCD;
                  when 16     => FifoResponse_Din <= totalSecondsBCD;
                  when 17     => FifoResponse_Din <= minutesBCD;
                  when 18     => FifoResponse_Din <= secondsBCD;
                  when 19     => FifoResponse_Din <= nextSubdata(resp_sub);
                  when 20     => FifoResponse_Din <= x"80" or nextSubdata(4);
                  when 21     => FifoResponse_Din <= std_logic_vector(peakvolumeR(7 downto 0));
                  when 22     => FifoResponse_Din <= '1' & std_logic_vector(peakvolumeR(14 downto 8));
                  when 23     => FifoResponse_Din <= std_logic_vector(peakvolumeL(7 downto 0));
                  when 24     => FifoResponse_Din <= '0' & std_logic_vector(peakvolumeL(14 downto 8));
                  when others => FifoResponse_Din <= resp_lit;
               end case;
            end if;

'''

VARIABLES = '''      variable resp_wr          : std_logic;
      variable resp_sel         : integer range 0 to 31;
      variable resp_sub         : integer range 0 to 11;
      variable resp_lit         : std_logic_vector(7 downto 0);
'''
PROCESS_HEAD = '''   -- command processing
   process(clk1x)
      variable paramCountNew    : integer range 0 to 6;
'''


INIT_ANCHOR = """            updatePhysicalPosition  <= '0';
         
            -- receive new command request or decrease wait timer on pending command
"""
INIT = """            updatePhysicalPosition  <= '0';

            -- prepare_cd_response_mux.py: every response byte written below picks
            -- a source (resp_sel/resp_lit/resp_sub) in program order, so later
            -- writes still win; one byte mux writes it after the CDDA report.
            resp_wr  := '0';
            resp_sel := 0;
            resp_lit := x"00";
            resp_sub := 0;
         
            -- receive new command request or decrease wait timer on pending command
"""

# GetID (workDelay 9..2) and the acknowledge/error bytes, in their places.
EDITS = [
    ("""                     if (workDelay = 9) then 
                        FifoResponse_Wr <= '1'; 
                        FifoResponse_Din <= internalStatus; 
                        if (hasCD = '0')     then FifoResponse_Din(3) <= '1'; end if; 
                        if (hasCD = '1')     then FifoResponse_Din(1) <= '1'; end if; -- from start motor
                        if (isAudioCD = '1') then FifoResponse_Din(3) <= '1'; end if; 
                     end if;
                     if (workDelay = 8) then
                        FifoResponse_Wr <= '1'; 
                        FifoResponse_Din <= x"00";
                        if (hasCD = '0')     then FifoResponse_Din(6) <= '1'; end if; 
                        if (isAudioCD = '1') then FifoResponse_Din(7) <= '1'; FifoResponse_Din(4) <= '1'; end if; 
                     end if;
                     if (workDelay = 7) then FifoResponse_Wr <= '1'; FifoResponse_Din <= x"20"; end if; -- ? 
                     if (workDelay = 6) then FifoResponse_Wr <= '1'; FifoResponse_Din <= x"00"; end if; -- ? 
                     if (workDelay = 5) then FifoResponse_Wr <= '1'; FifoResponse_Din <= std_logic_vector(to_unsigned(natural(character'pos('S')), 8)); end if;
                     if (workDelay = 4) then FifoResponse_Wr <= '1'; FifoResponse_Din <= std_logic_vector(to_unsigned(natural(character'pos('C')), 8)); end if; 
                     if (workDelay = 3) then FifoResponse_Wr <= '1'; FifoResponse_Din <= std_logic_vector(to_unsigned(natural(character'pos('E')), 8)); end if; 
                     if (region = "00") then -- ntsc-u
                        if (workDelay = 2) then FifoResponse_Wr <= '1'; FifoResponse_Din <= std_logic_vector(to_unsigned(natural(character'pos('A')), 8)); end if; 
                     elsif (region = "01") then -- ntsc-j
                        if (workDelay = 2) then FifoResponse_Wr <= '1'; FifoResponse_Din <= std_logic_vector(to_unsigned(natural(character'pos('I')), 8)); end if; 
                     elsif (region = "10") then -- pal
                        if (workDelay = 2) then FifoResponse_Wr <= '1'; FifoResponse_Din <= std_logic_vector(to_unsigned(natural(character'pos('E')), 8)); end if; 
                     end if;
""", """                     case (workDelay) is
                        when 9 => resp_wr := '1'; resp_sel := 25;   -- status | no disc/audio (3) | motor (1)
                        when 8 => resp_wr := '1'; resp_lit := (7 => isAudioCD, 6 => not hasCD, 4 => isAudioCD, others => '0');
                        when 7 => resp_wr := '1'; resp_lit := x"20"; -- ?
                        when 6 => resp_wr := '1'; -- ?
                        when 5 => resp_wr := '1'; resp_lit := x"53"; -- 'S'
                        when 4 => resp_wr := '1'; resp_lit := x"43"; -- 'C'
                        when 3 => resp_wr := '1'; resp_lit := x"45"; -- 'E'
                        when 2 =>
                           if (region = "00") then resp_wr := '1'; resp_lit := x"41"; -- 'A' ntsc-u
                           elsif (region = "01") then resp_wr := '1'; resp_lit := x"49"; -- 'I' ntsc-j
                           elsif (region = "10") then resp_wr := '1'; resp_lit := x"45"; -- 'E' pal
                           end if;
                        when others => null;
                     end case;
"""),
    ("""            if (cmdAck = '1' or ackRead_valid = '1') then
               FifoResponse_Din <= internalStatus;
               FifoResponse_Wr  <= '1';
            end if;
            
            if (driveAck = '1' or ackDrive = '1' or ackDriveEnd = '1') then
               if (CDROM_IRQFLAG = "00000") then
                  if (ackDriveEnd = '1') then
                     FifoResponse_Din <= internalStatus_1;
                  else
                     FifoResponse_Din <= internalStatus;
                  end if;
                  FifoResponse_Wr  <= '1';
               end if;
            end if;
                      
            if (errorResponseCmd_new = '1') then
               FifoResponse_Din           <= internalStatus or errorResponseCmd_error;
               FifoResponse_Wr            <= '1';
""", """            if (cmdAck = '1' or ackRead_valid = '1') then
               resp_wr := '1'; resp_sel := 1;
            end if;
            
            if (driveAck = '1' or ackDrive = '1' or ackDriveEnd = '1') then
               if (CDROM_IRQFLAG = "00000") then
                  resp_wr := '1';
                  if (ackDriveEnd = '1') then resp_sel := 26; else resp_sel := 1; end if;
               end if;
            end if;
                      
            if (errorResponseCmd_new = '1') then
               resp_wr := '1'; resp_sel := 27;
"""),
    ("""            if (errorResponseDrive_new = '1') then
               FifoResponse_Din        <= internalStatus or errorResponseDrive_error;
               FifoResponse_Wr         <= '1';
""", """            if (errorResponseDrive_new = '1') then
               resp_wr := '1'; resp_sel := 28;
"""),
    ("""            if (errorResponseNext_new = '1') then
               FifoResponse_Din        <= errorResponseNext_reason;
               FifoResponse_Wr         <= '1';
            end if; 
""", """            if (errorResponseNext_new = '1') then
               resp_wr := '1'; resp_sel := 29;
            end if; 
"""),
    ("""            if (ackPendingIRQNext = '1') then
               FifoResponse_Din  <= pendingDriveResponse;
               FifoResponse_Wr   <= '1';
            end if;
""", """            if (ackPendingIRQNext = '1') then
               resp_wr := '1'; resp_sel := 30;
            end if;
"""),
    ("""                  when 24     => FifoResponse_Din <= '0' & std_logic_vector(peakvolumeL(14 downto 8));
""", """                  when 24     => FifoResponse_Din <= '0' & std_logic_vector(peakvolumeL(14 downto 8));
                  when 25     => FifoResponse_Din <= internalStatus or ("0000" & (isAudioCD or not hasCD) & '0' & hasCD & '0');
                  when 26     => FifoResponse_Din <= internalStatus_1;
                  when 27     => FifoResponse_Din <= internalStatus or errorResponseCmd_error;
                  when 28     => FifoResponse_Din <= internalStatus or errorResponseDrive_error;
                  when 29     => FifoResponse_Din <= errorResponseNext_reason;
                  when 30     => FifoResponse_Din <= pendingDriveResponse;
"""),
]


def patch(text):
    for anchor in (START, END_LONG, CDDA_START, CDDA_END, PROCESS_HEAD):
        if anchor not in text:
            raise SystemExit(f'cd_top.vhd: anchor missing: {anchor.strip()}')
    if text.count(START) != 1 or text.count(END_LONG) != 1 or text.count(CDDA_START) != 1 or text.count(PROCESS_HEAD) != 1:
        raise SystemExit('cd_top.vhd: anchors not unique')
    a = text.index(START)
    b = text.index(END_LONG)
    text = text[:a] + LONG + text[b:]
    a = text.index(CDDA_START)
    b = text.index(CDDA_END, a)
    text = text[:a] + CDDA + text[b:]
    if text.count(INIT_ANCHOR) != 1:
        raise SystemExit('cd_top.vhd: ce-branch anchor not unique')
    text = text.replace(INIT_ANCHOR, INIT)
    for old, new in EDITS:
        if text.count(old) != 1:
            raise SystemExit(f'cd_top.vhd: response anchor not unique: {old.strip()[:60]}')
        text = text.replace(old, new)
    if 'FifoResponse_Din(' in text or text.count('FifoResponse_Din <=') != 31:
        raise SystemExit(f"cd_top.vhd: unexpected FifoResponse_Din writes left ({text.count('FifoResponse_Din <=')})")
    return text.replace(PROCESS_HEAD, PROCESS_HEAD + VARIABLES)


def target(path):
    path = Path(path)
    if path.is_file():
        return path
    hits = {line.split('"')[1] for line in (path / 'apf_top.qsf').read_text().splitlines() if '/cd_top.vhd"' in line}
    if len(hits) != 1:
        raise SystemExit(f'{path}: expected one assigned cd_top.vhd, found {sorted(hits)}')
    return Path(hits.pop())


if __name__ == '__main__':
    p = target(sys.argv[1])
    p.write_text(patch(p.read_text()))
    print(p)
