"""Install flame_vortex walk/melee/wake while protecting the two death sheets."""
from pathlib import Path
import sys
import hashlib
import numpy as np
from PIL import Image, ImageOps
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from normalize_anim import resize_rgba_canvas
SOURCE = Path(__file__).resolve().parent
DEST = ROOT.parent/'depth-hammer/assets/entity_sprites/default/monsters/flame_vortex'
protected = [p.name for p in DEST.glob('*.png') if '_melee_' not in p.name]
before = {n:hashlib.sha256((DEST/n).read_bytes()).hexdigest() for n in protected}

def gap(p,target,radius):
    lo,hi = max(1,int(target-radius)),min(len(p)-1,int(target+radius))
    candidates = np.flatnonzero(p[lo:hi]==0)+lo
    if not len(candidates):
        raise ValueError(f'No clear separator near {target}')
    return int(min(candidates,key=lambda x:abs(x-target)))

def extract(action,rows):
    im = Image.open(SOURCE/f'flame_vortex_{action}_reworked.png').convert('RGBA')
    alpha = np.array(im.getchannel('A'))
    alpha[alpha<=128] = 0
    im.putalpha(Image.fromarray(alpha))
    ys = [0]+[gap((alpha>0).sum(axis=1),im.height*i/rows,im.height/(rows*3)) for i in range(1,rows)]+[im.height]
    frames = []
    for top,bottom in zip(ys,ys[1:]):
        occupied = np.flatnonzero((alpha[top:bottom]>0).sum(axis=1))
        row_top = top + int(occupied.min())
        row_bottom = top + int(occupied.max()) + 1
        p = (alpha[top:bottom]>0).sum(axis=0)
        xs = [0]+[gap(p,im.width*i/4,im.width/12) for i in range(1,4)]+[im.width]
        for left,right in zip(xs,xs[1:]):
            cell = im.crop((left,row_top,right,row_bottom))
            assert cell.getbbox()
            bounds = cell.getbbox()
            frames.append(cell.crop((bounds[0],0,bounds[2],cell.height)))
    return frames

def normalize(frames,anchor_feet=False):
    anchors = []
    for f in frames:
        if anchor_feet:
            mask = np.array(f.getchannel('A'))[int(f.height*.85):]>128
            xs = np.where(mask)[1]
            anchors.append((int(xs.min())+int(xs.max())+1)/2)
        else:
            anchors.append(f.width/2)
    left = max(anchors)
    right = max(f.width-a for f,a in zip(frames,anchors))
    scale = min(32/(left+right),32/max(f.height for f in frames))
    origin = left*scale+(32-(left+right)*scale)/2
    out = Image.new('RGBA',(32*len(frames),32))
    heights = []
    for i,(f,a) in enumerate(zip(frames,anchors)):
        size = (max(1,round(f.width*scale)),max(1,round(f.height*scale)))
        resized = resize_rgba_canvas(f,size,pixel_art=True,settings={'preblur':.15,'colour_count':64})
        x = max(0,min(32-size[0],round(origin-a*scale)))
        out.alpha_composite(resized,(i*32+x,32-size[1]))
        heights.append(size[1])
    print('Normalized heights:',heights)
    return out

outputs = {}
for action in ('melee',):
    frames = extract(action,3)
    for i,direction in enumerate(('up','right','down')):
        outputs[f'flame_vortex_{action}_{direction}.png'] = normalize(frames[i*4:i*4+4],False)
    right = outputs[f'flame_vortex_{action}_right.png']
    left = Image.new('RGBA',right.size)
    for i in range(4):
        left.paste(ImageOps.mirror(right.crop((32*i,0,32*i+32,32))),(32*i,0))
    outputs[f'flame_vortex_{action}_left.png'] = left

for name,im in outputs.items():
    assert im.size == Image.open(DEST/name).size
    assert im.mode=='RGBA' and im.getchannel('A').getextrema()==(0,255)
    cells = [im.crop((x,0,x+32,32)) for x in range(0,im.width,32)]
    assert all(f.getbbox() for f in cells)
    assert len({f.tobytes() for f in cells})==len(cells)
for name,im in outputs.items():
    im.save(DEST/name)
    print('Installed',name,im.size)
assert all(hashlib.sha256((DEST/n).read_bytes()).hexdigest()==h for n,h in before.items())
print('Four melee sheets validated; all other PNGs unchanged.')

