"""Optional GPU divider operands held by their original producer registers."""
from pathlib import Path
import re
import json
import shutil
import subprocess

if __package__:
    from .prepare_resource_targets import UPSTREAM, PIN, digest
else:
    from prepare_resource_targets import UPSTREAM, PIN, digest


def borrowed_text(name,text):
    suffix='_out' if 'div1_out' in text else ''
    if name=='gpu_poly.vhd':
        start=text.index('            if (state /= CALCCOLOR2 and state /= CALCTEXTURE2) then')
        end=text.index("            if (state /= IDLE) then",start) if 'drawTiming' in text else text.index("            error <= '0';",start)
        block=text[start:end]
        if block.count('.dividend')!=6 or block.count('.divisor')!=6:raise ValueError('Polygon defaults changed')
        text=text[:start]+'''            if state=IDLE then
'''+''.join(f"               div{i}{suffix}.dividend <= (others=>'0');\n               div{i}{suffix}.divisor <= (others=>'0');\n" for i in range(1,7))+'''            end if;

'''+text[end:]
    elif name=='gpu_line.vhd':
        start=text.index("            div1"+suffix+".dividend        <= (others => '0');")
        end=text.index('            case (procstate) is',start)
        block=text[start:end]
        # Keep non-operand defaults/bookkeeping in the original position.
        changed,count=re.subn(r"^\s*div[1-6](?:_out)?\.(?:dividend|divisor)\s*<= \(others => '0'\);",'',block,flags=re.M)
        if count!=12:raise ValueError('Line defaults changed')
        clear='''            if procstate=PROCIDLE then
'''+''.join(f"               div{i}{suffix}.dividend <= (others=>'0');\n               div{i}{suffix}.divisor <= (others=>'0');\n" for i in range(1,7))+'''            end if;
'''
        # The processing state enum is named below; fail if pin differs.
        if 'case (procstate) is' not in text:raise ValueError('Line processing state changed')
        text=text[:start]+clear+changed+text[end:]
    elif name=='psx_gpu_divider_pool.vhd':
        marker='begin\n   divider_core'
        # Simulation-only lease records check the borrowed-input contract.
        declaration='   signal core_denominator, core_remainder : signed(24 downto 0) := (others=>\'0\');'
        if text.count(declaration)!=1:raise ValueError('Divider declaration changed')
        text=text.replace(declaration,declaration+'''
   -- synthesis translate_off
   signal leased_nums : signed(269 downto 0) := (others=>'0');
   signal leased_dens : signed(149 downto 0) := (others=>'0');
   -- synthesis translate_on''')
        marker='begin\n   divider_core'
        assert text.count(marker)==1
        text=text.replace(marker,'''begin
   borrowed : for i in 0 to 5 generate
         numerator(i) <= dividends(i*45+44 downto i*45);
         denominator(i) <= divisors(i*25+24 downto i*25);
   end generate;
   divider_core''')
        marker='''                     numerator(i)<=dividends(i*45+44 downto i*45);
                     denominator(i)<=divisors(i*25+24 downto i*25);'''
        assert text.count(marker)==1
        text=text.replace(marker,'')
        marker='            case state is'
        if text.count(marker)!=1:raise ValueError('Divider state decoder changed')
        text=text.replace(marker,'''            -- synthesis translate_off
            if state=RELEASE_START and starts="000000" then
               leased_nums<=dividends; leased_dens<=divisors;
            elsif state/=IDLE and state/=RELEASE_START then
               for i in 0 to 5 loop
                  if batch(i)='1' then
                     assert dividends(i*45+44 downto i*45)=leased_nums(i*45+44 downto i*45)
                        and divisors(i*25+24 downto i*25)=leased_dens(i*25+24 downto i*25)
                        report "Borrowed GPU operands changed before completion" severity failure;
                  end if;
               end loop;
            end if;
            -- synthesis translate_on
'''+marker)
    elif name=='gpu.vhd':
        marker='   signal pool_starts, pool_dones : std_logic_vector(5 downto 0);'
        assert text.count(marker)==1
        text=text.replace(marker,marker+"\n   signal pool_owner_poly : std_logic := '0';")
        marker='   divider_pool : entity work.psx_gpu_divider_pool\n'
        assert text.count(marker)==1
        text=text.replace(marker,'''   process(clk2x)
   begin
      if rising_edge(clk2x) then
         for i in 0 to 5 loop
            if line_div(i).start='1' then pool_owner_poly<='0'; end if;
            if poly_div(i).start='1' then pool_owner_poly<='1'; end if;
         end loop;
      end if;
   end process;
   divider_pool : entity work.psx_gpu_divider_pool
''')
        for kind in ('dividend','divisor'):
            pattern=rf'      div_array\(i\)\.{kind}\s*<= line_div\(i\)\.{kind}\s*or poly_div\(i\)\.{kind};'
            text,count=re.subn(pattern,f"      div_array(i).{kind} <= poly_div(i).{kind} when pool_owner_poly='1' else line_div(i).{kind};",text)
            assert count==1
    elif name=='gpu_rect.vhd':pass  # unchanged; lets variant tests hook the rectangle engine
    else:raise ValueError(name)
    return text


def apply_gpu_borrowed(manifest):
    pin=subprocess.check_output(['git','-C',str(UPSTREAM),'rev-parse','HEAD'],text=True).strip()
    if pin!=PIN or subprocess.run(['git','-C',str(UPSTREAM),'diff','--quiet','HEAD']).returncode:
        raise ValueError('GPU reference differs from its recorded pin')
    if set(manifest['targets']) not in ({'gpu'},{'psx_top'}):
        raise ValueError('Borrowed GPU operands require GPU or system alone')
    for t in manifest['targets'].values():
        if not t.get('gpu_divider_pool') or t.get('gpu_borrowed'):
            raise ValueError('Borrowed GPU operands require one existing divider pool')
        copies=[]
        q=Path(t['directory'])/(t['top']+'.qsf');qt=q.read_text()
        for name in ('gpu_poly.vhd','gpu_line.vhd','psx_gpu_divider_pool.vhd','gpu.vhd'):
            i=next(i for i,s in enumerate(t['quartus_sources']) if Path(s['path']).name==name)
            old=t['quartus_sources'][i];d=Path(t['directory'])/'borrowed-inputs';d.mkdir(exist_ok=True)
            p=d/name;p.write_text(borrowed_text(name,Path(old['path']).read_text()))
            copy={**old,'path':str(p),'sha256':digest(p),'selected':old['path'],'selected_sha256':old['sha256'],
                'transform':'Hold producer operands through divider completion; select current owner; omit duplicate snapshots',
                'state_machine_latency_changed':False,'game_verified':False,'hardware_verified':False}
            t['quartus_sources'][i]=copy;copies.append(copy)
            assert qt.count(old['path'])==1;qt=qt.replace(old['path'],str(p))
        q.write_text(qt);t['project_sha256']['.qsf']=digest(q)
        shutil.copy2(UPSTREAM/'LICENSE',d/'COPYING.GPL2')
        info={'sources':copies,'required_contract':'Selected producer holds every active-lane operand until batch completion',
              'cycle_equivalence_verified':False,'game_verified':False,'hardware_verified':False}
        t['gpu_borrowed']=info;manifest['gpu_borrowed']=info
        (Path(t['directory']).parent/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
