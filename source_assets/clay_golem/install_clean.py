"""Install remaining clay golem style retouches without changing melee."""
from pathlib import Path
import sys
import hashlib
import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from normalize_anim import resize_rgba_canvas
SOURCE = Path(__file__).resolve().parent
DEST = ROOT.parent / 'depth-hammer/assets/entity_sprites/default/monsters/clay_golem'
melee_before = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in DEST.glob('*melee*.png')}

def load(name):
    im = Image.open(SOURCE / f'clay_golem_{name}_clean.png').convert('RGBA')
    a = np.array(im.getchannel('A'))
    a[a <= 128] = 0
    im.putalpha(Image.fromarray(a))
    return im

def gap(p, target, radius):
    lo,hi = max(1,int(target-radius)),min(len(p)-1,int(target+radius))
    found = np.flatnonzero(p[lo:hi] == 0)+lo
    if not len(found):
        raise ValueError(f'No transparent separator at {target}')
    return int(min(found,key=lambda x:abs(x-target)))

def frames(im, rows):
    a = np.array(im.getchannel('A')) > 0
    ys = [0] + [gap(a.sum(axis=1), im.height*i/rows, im.height/(rows*3)) for i in range(1,rows)] + [im.height]
    result = []
    for top,bottom in zip(ys,ys[1:]):
        p = a[top:bottom].sum(axis=0)
        xs = [0]+[gap(p, im.width*i/4, im.width/12) for i in range(1,4)]+[im.width]
        for left,right in zip(xs,xs[1:]):
            cell = im.crop((left,top,right,bottom))
            assert cell.getbbox()
            result.append(cell.crop(cell.getbbox()))
    return result

def strip(items):
    scale = min(32/max(f.width for f in items),32/max(f.height for f in items))
    out = Image.new('RGBA',(32*len(items),32))
    heights = []
    for i,f in enumerate(items):
        size = (max(1,round(f.width*scale)),max(1,round(f.height*scale)))
        resized = resize_rgba_canvas(f,size,pixel_art=True,settings={'preblur':.15})
        out.alpha_composite(resized,(32*i+(32-size[0])//2,32-size[1]))
        heights.append(size[1])
    print('Frame heights:',heights)
    return out

outputs = {}
walk = frames(load('walk'),3)
for i,direction in enumerate(('up','right','down')):
    outputs[f'clay_golem_walk_{direction}.png'] = strip(walk[i*4:(i+1)*4])
right = outputs['clay_golem_walk_right.png']
left = Image.new('RGBA',right.size)
for i in range(4):
    left.paste(ImageOps.mirror(right.crop((i*32,0,(i+1)*32,32))),(i*32,0))
outputs['clay_golem_walk_left.png'] = left
for action in ('death','deathfire','wake'):
    outputs[f'clay_golem_{action}.png'] = strip(frames(load(action),2))
base = load('base')
base = base.crop(base.getbbox())
for name,cell in [('clay_golem.png',32),('clay_golem_large.png',256)]:
    scale = min(cell/base.width,cell/base.height)
    size = (max(1,round(base.width*scale)),max(1,round(base.height*scale)))
    canvas = Image.new('RGBA',(cell,cell))
    canvas.alpha_composite(resize_rgba_canvas(base,size,pixel_art=cell==32),((cell-size[0])//2,cell-size[1]))
    outputs[name] = canvas
for name,im in outputs.items():
    old = Image.open(DEST/name)
    assert im.size == old.size, (name,im.size,old.size)
    assert im.mode == 'RGBA' and im.getchannel('A').getextrema() == (0,255)
    if im.width > im.height:
        assert all(im.crop((x,0,x+32,32)).getbbox() for x in range(0,im.width,32))
for name,im in outputs.items():
    im.save(DEST/name)
    print('Installed',name,im.size)
assert all(hashlib.sha256((DEST/name).read_bytes()).hexdigest()==digest for name,digest in melee_before.items())
print('All nine replacements validated; melee sheets unchanged.')
