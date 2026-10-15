"""Resample the SPU reverb input and output with the hardware 39-tap FIR.

Upstream MiSTer feeds the reverb from a 5-tap low-pass and holds its output
between updates. The hardware decimates the 44.1 kHz reverb input and
interpolates the 22.05 kHz output with a 39-tap FIR (psx-spx coefficients),
processing Left and Right on alternate 44.1 kHz ticks. This follows the
GPL Mednafen/Beetle PSX model of that path:

  down = clamp16((sum c[i] * in[p - 38 + 2i] + 0x4000 * in[p - 19]) >> 15)
  fresh channel: up = clamp16((sum c[i] * out[k - 19 + i]) >> 14)
  other channel: Left holds out[k - 9], Right out[k - 10]

where p is the newest input and k the newest output of that channel.

Histories live in one 256 x 16 block RAM. After each tick a one-multiplier
engine precomputes the sums for the next tick except their newest term
(coefficient -1), so the input/output paths keep their original cycles and
only subtract that term. The voice loop waits at its last voice only if the
engine has not finished, which needs about 45 of the 768 cycles per sample.
Histories are cleared after reset; they are not part of save states, so this
variant requires the reset-only build.
"""
import json
from pathlib import Path
import shutil

if __package__:
    from .prepare_resource_targets import UPSTREAM, digest
else:
    from prepare_resource_targets import UPSTREAM, digest

# Non-zero taps of the 39-tap FIR except the 0x4000 middle tap; c[19] = -1
# multiplies the newest sample and is applied when that sample arrives.
COEFFICIENTS = (-1, 2, -10, 35, -103, 266, -616, 1332, -2960, 10246,
                10246, -2960, 1332, -616, 266, -103, 35, -10, 2, -1)


def _replace(text, old, new, count=1):
    if text.count(old) != count: raise ValueError('Pinned SPU source changed near: ' + old.strip()[:60])
    return text.replace(old, new)


def declarations(text):
    coef = ', '.join(f'to_signed({c}, 16)' for c in COEFFICIENTS[:19])
    attribute = '' if 'attribute ramstyle : string;' in text else '   attribute ramstyle : string;\n'
    return f'''
   -- Exact reverb resampler (spu-reverb-resample).
   type trsRam is array(0 to 255) of signed(15 downto 0);
   signal rsRam               : trsRam := (others => (others => '0'));
{attribute}   attribute ramstyle of rsRam : signal is "M10K, no_rw_check";
   type trsCoef is array(0 to 18) of signed(15 downto 0);
   constant rsCoef            : trsCoef := ({coef});
   signal rsInWe              : std_logic := '0';
   signal rsStart             : std_logic := '0';
   signal rsInL               : signed(15 downto 0) := (others => '0');
   signal rsInR               : signed(15 downto 0) := (others => '0');
   signal rsPendR             : std_logic := '0';
   signal rsInPos             : unsigned(5 downto 0) := (others => '0');
   signal rsOutPosL           : unsigned(4 downto 0) := (others => '0');
   signal rsOutPosR           : unsigned(4 downto 0) := (others => '0');
   signal rsCh                : std_logic := '0';
   signal rsBusy              : std_logic := '0';
   signal rsSweep             : unsigned(8 downto 0) := (others => '1');
   signal rsStep              : integer range 0 to 40 := 40;
   signal rsRAddr             : unsigned(7 downto 0) := (others => '0');
   signal rsRData             : signed(15 downto 0) := (others => '0');
   signal rsCoef1, rsCoef2    : signed(15 downto 0) := (others => '0');
   signal rsDst1, rsDst2, rsDst3 : integer range 0 to 3 := 0;
   signal rsValid1, rsValid2, rsValid3 : std_logic := '0';
   signal rsProd              : signed(31 downto 0) := (others => '0');
   signal rsAccIn             : signed(31 downto 0) := (others => '0');
   signal rsAccOut            : signed(31 downto 0) := (others => '0');
   signal rsMid               : signed(15 downto 0) := (others => '0');
   signal rsDown              : signed(15 downto 0) := (others => '0');
   signal rsUpFresh           : signed(15 downto 0);
   signal rsUpL               : signed(15 downto 0);
   signal rsUpR               : signed(15 downto 0);
'''


ENGINE = '''
   -- Exact reverb resampler (spu-reverb-resample). Addresses: input Left
   -- 0-63, input Right 64-127, output Left 128-159, output Right 160-191.
   rsUpFresh <= clamp16(shift_right(rsAccOut - resize(reverbLastRight, 32), 14)) when (reverbRight = '1') else
                clamp16(shift_right(rsAccOut - resize(reverbLastLeft, 32), 14));
   rsUpL     <= rsMid when (reverbRight = '1') else rsUpFresh;
   rsUpR     <= rsUpFresh when (reverbRight = '1') else rsMid;

   process (clk1x)
      variable writeAddr : unsigned(7 downto 0);
      variable writeData : signed(15 downto 0);
      variable writeEna  : std_logic;
      variable tap       : integer range 0 to 18;
      variable newestIn  : signed(15 downto 0);
   begin
      if (rising_edge(clk1x)) then
         writeEna := '0'; writeAddr := (others => '0'); writeData := (others => '0');
         if (reset = '1') then
            rsSweep   <= (others => '0');
            rsInPos   <= (others => '0');
            rsOutPosL <= (others => '0');
            rsOutPosR <= (others => '0');
            rsPendR   <= '0';
            rsStep    <= 40;
            rsValid1  <= '0'; rsValid2 <= '0'; rsValid3 <= '0';
            rsAccIn   <= (others => '0');
            rsAccOut  <= (others => '0');
            rsMid     <= (others => '0');
            rsDown    <= (others => '0');
            rsBusy    <= '1';
         elsif (rsSweep(8) = '0') then
            writeEna := '1'; writeAddr := rsSweep(7 downto 0);
            rsSweep  <= rsSweep + 1;
            if (rsSweep = 255) then rsBusy <= '0'; end if;
         else
            -- Not gated by ce: the pulses from the main process must not be
            -- lost, and finishing during a pause changes nothing visible.
            if (rsInWe = '1') then
               if (reverbRight = '1') then newestIn := rsInR; else newestIn := rsInL; end if;
               rsDown    <= clamp16(shift_right(rsAccIn - resize(newestIn, 32), 15));
               writeEna  := '1'; writeAddr := "00" & rsInPos; writeData := rsInL;
               rsPendR   <= '1';
            elsif (rsPendR = '1') then
               writeEna  := '1'; writeAddr := "01" & rsInPos; writeData := rsInR;
               rsPendR   <= '0';
               rsInPos   <= rsInPos + 1;
            elsif (rsStart = '1') then
               -- reverbRight already names the next tick's channel; store the
               -- output just produced by the other channel first.
               writeEna  := '1'; writeData := (others => '0');
               if (reverbRight = '1') then
                  writeAddr := "100" & rsOutPosL; writeData := reverbLastLeft;  rsOutPosL <= rsOutPosL + 1;
               else
                  writeAddr := "101" & rsOutPosR; writeData := reverbLastRight; rsOutPosR <= rsOutPosR + 1;
               end if;
               rsCh     <= reverbRight;
               rsStep   <= 0;
               rsBusy   <= '1';
               rsAccIn  <= (others => '0');
               rsAccOut <= (others => '0');
            end if;

            -- Issue: one history read per step.
            rsValid1 <= '0';
            if (rsStep < 40) then
               rsStep   <= rsStep + 1;
               rsValid1 <= '1';
               if (rsStep < 19) then
                  tap := rsStep;
                  rsRAddr <= "0" & rsCh & (rsInPos - 38 + to_unsigned(2 * tap, 6));
                  rsCoef1 <= rsCoef(tap); rsDst1 <= 0;
               elsif (rsStep = 19) then
                  rsRAddr <= "0" & rsCh & (rsInPos - 19);
                  rsCoef1 <= to_signed(16384, 16); rsDst1 <= 0;
               elsif (rsStep < 39) then
                  tap := rsStep - 20;
                  if (rsCh = '1') then
                     rsRAddr <= "101" & (rsOutPosR - 19 + to_unsigned(tap, 5));
                  else
                     rsRAddr <= "100" & (rsOutPosL - 19 + to_unsigned(tap, 5));
                  end if;
                  rsCoef1 <= rsCoef(tap); rsDst1 <= 1;
               else
                  -- Middle tap of the channel that was fresh this tick.
                  if (rsCh = '1') then
                     rsRAddr <= "100" & (rsOutPosL - 10);
                  else
                     rsRAddr <= "101" & (rsOutPosR - 11);
                  end if;
                  rsCoef1 <= to_signed(1, 16); rsDst1 <= 2;
               end if;
            end if;

            -- Read, multiply, accumulate.
            rsValid2 <= rsValid1; rsCoef2 <= rsCoef1; rsDst2 <= rsDst1;
            rsValid3 <= rsValid2; rsDst3 <= rsDst2;
            rsProd   <= rsRData * rsCoef2;
            if (rsValid2 = '1' and rsDst2 = 2) then rsMid <= rsRData; end if;
            if (rsValid3 = '1') then
               if (rsDst3 = 0) then rsAccIn  <= rsAccIn  + rsProd; end if;
               if (rsDst3 = 1) then rsAccOut <= rsAccOut + rsProd; end if;
               if (rsDst3 = 2) then rsBusy   <= '0'; end if;
            end if;
         end if;

         if (writeEna = '1') then
            rsRam(to_integer(writeAddr)) <= writeData;
         end if;
         rsRData <= rsRam(to_integer(rsRAddr));
      end if;
   end process;

end architecture;'''


def reverb_resample_text(text):
    text = _replace(text, '\nbegin\n', declarations(text) + '\nbegin\n')
    text = _replace(text, '         ram_request             <= \'0\';\n',
                    '         ram_request             <= \'0\';\n         rsInWe                  <= \'0\';\n         rsStart                 <= \'0\';\n')
    marker = '                        reverbDownLeft  <= clamp16'
    text = _replace(text, marker, '                        rsInWe <= \'1\'; rsInL <= reverbInputLeft_v; rsInR <= reverbInputRight_v;\n' + marker)
    text = _replace(text, '                        reverbRight <= not reverbRight;\n',
                    '                        reverbRight <= not reverbRight;\n                        rsStart     <= \'1\';\n')
    text = _replace(text, "reverb_sample <= resize(reverbDownLeft, 24) when (reverbRight = '0') else resize(reverbDownRight, 24);",
                    'reverb_sample <= resize(rsDown, 24);')
    text = _replace(text, 'soundmul1 <= resize(reverbLastLeft, 24);', 'soundmul1 <= resize(rsUpL, 24);')
    text = _replace(text, 'soundmul1 <= resize(reverbLastRight, 24);', 'soundmul1 <= resize(rsUpR, 24);')
    text = _replace(text, "if (voiceCounter >= 23 or useSDRAM = '0') then",
                    "if ((voiceCounter >= 23 or useSDRAM = '0') and not (index = 23 and rsBusy = '1')) then")
    text = _replace(text, '\nend architecture;', ENGINE)
    return text


def reverb_resample_source(directory, original):
    original = Path(original)
    text = reverb_resample_text(original.read_text())
    directory = Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'spu.vhd'
    path.write_text('-- GPL-2.0 SPU specialization; exact 39-tap reverb resampler.\n' + text)
    shutil.copy2(UPSTREAM / 'LICENSE', directory / 'COPYING.GPL2')
    return {'path': str(path), 'library': 'work', 'sha256': digest(path),
            'original': str(original), 'original_sha256': digest(original),
            'transform': 'Hardware 39-tap reverb input decimation and output interpolation from a block-RAM history',
            'required_controls': {'savestate_transfers': 'off (reset-only)'},
            'game_verified': False, 'hardware_verified': False}


def apply_spu_reverb_resample(manifest):
    target = manifest['targets']['psx_top']
    if 'spu_reverb_resample' in target: raise ValueError('SPU reverb resampler already applied')
    if 'reset_only_specialization' not in target: raise ValueError('SPU reverb resampler requires the reset-only build')
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'spu.vhd')
    previous = target['quartus_sources'][index]['path']
    directory = Path(target['directory']); copy = reverb_resample_source(directory / 'spu-reverb-resample', previous)
    target['quartus_sources'][index] = copy
    qsf = directory / (target['top'] + '.qsf'); text = qsf.read_text()
    if text.count(previous) != 1: raise ValueError('Expected one SPU assignment')
    qsf.write_text(text.replace(previous, copy['path']))
    target['project_sha256']['.qsf'] = digest(qsf)
    target['spu_reverb_resample'] = copy; manifest['spu_reverb_resample'] = copy
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
