"""Optional MiSTer polygon line-attribute multiplier sharing experiment."""
import json
from pathlib import Path
import shutil
import subprocess

if __package__:
    from .prepare_resource_targets import UPSTREAM,digest
else:
    from prepare_resource_targets import UPSTREAM,digest

PIN='cd17b5a0ad261005c321d9d5ba41b8dbe313a7d3'


def shared_line_source(directory):
    pin=subprocess.check_output(['git','-C',str(UPSTREAM),'rev-parse','HEAD'],text=True).strip()
    if pin!=PIN or subprocess.run(['git','-C',str(UPSTREAM),'diff','--quiet','HEAD']).returncode:
        raise ValueError('GPU reference differs from its recorded pin')
    original=UPSTREAM/'rtl/gpu_poly.vhd';text=original.read_text()
    marker='      PREPARELINE,\n'
    if text.count(marker)!=1:raise ValueError('Pinned GPU line state changed')
    text=text.replace(marker,marker+'      CALCLINE,\n      CALCLINEADD,\n')
    marker='   signal mulResult32clk      : unsigned(31 downto 0) := (others => \'0\');'
    if text.count(marker)!=1:raise ValueError('Pinned shared multiplier changed')
    text=text.replace(marker,marker+'''
   signal lineMulStep         : integer range 0 to 9 := 0;
   signal lineNextState       : tState := PROCPIXELS;
   signal lineGradient        : unsigned(31 downto 0);
   signal lineCoordinate      : unsigned(31 downto 0);''')
    marker='   mulIn1 <= dxU when (state = CALCTEXTURE4 and mulStep = 0) else'
    if text.count(marker)!=1:raise ValueError('Pinned multiplier coefficient selection changed')
    text=text.replace(marker,'''   lineGradient <= dxR when lineMulStep=0 else dyR when lineMulStep=1 else
                   dxG when lineMulStep=2 else dyG when lineMulStep=3 else
                   dxB when lineMulStep=4 else dyB when lineMulStep=5 else
                   dxU when lineMulStep=6 else dyU when lineMulStep=7 else
                   dxV when lineMulStep=8 else dyV;
   lineCoordinate <= unsigned(to_signed(yCoord,32)) when lineMulStep mod 2=1 else
                     (others=>'0') when xStart(43 downto 32)<0 else
                     unsigned(resize(xStart(43 downto 32),32));

   mulIn1 <= lineGradient when state=CALCLINE else
             dxU when (state = CALCTEXTURE4 and mulStep = 0) else''')
    marker='   mulIn2 <= unsigned(to_signed(-vt(coreVertex).x, 32)) when (state = CALCTEXTURE4 and mulStep = 0) else'
    if text.count(marker)!=1:raise ValueError('Pinned multiplier coordinate selection changed')
    text=text.replace(marker,'''   mulIn2 <= lineCoordinate when state=CALCLINE else
             unsigned(to_signed(-vt(coreVertex).x, 32)) when (state = CALCTEXTURE4 and mulStep = 0) else''')
    begin=text.index('-- check required to prevent simulation errors for multiplying negative value with unsigned')
    end=text.index("                  stop := '0';",begin)
    original_block=text[begin:end]
    if original_block.count('resize(')!=15:raise ValueError('Pinned scanline multiplication block changed')
    text=text[:begin]+'''                  work_R <= base_R; work_G <= base_G; work_B <= base_B;
                  work_U <= base_U; work_V <= base_V;

'''+text[end:]
    marker='                when REQUESTFIRST =>'
    if text.count(marker)!=1:raise ValueError('Pinned polygon line continuation changed')
    # Geometry/skip/stop logic is unchanged. Only a drawable line with actual
    # shading/texturing enters the shared unit; flat polygons retain latency.
    text=text.replace(marker,'''                  if skip='0' and stop='0' and (rec_shading='1' or rec_texture='1') then
                     if rec_transparency='1' or DrawPixelsMask='1' then
                        if firstPixel='1' then lineNextState <= REQUESTFIRST;
                        else lineNextState <= REQUESTLINE; end if;
                     else lineNextState <= PROCPIXELS; end if;
                     if rec_shading='1' then lineMulStep<=0; else lineMulStep<=6; end if;
                     state<=CALCLINE;
                  end if;

               when CALCLINE =>
                  mulResult32clk <= mulResult32;
                  state<=CALCLINEADD;

               when CALCLINEADD =>
                  case lineMulStep is
                     when 0 | 1 => work_R <= work_R + mulResult32clk;
                     when 2 | 3 => work_G <= work_G + mulResult32clk;
                     when 4 | 5 => work_B <= work_B + mulResult32clk;
                     when 6 | 7 => work_U <= work_U + mulResult32clk;
                     when 8 | 9 => work_V <= work_V + mulResult32clk;
                     when others => null;
                  end case;
                  if lineMulStep=9 or (lineMulStep=5 and rec_texture='0') then
                     state<=lineNextState;
                  else lineMulStep<=lineMulStep+1;state<=CALCLINE; end if;

                when REQUESTFIRST =>''')
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    path=directory/'gpu_poly.vhd';path.write_text('-- GPL-2.0 derived scanline multiplier sharing; experimental added setup cycles.\n'+text)
    shutil.copy2(UPSTREAM/'LICENSE',directory/'COPYING.GPL2')
    result={'path':str(path),'library':'work','sha256':digest(path),'original':str(original),
        'original_sha256':digest(original),'transform':'Reuse existing mul32u and result register for ten scanline color/UV products',
        'registered_line_products':True,
        'additional_2x_setup_ticks':{'flat':0,'shaded':12,'textured':8,'shaded_and_textured':20},
        'state_machine_latency_changed':True,'pixel_equivalence_verified':False,'fit_verified':False,
        'game_verified':False,'hardware_verified':False}
    (directory/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');return result


def apply_gpu_line_share(manifest):
    if set(manifest['targets']) not in ({'gpu'},{'psx_top'}):raise ValueError('GPU sharing requires GPU or system alone')
    name=next(iter(manifest['targets']));target=manifest['targets'][name]
    if 'gpu_line_share' in target:raise ValueError('GPU sharing already applied')
    directory=Path(target['directory']);adapted=shared_line_source(directory/'gpu-line-share')
    changes=0
    for i,source in enumerate(target['quartus_sources']):
        if source['path']==adapted['original']:target['quartus_sources'][i]=adapted;changes+=1
    if changes!=1:raise ValueError('Expected one original polygon engine')
    qsf=directory/(target['top']+'.qsf');text=qsf.read_text()
    if text.count(adapted['original'])!=1:raise ValueError('Expected one polygon source assignment')
    qsf.write_text(text.replace(adapted['original'],adapted['path']));target['project_sha256']['.qsf']=digest(qsf)
    target['gpu_line_share']=adapted;manifest['gpu_line_share']=adapted
    (directory.parent/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');return manifest
