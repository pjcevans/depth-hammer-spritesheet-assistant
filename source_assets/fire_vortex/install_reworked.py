"""Install fire vortex movement and vertical strikes with fixed row anchoring."""
from pathlib import Path
import hashlib
import sys
import numpy as np
from PIL import Image, ImageOps
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from normalize_anim import resize_rgba_canvas
SOURCE = Path(__file__).resolve().parent
DEST = ROOT.parent/'depth-hammer/assets/entity_sprites/default/monsters/fire_vortex'
targets = {f'fire_vortex_walk_{d}.png' for d in ('up','down','left','right')} | {'fire_vortex_melee_up.png','fire_vortex_melee_down.png'}
if '--only-melee-up' in sys.argv:
    targets = {'fire_vortex_melee_up.png'}
protected = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in DEST.glob('*.png') if p.name not in targets}

def gap(p,target,radius):
    lo,hi=max(1,int(target-radius)),min(len(p)-1,int(target+radius))
    candidates=np.flatnonzero(p[lo:hi]==0)+lo
    if not len(candidates):
        raise ValueError(f'No transparent separator near {target}')
    return int(min(candidates,key=lambda x:abs(x-target)))

def rows_from(name,count):
    im=Image.open(SOURCE/f'fire_vortex_{name}_reworked.png').convert('RGBA')
    a=np.array(im.getchannel('A'))
    a[a<=128]=0
    im.putalpha(Image.fromarray(a))
    ys=[0]+[gap((a>0).sum(axis=1),im.height*i/count,im.height/(count*3)) for i in range(1,count)]+[im.height]
    rows=[]
    for top,bottom in zip(ys,ys[1:]):
        occupied=np.flatnonzero((a[top:bottom]>0).sum(axis=1))
        y0,y1=top+int(occupied.min()),top+int(occupied.max())+1
        p=(a[y0:y1]>0).sum(axis=0)
        xs=[0]+[gap(p,im.width*i/4,im.width/12) for i in range(1,4)]+[im.width]
        frames=[]
        for l,r in zip(xs,xs[1:]):
            cell=im.crop((l,y0,r,y1))
            box=cell.getbbox()
            assert box
            frames.append(cell.crop((box[0],0,box[2],cell.height)))
        rows.append(frames)
    return rows

def normalize(frames):
    scale=min(32/max(f.width for f in frames),32/frames[0].height)
    out=Image.new('RGBA',(128,32))
    for i,f in enumerate(frames):
        size=(max(1,round(f.width*scale)),max(1,round(f.height*scale)))
        sprite=resize_rgba_canvas(f,size,pixel_art=True,settings={'preblur':.15,'colour_count':64})
        out.alpha_composite(sprite,(i*32+(32-size[0])//2,32-size[1]))
    return out

outputs={}
for direction,frames in zip(('up','right','down'),rows_from('walk',3)):
    outputs[f'fire_vortex_walk_{direction}.png']=normalize(frames)
right=outputs['fire_vortex_walk_right.png']
left=Image.new('RGBA',right.size)
for i in range(4):
    left.paste(ImageOps.mirror(right.crop((32*i,0,32*i+32,32))),(32*i,0))
outputs['fire_vortex_walk_left.png']=left
for direction,frames in zip(('up','down'),rows_from('melee_vertical',2)):
    if direction == 'up' and (SOURCE/'fire_vortex_melee_up_reworked.png').exists():
        frames = rows_from('melee_up',1)[0]
    outputs[f'fire_vortex_melee_{direction}.png']=normalize(frames)
outputs = {name:im for name,im in outputs.items() if name in targets}
assert set(outputs)==targets
for name,im in outputs.items():
    assert im.size==Image.open(DEST/name).size==(128,32)
    assert im.mode=='RGBA' and im.getchannel('A').getextrema()==(0,255)
    frames=[im.crop((32*i,0,32*i+32,32)) for i in range(4)]
    assert all(f.getbbox() for f in frames) and len({f.tobytes() for f in frames})==4
for name,im in outputs.items():
    im.save(DEST/name)
    print('Installed',name)
assert all(hashlib.sha256((DEST/n).read_bytes()).hexdigest()==h for n,h in protected.items())
print(f'{len(outputs)} sheets validated; all other PNGs unchanged.')
