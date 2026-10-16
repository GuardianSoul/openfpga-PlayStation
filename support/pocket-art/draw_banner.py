#!/usr/bin/env python3
"""Draw the PlayStation platform image (521x165) for the Pocket.

An isometric grey console in the style of other openFPGA platform images, with
an emblem of our own on the lid (no Sony logo). Writes a PNG; package_release.py
converts support/pocket-art/playstation-banner.png with pocket_image.py.

  python3 support/pocket-art/draw_banner.py support/pocket-art/playstation-banner.png
"""
import math, sys
from PIL import Image, ImageDraw
A=0.5; E=0.6
# model: X = depth (0 back .. XD front), Y = width (0 right end .. YW left end), Z up
def P(x,y,z): return (400+x*A - y*A, 150+(x+y)*A/2 - z*E)
XD,YW,H=188,270,44
img=Image.new('L',(800,400),255); d=ImageDraw.Draw(img)
def poly(pts,c): d.polygon([P(*p) for p in pts],fill=c)
def line(a,b,c): d.line([P(*a),P(*b)],fill=c)
def circ(cx,cy,z,r,n=120): return [(cx+r*math.cos(2*math.pi*i/n),cy+r*math.sin(2*math.pi*i/n),z) for i in range(n)]
def band(cx,cy,r,z0,z1,c,n=120):
    for i in range(n):
        t0=2*math.pi*i/n; t1=2*math.pi*(i+1)/n; tm=(t0+t1)/2
        if math.cos(tm)+math.sin(tm) < 0: continue
        a=(cx+r*math.cos(t0),cy+r*math.sin(t0)); b=(cx+r*math.cos(t1),cy+r*math.sin(t1))
        poly([(a[0],a[1],z0),(b[0],b[1],z0),(b[0],b[1],z1),(a[0],a[1],z1)],c)
def ring(cx,cy,z,r,c):
    p=[P(*q) for q in circ(cx,cy,z,r)]; d.line(p+[p[0]],fill=c)
def disc_mask(cx,cy,z,r):
    """Screen pixels whose centre lies on the disc of radius r at height z."""
    pts=[P(*q) for q in circ(cx,cy,z,r,32)]
    x0=int(min(q[0] for q in pts))-1; x1=int(max(q[0] for q in pts))+2
    y0=int(min(q[1] for q in pts))-1; y1=int(max(q[1] for q in pts))+2
    m=set()
    for sy in range(y0,y1):
        for sx in range(x0,x1):
            p=(sx+0.5-400)/A; q=(sy+0.5-150+E*z)*2/A
            x=(p+q)/2; y=(q-p)/2
            if (x-cx)**2+(y-cy)**2<=r*r: m.add((sx,sy))
    return m
def edge(m): return {(x,y) for x,y in m if any(n not in m for n in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)))}
def put(pixels,c):
    for q in pixels: img.putpixel(q,c)
def round_part(cx,cy,r,z0,z1,top,rim,inner=None,sc=None):
    """Raised round part: side band, top face, 1-pixel rim, optional inner ring."""
    side=set()
    for k in range(9): side|=disc_mask(cx,cy,z0+(z1-z0)*k/8,r)
    topm=disc_mask(cx,cy,z1,r)
    sc=sc or (SIDE,SOFT)                  # side band and its outline
    put(side-topm,sc[0]); put(edge(side)-topm,sc[1])
    put(topm,top); put(edge(topm),rim)
    if inner: put(edge(disc_mask(cx,cy,z1,inner[0])),inner[1])
# palette after the other openFPGA platform images: few greys, light from the
# left, so the left end is lighter than the front; no dark outlines
WHITE,TOP,TOP2,FRONT,SIDE,EDGE,DARK=230,206,190,152,174,112,52
SOFT=142                                  # soft outline for the lid and buttons
YR,YL=62,190          # center strip between right block (Y<YR) and left block (Y>YL)
R=4                   # center strip recess
# faces
YR,YL=62,190
R=4
I=10
poly([(XD-I,YR,0),(XD-I,YL,0),(XD-I,YL,H-R),(XD-I,YR,H-R)],FRONT-12)   # inset centre front
poly([(XD-I,YR,0),(XD,YR,0),(XD,YR,H),(XD-I,YR,H)],SIDE)     # right block wall
poly([(XD,0,0),(XD,YR,0),(XD,YR,H),(XD,0,H)],FRONT)
poly([(XD,YL,0),(XD,YW,0),(XD,YW,H),(XD,YL,H)],FRONT)
poly([(0,YW,0),(XD,YW,0),(XD,YW,H),(0,YW,H)],SIDE)           # left end (vents)
# vents on left end
NV=19; V0=(XD-(NV*9-5))/2                 # vents centred along the left end
for x in [V0+9*k for k in range(NV)]:
    poly([(x,YW,5),(x+4,YW,5),(x+4,YW,H-5),(x,YW,H-5)],EDGE)
# top: blocks and recessed strip
poly([(0,0,H),(XD,0,H),(XD,YR,H),(0,YR,H)],TOP)
poly([(0,YR,H-R),(XD-I,YR,H-R),(XD-I,YL,H-R),(0,YL,H-R)],TOP2)
poly([(0,YR,H-R),(0,YL,H-R),(0,YL,H),(0,YR,H)],TOP2)   # back lip: keeps the back silhouette straight
# strip front lip drop and the step faces of the blocks into the strip
poly([(0,YR,H-R),(XD-I,YR,H-R),(XD-I,YR,H),(0,YR,H)],SIDE-8)
# left block top last: it stands in front of the recessed strip, so its edge
# into the strip is one straight line
poly([(0,YL,H),(XD,YL,H),(XD,YW,H),(0,YW,H)],TOP)
# lid
cx,cy,r=96,(YR+YL)/2,80   # lid, "PS" and port pairs share the strip centre line
round_part(cx,cy,r,H-R,H+2,TOP+10,TOP+10,sc=(160,160))   # lid: no outlines, side well below the strip
# lid print, our own marks and not Sony's: a small flat diamond with a short
# unreadable caption bar under it toward the front, and a wordmark bar at the back
Z=H+2
def flat(x0,x1,y0,y1,c): poly([(x0,y0,Z),(x1,y0,Z),(x1,y1,Z),(x0,y1,Z)],c)
DX,DY,DR=cx+20,cy,13                  # mark centre and half-size: centred across the lid, toward its front
# our own mark, drawn per pixel: a bold G in the mark grey,
# no block behind it
GLYPH=["XXXXXXXXXXXXX.",
       "XXXXXXXXXXXXX.",
       "XXX...........",
       "XXX...XXXXXXXX",
       "XXX...XXXXXXXX",
       "XXX........XXX",
       "XXXXXXXXXXXXXX",
       "XXXXXXXXXXXXXX"]
mx,my=P(DX,DY,Z); mx=round(mx)-7; my=round(my)-4
for r,row in enumerate(GLYPH):
    for c,bit in enumerate(row):
        if bit=='X': img.putpixel((mx+c,my+r),SIDE)
GR=13                                 # mark half-size in model units, for the caption below
flat(DX+GR+7,DX+GR+11,DY+14,DY-14,SIDE+12)      # caption bar, clear of the mark
flat(cx-60,cx-57,cy+12,cy-12,SIDE)             # wordmark bar
# buttons
def button(x,y,r,top,rim):
    """Button flush with the body: a flat disc with a thin rim and a soft
    shade all around just inside it."""
    face=disc_mask(x,y,H,r); put(face,top)
    ring=edge(face); b1=edge(face-ring); b2=edge(face-ring-b1)
    put(b1,top-12)                                    # soft one-pixel shade all around the inner edge
    put(ring,rim)
button(160,230,22,TOP-10,TOP-34)   # power
button(114,230,10,TOP-10,TOP-34)    # reset
button(160,31,22,TOP-10,TOP-34)     # open
# ports on front, right of center: memory card slot above controller port
def fpoly(pts,c): poly([(XD-I,y,z) for y,z in pts],c)
def rrect(y0,y1,z0,z1,c,cut=2):
    fpoly([(y0+cut,z0),(y1-cut,z0),(y1,z0+cut),(y1,z1-cut),(y1-cut,z1),(y0+cut,z1),(y0,z1-cut),(y0,z0+cut)],c)
def fline(a,b,c): line((XD-I,a[0],a[1]),(XD-I,b[0],b[1]),c)
def inset(y0,y1,z0,z1):
    # bevelled opening: dark top and left edges, light bottom and right edges
    fpoly([(y0,z0),(y1,z0),(y1,z1),(y0,z1)],FRONT-4)
    fline((y1,z1),(y0,z1),EDGE); fline((y1,z1),(y1,z0),EDGE)
    fline((y0,z0),(y1,z0),WHITE); fline((y0,z0),(y0,z1),WHITE)
PC=(YR+YL)/2-8                                        # optical centre of the ports, nudged right
# 3x6 pixel digits hand-sheared to the front face's slope (rows rise one
# pixel toward the right), anchored on the slot centre
DIGITS={'1':[".X.","XX.",".X.",".X.",".X.","..."],
        '2':["...","XX.","..X",".X.","X..","XX."]}
def digit(ch,yc,ztop,c):
    sx,sy=P(XD-I,yc,ztop); sx=round(sx-1.5); sy=round(sy)+1
    for r,row in enumerate(DIGITS[ch]):
        for k,bit in enumerate(row):
            if bit=='X': img.putpixel((sx+k,sy+r),c)
for label,yc in (('2',PC-28),('1',PC+28)):            # port 1 on the left, as on the console
    inset(yc-17,yc+17,3,13)                           # controller port, near the bottom
    fpoly([(yc-13,6),(yc+13,6),(yc+13,10),(yc-13,10)],EDGE)
    for k in range(-10,11,5): fline((yc+k,6),(yc+k,10),FRONT-4)
    inset(yc-17,yc+17,16,23)                          # memory card slot
    fline((yc-13,19.5),(yc+13,19.5),DARK)
    digit(label,yc,37,EDGE-16)
# silhouette edges
line((XD,0,H),(XD,YR,H),WHITE); line((XD,YL,H),(XD,YW,H),WHITE); line((XD-I,YR,H-R),(XD-I,YL,H-R),TOP)
# power LED straddling the top-front edge of the left block, below Power
# drawn per pixel as a square in the front face's plane: each column starts on
# the edge pixel and runs down, so it shears with the edge (rows rise to the right)
sx,sy=P(XD,230,H); sx=round(sx); sy=round(sy)
tops=[]
for x in range(sx-2,sx+2):
    top=next(y for y in range(sy-4,sy+4) if img.getpixel((x,y))==WHITE)
    for y in range(top,top+4): img.putpixel((x,y),DARK)
    tops.append((x,top))
for x,top in tops:                  # one-pixel sliver on the top face, sheared back
    for k in (0,1): img.putpixel((x-k,top-1),DARK)
bb=Image.eval(img,lambda v:255-v).getbbox()        # console alone sets the placement
out=Image.new('L',(521,165),255); out.paste(img,(131-bb[0],160-bb[3])); out.save(sys.argv[1]); print(bb)
