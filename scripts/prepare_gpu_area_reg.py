"""Register gpu_poly's drawing-area test of xPos (gpu-area-reg).

PROCPIXELS writes a pixel to the pipeline only when drawingAreaLeft <= xPos
<= drawingAreaRight. Those two 12-bit compares gate the enables of about 60
pipeline output registers and were the worst gpu_poly setup path in full
fits. xPos changes in exactly three places (line start at 0 or at the edge,
and +1 per pixel); this variant evaluates the same compares on the value
being written and keeps the two results in registers beside xPos.

The drawing area is constant while a primitive runs: gpu.vhd decodes the
E3/E4 commands only with proc_idle set, and gpu_poly only assigns xPos while
busy. So the registered results equal the original compares whenever
PROCPIXELS reads them. No cycle changes.
"""
import json
from pathlib import Path

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest


def _replace(text, old, new, count=1):
    if text.count(old) != count: raise ValueError('Pinned gpu_poly source changed near: ' + old.strip()[:60])
    return text.replace(old, new)


def area_reg_text(text):
    text = _replace(text, "   signal xPos                : signed(11 downto 0) := (others => '0'); \n",
                    "   signal xPos                : signed(11 downto 0) := (others => '0'); \n"
                    "   -- xPos >= drawingAreaLeft / xPos <= drawingAreaRight (gpu-area-reg)\n"
                    "   signal xPosInLeft          : std_logic := '1';\n"
                    "   signal xPosInRight         : std_logic := '1';\n")
    text = _replace(text, '''                     xPos  <= (others => '0');
''', '''                     xPos  <= (others => '0');
                     if (drawingAreaLeft = 0) then xPosInLeft <= '1'; else xPosInLeft <= '0'; end if;
                     xPosInRight <= '1';
''')
    text = _replace(text, '''                     xPos  <= xStart(43 downto 32);
''', '''                     xPos  <= xStart(43 downto 32);
                     if (xStart(43 downto 32) >= to_integer(drawingAreaLeft)) then xPosInLeft <= '1'; else xPosInLeft <= '0'; end if;
                     if (xStart(43 downto 32) <= to_integer(drawingAreaRight)) then xPosInRight <= '1'; else xPosInRight <= '0'; end if;
''')
    text = _replace(text, '''                     xPos  <= xPos + 1;
''', '''                     xPos  <= xPos + 1;
                     if (xPos + 1 >= to_integer(drawingAreaLeft)) then xPosInLeft <= '1'; else xPosInLeft <= '0'; end if;
                     if (xPos + 1 <= to_integer(drawingAreaRight)) then xPosInRight <= '1'; else xPosInRight <= '0'; end if;
''')
    return _replace(text,
        "                     if (xPos >= to_integer(drawingAreaLeft) and xPos <= to_integer(drawingAreaRight)) then\n",
        "                     if (xPosInLeft = '1' and xPosInRight = '1') then\n")


def apply_gpu_area_reg(manifest):
    target = manifest['targets']['psx_top']
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'gpu_poly.vhd')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'gpu-area-reg'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'gpu_poly.vhd'; path.write_text(area_reg_text(previous.read_text()))
    target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
        original=str(previous), original_sha256=digest(previous), transform='Registered drawing-area compares of xPos')
    qsf = directory / (target['top'] + '.qsf'); q = qsf.read_text()
    if q.count(str(previous)) != 1: raise ValueError('Expected one gpu_poly assignment')
    qsf.write_text(q.replace(str(previous), str(path))); target['project_sha256']['.qsf'] = digest(qsf)
    target['gpu_area_reg'] = True; manifest['gpu_area_reg'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
