"""Install goblin walk/melee/wake while protecting the two death sheets."""
from pathlib import Path
import sys
import hashlib
import numpy as np
from PIL import Image, ImageOps
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from normalize_anim import resize_rgba_canvas
SOURCE = Path(__file__).resolve().parent
DEST = ROOT.parent/'depth-hammer/assets/entity_sprites/default/monsters/goblin'
protected = [p.name for p in DEST.glob('*.png') if '_melee_' not in p.name]
before = {n:hashlib.sha256((DEST/n).read_bytes()).hexdigest() for n in protected}

def gap(p,target,radius):
    lo,hi = max(1,int(target-radius)),min(len(p)-1,int(target+radius))
    candidates = np.flatnonzero(p[lo:hi]==0)+lo
    if not len(candidates):
        raise ValueError(f'No clear separator near {target}')
    return int(min(candidates,key=lambda x:abs(x-target)))

def extract(action,rows):
    im = Image.open(SOURCE/f'goblin_{action}_reworked.png').convert('RGBA')
    alpha = np.array(im.getchannel('A'))
    alpha[alpha<=128] = 0
    im.putalpha(Image.fromarray(alpha))
    ys = [0]+[gap((alpha>0).sum(axis=1),im.height*i/rows,im.height/(rows*3)) for i in range(1,rows)]+[im.height]
    frames = []
    for top,bottom in zip(ys,ys[1:]):
        p = (alpha[top:bottom]>0).sum(axis=0)
        xs = [0]+[gap(p,im.width*i/4,im.width/12) for i in range(1,4)]+[im.width]
        for left,right in zip(xs,xs[1:]):
            cell = im.crop((left,top,right,bottom))
            assert cell.getbbox()
            frames.append(cell.crop(cell.getbbox()))
    return frames

def body(im):
    a=np.array(im).astype(float)
    m=(a[:,:,1]>a[:,:,0]*1.2)&(a[:,:,1]>a[:,:,2]*1.6)&(a[:,:,3]>200)
    counts=m.sum(axis=1)
    top=int(np.where(counts>=counts.max()*.4)[0].min())
    bottom=int(np.where(counts>=max(2,counts.max()*.15))[0].max())+1
    return top,bottom

frames=extract('melee',3)
from measure_walk import body_bounds
outputs={}
for row,direction in enumerate(('up','right','down')):
    fs=frames[row*4:row*4+4]
    if direction=='right':
        fs=[f.resize((round(f.width*.90),f.height),Image.Resampling.LANCZOS) for f in fs]
    walk=Image.open(DEST/f'goblin_walk_{direction}.png').convert('RGBA')
    target=float(np.median([body_bounds(walk.crop((j*32,0,j*32+32,32)))[1]-body_bounds(walk.crop((j*32,0,j*32+32,32)))[0] for j in range(4)]))
    bounds=[body(f) for f in fs]
    scale=target/float(np.median([b-t for t,b in bounds]))
    scale=min(scale,32/max(f.width for f in fs),32/max(f.height for f in fs))
    heights=[round((b-t)*scale) for t,b in bounds]
    assert all(abs(h-target)<=2 for h in heights),(direction,target,heights)
    baseline=min(32,32-max(round((f.height-b)*scale) for f,(t,b) in zip(fs,bounds)))
    out=Image.new('RGBA',(128,32))
    for j,(f,(t,b)) in enumerate(zip(fs,bounds)):
        size=(round(f.width*scale),round(f.height*scale))
        resized=resize_rgba_canvas(f,size,pixel_art=True,settings={'preblur':.15,'colour_count':64})
        y=max(0,min(32-size[1],baseline-round(b*scale)))
        assert y>=0 and y+size[1]<=32
        out.alpha_composite(resized,(j*32+(32-size[0])//2,y))
    outputs[f'goblin_melee_{direction}.png']=out
    print(direction,'walk body target',target,'attack body heights',heights)
right=outputs['goblin_melee_right.png']
left=Image.new('RGBA',right.size)
for j in range(4):
    left.paste(ImageOps.mirror(right.crop((j*32,0,j*32+32,32))),(j*32,0))
outputs['goblin_melee_left.png']=left
for name,im in outputs.items():
    assert im.size==Image.open(DEST/name).size==(128,32)
    assert im.getchannel('A').getextrema()==(0,255)
    assert len({im.crop((j*32,0,j*32+32,32)).tobytes() for j in range(4)})==4
for name,im in outputs.items():
    im.save(DEST/name)
    print('Installed',name)
assert all(hashlib.sha256((DEST/n).read_bytes()).hexdigest()==h for n,h in before.items())
print('All non-melee PNGs unchanged.')


