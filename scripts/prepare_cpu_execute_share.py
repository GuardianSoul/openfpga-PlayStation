#!/usr/bin/env python3
"""Source-bound, opt-in combinational execute sharing for the current CPU.

Keep the MiSTer decode, bypass, stalls, traps and all pipeline edges. Share
register add/subtract and signed/unsigned comparisons (including SLTI/U),
and reverse bits around one right shifter for SLL/V. Immediate ADD/IU and
memory-address arithmetic retain their original expressions by default.
The source-bound cache profile can also share those additions. No defaults.
"""
import hashlib
from pathlib import Path
CPU_SHA256='a4bd035801471eb0dff3dd9580821fbeae439e959ab4c97d413390cfff227af0'
CACHE_CPU_SHA256='e7fac3b0ebed127f8ff2f6b763419e6dbe92c84a54c94f1440470a57c95cdf65'
FUNCTIONS='''
   -- Combinational execute sharing; consumer edge and pipeline are unchanged.
   function pocket_addsub(a, b : unsigned(31 downto 0); subtract : std_logic)
      return unsigned is
      variable mask : unsigned(31 downto 0);
      variable carry : unsigned(32 downto 0) := (others => '0');
   begin
      mask := (others => subtract);
      carry(0) := subtract;
      return ('0' & a) + ('0' & (b xor mask)) + carry;
   end function;

   function pocket_shift(a : unsigned(31 downto 0); amount : unsigned(4 downto 0);
                         funct : unsigned(5 downto 0)) return unsigned is
      variable source : signed(32 downto 0);
      variable shifted, result : unsigned(31 downto 0);
   begin
      source := '0' & signed(a);
      if funct(1) = '0' then
         for bitnum in 0 to 31 loop source(bitnum) := a(31-bitnum); end loop;
      elsif funct(0) = '1' then
         source(32) := a(31);
      end if;
      shifted := resize(unsigned(shift_right(source, to_integer(amount))),32);
      result := shifted;
      if funct(1) = '0' then
         for bitnum in 0 to 31 loop result(bitnum) := shifted(31-bitnum); end loop;
      end if;
      return result;
   end function;
'''
def once(t,a,b):
 if t.count(a)!=1:raise ValueError('CPU execute anchor changed: '+repr(a[:90]))
 return t.replace(a,b,1)
def execute_share_text(text, address_share=False):
 source_sha=hashlib.sha256(text.encode()).hexdigest()
 if source_sha not in (CPU_SHA256,CACHE_CPU_SHA256):raise ValueError('CPU execute sharing requires exact current CPU')
 if address_share and source_sha!=CACHE_CPU_SHA256:
  raise ValueError('Address sharing requires the reviewed cache-profile CPU')
 text=once(text,'architecture arch of cpu is','architecture arch of cpu is\n'+FUNCTIONS)
 text=once(text,"      variable shiftValue   : signed(32 downto 0) := (others => '0');",'''      variable sharedRight : unsigned(31 downto 0);
      variable sharedSub : std_logic;
      variable sharedSum : unsigned(32 downto 0);
      variable sharedLess : std_logic;
      variable sharedShift : unsigned(31 downto 0);''')
 marker="      calcMemAddr             := value1 + unsigned(resize(signed(decodeImmData), 32));"
 text=once(text,marker,'''      sharedRight := value2;
      sharedSub := decodeFunct(1);
      if decodeOP /= 0 then
         sharedRight := unsigned(resize(signed(decodeImmData), 32));
         sharedSub := '0';
         if decodeOP = 16#0A# or decodeOP = 16#0B# then sharedSub := '1'; end if;
      end if;
      sharedSum := pocket_addsub(value1,sharedRight,sharedSub);
      sharedLess := not sharedSum(32);
      if value1(31) /= sharedRight(31) then sharedLess := value1(31); end if;
'''+marker)
 start="      -- multiplex high bit of rightshift so arithmetic shift can be reused for logical shift"
 end="      if (EXEBreakpoint = '1') then"
 a=text.index(start);b=text.index(end,a)
 text=text[:a]+"      sharedShift := pocket_shift(value2,executeShamt,decodeFunct);\n      \n"+text[b:]
 for old,new,count in [
 ('calcResult    := value1 + value2;', 'calcResult    := sharedSum(31 downto 0);',1),
 ('EXEresultData        <= value1 + value2;', 'EXEresultData        <= sharedSum(31 downto 0);',1),
 ('calcResult    := value1 - value2;', 'calcResult    := sharedSum(31 downto 0);',1),
 ('EXEresultData        <= value1 - value2;', 'EXEresultData        <= sharedSum(31 downto 0);',1),
 ('if (signed(value1) < signed(value2)) then', "if sharedLess = '1' then",1),
 ('if (value1 < value2) then', "if sharedSum(32) = '0' then",1),
 ('if (signed(value1) < resize(signed(decodeImmData), 32)) then', "if sharedLess = '1' then",1),
 ('if (value1 < unsigned(resize(signed(decodeImmData), 32))) then', "if sharedSum(32) = '0' then",1),
 ('value2 sll to_integer(executeShamt)', 'sharedShift',1),
 ('resize(unsigned(shift_right(shiftValue,to_integer(executeShamt))), 32)', 'sharedShift',1),
 ]:
  if text.count(old)!=count:raise ValueError('Missing arithmetic expression '+old)
  text=text.replace(old,new,count)
 if address_share:
  # All memory opcodes select the sign-extended immediate with subtract=0.
  # EXEMemAddr is consumed only by memory requests/address exceptions.
  # Non-memory idle address payloads may differ; accepted requests do not.
  for old,new in [
   ('calcMemAddr             := value1 + unsigned(resize(signed(decodeImmData), 32));','calcMemAddr             := sharedSum(31 downto 0);'),
   ('calcResult    := value1 + unsigned(resize(signed(decodeImmData), 32));','calcResult    := sharedSum(31 downto 0);'),
   ('EXEresultData        <= value1 + unsigned(resize(signed(decodeImmData), 32));','EXEresultData        <= sharedSum(31 downto 0);'),
  ]:text=once(text,old,new)
 return text


def prepare_project(directory, address_share=False):
    import json
    import shutil
    import subprocess
    from measure_pocket_seeds import inputs
    from prepare_dpram_sdp import primitive
    from prepare_pocket_config_cdc import assignments
    from prepare_resource_targets import UPSTREAM, digest
    from tool_paths import ROOT

    directory = Path(directory).resolve()
    if (not directory.is_relative_to(ROOT/'build') or
            subprocess.run(['git','check-ignore','--quiet',str(directory/'cpu-execute-share/manifest.json')],cwd=ROOT).returncode):
        raise ValueError('Fresh ignored build project required')
    if any((directory/name).exists() for name in ('db','incremental_db','output_files','cpu-execute-share')):
        raise ValueError('CPU execute sharing must precede map/fit')
    if not json.loads((directory/'manifest.json').read_text()).get('reset_only'):
        raise ValueError('Current CPU execute proof requires reset-only build')
    qsf = directory/'apf_top.qsf'
    qtext = qsf.read_text()
    selected = assignments(directory,qtext)
    def one(name):
        paths = {p for _,p in selected if p.name==name}
        if len(paths)!=1:
            raise ValueError('Expected one selected '+name)
        return paths.pop()
    cpu,ram = one('cpu.vhd'),one('pocket_dpram_sdp.vhd')
    if ram.read_text()!=primitive((UPSTREAM/'rtl/dpram.vhd').read_text()):
        raise ValueError('Current CPU proof requires reviewed SDP primitive')
    transformed = execute_share_text(cpu.read_text(), address_share=address_share)
    if qtext.count('"'+str(cpu)+'"')!=1:
        raise ValueError('Expected one quoted CPU assignment')
    before = inputs(directory)
    work = directory/'cpu-execute-share'
    work.mkdir()
    output = work/'cpu.vhd'
    output.write_text(transformed)
    shutil.copyfile(UPSTREAM/'LICENSE',work/'COPYING.GPL2')
    if any(digest(Path(row['path']))!=row['sha256'] for row in before):
        raise ValueError('CPU execute input changed during preparation')
    qsf.write_text(qtext.replace('"'+str(cpu)+'"','"'+str(output)+'"'))
    report = dict(original=dict(path=str(cpu),sha256=digest(cpu)),candidate=dict(path=str(output),sha256=digest(output)),
        original_inputs=before,preparer_sha256=digest(Path(__file__)),additional_guest_cycles=0,
        clocks_enables_constraints_unchanged=True,registers_RAM_interfaces_unchanged=True,
        address_adder_shared=address_share,
        scope='Combinational execute add/subtract/compare and barrel shifter only; no CPU replacement',
        fit_verified=False,timing_verified=False,hardware_verified=False,game_verified=False,production_selected=False)
    (work/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    import argparse,json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project',type=Path)
    parser.add_argument('--address-share',action='store_true',help='Exact cache-profile CPU only: also share ADDI/IU and the memory-address adder')
    args=parser.parse_args()
    print(json.dumps(prepare_project(args.project,address_share=args.address_share),indent=2))
