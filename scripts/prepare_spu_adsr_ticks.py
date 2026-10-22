"""spu.vhd: voice.adsrTicks through one tick decoder, and the ADSR step of
the current phase only (prepare --spu-adsr-ticks).

The SPU loads voice.adsrTicks in 15 places. Thirteen load a decoded tick
count adsr_ticks(X) = 1 sll (rate(X)(6 downto 2) - 11) (1 for a rate under
48), some shifted right by 1 or 2; the other two load the voice record or
count down. The per-phase decoders (adsr_ticks is five 24-bit decoders) and the
23-way selection between them become: each site records what it would load
(tk_mode 1: a rate and a right shift; tk_mode 2: a value), and one assignment
at the end of the clock applies the last site that ran, through a single
decoder. The last assignment of a clock wins, as before, so voice.adsrTicks
gets the same value on every clock. make test-spu-adsr-ticks compares every
SPU output on every clock against the SPU without it.
The step: adsr_step is read in one place, adsr_step(adsr_index) with the
current phase, so adsr_step_of(adsr_rate_q(i), adsr_dec_q(i)) (the same
expression the adsr_step process gives entry i) replaces it; the five-entry
array is then unused.
Usage: prepare_spu_adsr_ticks.py PROJECT_OR_spu.vhd (post-prepare step, after the
DualShock fit helper; patches the project's own spu.vhd copy)
"""
import re
import sys
from pathlib import Path

VARS = ('      variable adsr_index          : integer range 0 to 4;\n',
        '      variable adsr_index          : integer range 0 to 4;\n'
        '      variable tk_mode             : integer range 0 to 2;   -- spu-adsr-ticks: 1 decode, 2 value\n'
        '      variable tk_rate             : unsigned(6 downto 0);\n'
        '      variable tk_extra            : integer range 0 to 2;\n'
        '      variable tk_val              : unsigned(23 downto 0);\n')
START = ('      variable env_calcticks       : unsigned(23 downto 0);\n', None)   # locates the voice process
FINAL = """         -- spu-adsr-ticks: the last voice.adsrTicks load of this clock, one decoder
         if (tk_mode = 1) then
            if (tk_rate >= 48) then
               voice.adsrTicks <= shift_right(to_unsigned(1, 24) sll (to_integer(tk_rate(6 downto 2)) - 11), tk_extra);
            else
               voice.adsrTicks <= shift_right(to_unsigned(1, 24), tk_extra);
            end if;
         elsif (tk_mode = 2) then
            voice.adsrTicks <= tk_val;
         end if;
"""
STEP_FUNC = """
   -- spu-adsr-ticks: the ADSR step of one phase (as the adsr_step(i) process), so
   -- the voice computes only the current phase's step instead of five
   function adsr_step_of(rate : unsigned(6 downto 0); dec : std_logic) return signed is
      variable shift : integer range 0 to 11;
   begin
      shift := 0;
      if (rate(6 downto 2) <= 11) then
         shift := (11 - to_integer(rate(6 downto 2)));
      end if;
      if (rate < 48) then
         if (dec = '1') then
            return shift_left(to_signed(-8, 16) + to_integer(rate(1 downto 0)), shift);
         else
            return shift_left(to_signed( 7, 16) - to_integer(rate(1 downto 0)), shift);
         end if;
      else
         if (dec = '1') then
            return (to_signed(-8, 16) + to_integer(rate(1 downto 0)));
         else
            return (to_signed( 7, 16) - to_integer(rate(1 downto 0)));
         end if;
      end if;
   end function;
"""
STEP_USE = ('adsr_stepcalc := resize(adsr_step(adsr_index), 17);',
            'adsr_stepcalc := resize(adsr_step_of(adsr_rate_q(adsr_index), adsr_dec_q(adsr_index)), 17);')

SITES = [
    ('voice.adsrTicks      <= unsigned(RamVoiceRecord_dataB(0)(100 downto  77));',
     'tk_mode := 2; tk_val := unsigned(RamVoiceRecord_dataB(0)(100 downto  77));', 1),
    ('voice.adsrTicks <= "00" & adsr_ticks(to_integer(voice.adsrphase))(23 downto 2);',
     'tk_mode := 1; tk_rate := adsr_rate_q(to_integer(voice.adsrphase)); tk_extra := 2;', 1),
    ('voice.adsrTicks <= "0" & adsr_ticks(to_integer(voice.adsrphase))(23 downto 1);',
     'tk_mode := 1; tk_rate := adsr_rate_q(to_integer(voice.adsrphase)); tk_extra := 1;', 1),
    ('voice.adsrTicks <= adsr_ticks(to_integer(voice.adsrphase));',
     'tk_mode := 1; tk_rate := adsr_rate_q(to_integer(voice.adsrphase)); tk_extra := 0;', 1),
    ('voice.adsrTicks <= voice.adsrTicks - 1;',
     'tk_mode := 2; tk_val := voice.adsrTicks - 1;', 1),
]


def adsr_ticks_text(text):
    if text.count(START[0]) != 1: raise ValueError('spu-adsr-ticks: voice process not found')
    if text.count(VARS[0]) != 1: raise ValueError('spu-adsr-ticks: variable anchor not unique')
    text = text.replace(VARS[0], VARS[1])
    # the final assignment goes last inside the voice process's clocked branch
    begin = text.index(START[0]); tail = text.index('      end if;\n   end process;\n', begin)
    text = text[:tail] + FINAL + text[tail:]
    # the clock's first statement clears the record of the last load
    begin = text.index(START[0]); edge = text.index('      if (rising_edge(clk1x)) then\n', begin)
    text = text[:edge] + '      if (rising_edge(clk1x)) then\n         tk_mode := 0; tk_rate := (others => \'0\'); tk_extra := 0; tk_val := (others => \'0\');   -- no state kept\n' + text[edge + len('      if (rising_edge(clk1x)) then\n'):]
    for old, new, count in SITES:
        if text.count(old) != count: raise ValueError(f'spu-adsr-ticks: site count {text.count(old)} != {count}: {old}')
        text = text.replace(old, new)
    # constant-phase loads: adsr_ticks(ADSR_X)
    text, n = re.subn(r'voice\.adsrTicks\s*<=\s*adsr_ticks\((ADSR_[A-Z]+)\);',
                      r'tk_mode := 1; tk_rate := adsr_rate_q(\1); tk_extra := 0;', text)
    if n != 10: raise ValueError(f'spu-adsr-ticks: {n} constant-phase loads, expected 10')
    if re.search(r'voice\.adsrTicks\s*<=', text.split('-- spu-adsr-ticks: the last')[0]):
        raise ValueError('spu-adsr-ticks: an adsrTicks assignment was left in place')
    # the step: one phase's computation instead of the five-entry adsr_step array
    if text.count(STEP_USE[0]) != 1: raise ValueError('spu-adsr-ticks: adsr_step use changed')
    text = text.replace(STEP_USE[0], STEP_USE[1])
    if re.search(r'\badsr_step\(', text.replace('adsr_step(i)', '')): raise ValueError('spu-adsr-ticks: another adsr_step use')
    arch = text.index('architecture arch of spu is'); begin = text.index('\nbegin\n', arch)
    text = text[:begin] + STEP_FUNC + text[begin:]
    return text


def target(path):
    path = Path(path)
    if path.is_file():
        return path
    hits = {line.split('"')[1] for line in (path / 'apf_top.qsf').read_text().splitlines() if '/spu.vhd"' in line}
    if len(hits) != 1:
        raise SystemExit(f'{path}: expected one assigned spu.vhd, found {sorted(hits)}')
    found = Path(hits.pop())
    if not found.resolve().is_relative_to(path.resolve()):
        raise SystemExit(f'{found}: not a project copy; refusing to edit a shared source')
    return found


if __name__ == '__main__':
    p = target(sys.argv[1]); p.write_text(adsr_ticks_text(p.read_text())); print(p)
