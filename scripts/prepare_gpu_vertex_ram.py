"""Keep received polygon vertices in block RAM; load the working set serially.

The four received vertices (position, colour, texture coordinate) move from
flip-flops into three small synchronous RAMs written exactly where the original
record fields were written. The flat colour of the command word, which the
original broadcast to every vertex, is kept in one register and stands in for
vertex 0 and for every vertex of an unshaded polygon. ISSUE then reads the
three vertices of the current triangle over three extra cycles instead of one.
Pixels, memory traffic order and every register the rasteriser uses are
unchanged; each triangle starts three gpu clocks later. Opt-in (approved
tested, reversible latency changes for gpu_poly on 2026-10-13).
"""
import json
from pathlib import Path
import re

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError(f'gpu_poly source changed ({text.count(old)} matches): ' + old.strip()[:70])
    return text.replace(old, new)


def vertex_ram_text(text):
    text = _replace(text, '      ISSUE,\n', '      ISSUE,\n      ISSUE_READ1,\n      ISSUE_READ2,\n      ISSUE_READ3,\n')
    decl = '''   -- Received vertices in block RAM (synchronous read).
   type tPosRam is array(0 to 3) of std_logic_vector(23 downto 0);
   type tColRam is array(0 to 3) of std_logic_vector(23 downto 0);
   type tTexRam is array(0 to 3) of std_logic_vector(15 downto 0);
   signal posRam : tPosRam := (others => (others => '0'));
   signal colRam : tColRam := (others => (others => '0'));
   signal texRam : tTexRam := (others => (others => '0'));
   attribute ramstyle : string;
   attribute ramstyle of posRam : signal is "M10K";
   attribute ramstyle of colRam : signal is "M10K";
   attribute ramstyle of texRam : signal is "M10K";
   signal ramRa              : integer range 0 to 3 := 0;
   signal ramRaIndex         : integer range 0 to 3 := 0;
   signal posQ, colQ         : std_logic_vector(23 downto 0);
   signal texQ               : std_logic_vector(15 downto 0);
   signal flatColor          : std_logic_vector(23 downto 0) := (others => '0');
   signal readVertex         : tVertex;
   signal loadSecond         : std_logic := '0';
'''
    text = _replace(text, '      variable xrec12   : signed(11 downto 0);\n', '      variable xrec12   : signed(11 downto 0);\n      variable posX     : std_logic_vector(11 downto 0);\n      variable posY     : std_logic_vector(11 downto 0);\n')
    marker = '   signal vt                  : tworkVertexArray;\n'
    text = _replace(text, marker, marker + decl)
    # Writes: same states and conditions as the original record assignments.
    text = _replace(text, '''                     for i in 0 to 3 loop
                        rec_vertices(i).r <= unsigned(fifo_data( 7 downto  0));
                        rec_vertices(i).g <= unsigned(fifo_data(15 downto  8));
                        rec_vertices(i).b <= unsigned(fifo_data(23 downto 16));
                     end loop;
''', '''                     flatColor <= fifo_data(23 downto 0);
''')
    text = _replace(text, '''                     rec_vertices(rec_index).r <= unsigned(fifo_data( 7 downto  0));
                     rec_vertices(rec_index).g <= unsigned(fifo_data(15 downto  8));
                     rec_vertices(rec_index).b <= unsigned(fifo_data(23 downto 16));
''', '''                     colRam(rec_index) <= fifo_data(23 downto 0);
''')
    pos = re.search(r'''                     if \(drawingOffsetX < 0\) then
(.*?)                     end if;
                     
                     if \(drawingOffsetY < 0\) then
(.*?)                     end if;
''', text, re.S)
    if not pos: raise ValueError('gpu_poly position receive changed')
    def field(block, name, var):
        lines = []
        for line in block.splitlines(keepends=True):
            if f'rec_vertices(rec_index).{name} <= to_integer(' in line:
                line = line.replace(f'rec_vertices(rec_index).{name} <= to_integer(', f'{var} := std_logic_vector(to_signed(to_integer(').replace(');\n', '), 12));\n')
            lines.append(line)
        return ''.join(lines)
    xblock = field(pos.group(1), 'x', 'posX')
    yblock = field(pos.group(2), 'y', 'posY')
    if 'rec_vertices' in xblock + yblock: raise ValueError('gpu_poly position receive changed')
    new = ('                     if (drawingOffsetX < 0) then\n' + xblock + '                     end if;\n                     \n'
           '                     if (drawingOffsetY < 0) then\n' + yblock + '                     end if;\n'
           '                     posRam(rec_index) <= posY & posX;\n')
    text = text[:pos.start()] + new + text[pos.end():]
    text = _replace(text, '''                     rec_vertices(rec_index).u <= unsigned(fifo_data( 7 downto  0));
                     rec_vertices(rec_index).v <= unsigned(fifo_data(15 downto  8));
''', '''                     texRam(rec_index) <= fifo_data(15 downto 0);
''')
    # ISSUE: same decisions, then read the three vertices one per cycle.
    issue = re.search(r'''                  if \(rec_quad = '0' or quadFirst = '1'\) then
                     vt\(0\) <= rec_vertices\(0\);
                     vt\(1\) <= rec_vertices\(1\);
                     vt\(2\) <= rec_vertices\(2\);
                     quadFirst <= '0';
                     if \(rec_quad = '1'\) then
                        nextQuad <= '1';
                     end if;
                  else
                     vt\(0\) <= rec_vertices\(2\);
                     vt\(1\) <= rec_vertices\(1\);
                     vt\(2\) <= rec_vertices\(3\);
                  end if;
''', text)
    if not issue: raise ValueError('gpu_poly ISSUE changed')
    text = text[:issue.start()] + '''                  state <= ISSUE_READ1;
                  if (rec_quad = '0' or quadFirst = '1') then
                     loadSecond <= '0';
                     quadFirst <= '0';
                     if (rec_quad = '1') then
                        nextQuad <= '1';
                     end if;
                  else
                     loadSecond <= '1';
                  end if;
''' + text[issue.end():]
    text = _replace(text, '''               when ISSUE =>
                  state    <= SORTVERTICES;
''', '''               when ISSUE_READ1 =>
                  vt(0) <= readVertex;
                  state <= ISSUE_READ2;

               when ISSUE_READ2 =>
                  vt(1) <= readVertex;
                  state <= ISSUE_READ3;

               when ISSUE_READ3 =>
                  vt(2) <= readVertex;
                  state <= SORTVERTICES;

               when ISSUE =>
''')
    # Clear write strobes each enabled cycle (they are set in the same branch).
    text = _replace(text, '''            done                 <= '0';
            CmdDone              <= '0';
''', '''            done                 <= '0';
            CmdDone              <= '0';
''')
    if 'rec_vertices(' in text.split('architecture', 1)[1].split('begin', 1)[1]:
        raise ValueError('An original vertex register access remains')
    # RAM processes and read-address sequencing.
    rams = '''
   -- Vertex RAMs are written by the state machine in the receive states.
   process (clk2x)
   begin
      if rising_edge(clk2x) then
         if (ce = '1') then
            posQ <= posRam(ramRa);
            colQ <= colRam(ramRa);
            texQ <= texRam(ramRa);
            ramRaIndex <= ramRa;
         end if;
      end if;
   end process;

   -- Vertex order: first triangle 0,1,2; second triangle of a quad 2,1,3.
   process (state, loadSecond, ramRaIndex, rec_quad, quadFirst)
   begin
      ramRa <= ramRaIndex;
      if (state = ISSUE) then
         if (rec_quad = '1' and quadFirst = '0') then ramRa <= 2; else ramRa <= 0; end if;
      elsif (state = ISSUE_READ1) then
         ramRa <= 1;
      elsif (state = ISSUE_READ2) then
         if (loadSecond = '1') then ramRa <= 3; else ramRa <= 2; end if;
      end if;
   end process;

   readVertex.x <= to_integer(signed(posQ(11 downto 0)));
   readVertex.y <= to_integer(signed(posQ(23 downto 12)));
   readVertex.r <= unsigned(flatColor( 7 downto  0)) when (rec_shading = '0' or ramRaIndex = 0) else unsigned(colQ( 7 downto  0));
   readVertex.g <= unsigned(flatColor(15 downto  8)) when (rec_shading = '0' or ramRaIndex = 0) else unsigned(colQ(15 downto  8));
   readVertex.b <= unsigned(flatColor(23 downto 16)) when (rec_shading = '0' or ramRaIndex = 0) else unsigned(colQ(23 downto 16));
   readVertex.u <= unsigned(texQ( 7 downto 0));
   readVertex.v <= unsigned(texQ(15 downto 8));
'''
    arch = text.index('architecture')
    pos = text.index('\nbegin', arch) + len('\nbegin')
    text = text[:pos] + '\n' + rams + text[pos:]
    return text


def apply_gpu_vertex_ram(manifest):
    target = manifest['targets']['psx_top']
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'gpu_poly.vhd')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'gpu-vertex-ram'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'gpu_poly.vhd'; path.write_text(vertex_ram_text(previous.read_text()))
    target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
        original=str(previous), original_sha256=digest(previous), transform='Received vertices in block RAM, serial triangle load')
    qsf = directory / (target['top'] + '.qsf'); q = qsf.read_text()
    if q.count(str(previous)) != 1: raise ValueError('Expected one gpu_poly assignment')
    qsf.write_text(q.replace(str(previous), str(path))); target['project_sha256']['.qsf'] = digest(qsf)
    target['gpu_vertex_ram'] = True; manifest['gpu_vertex_ram'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
