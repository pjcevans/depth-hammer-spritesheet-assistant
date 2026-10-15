"""Install requested October 12 sprite fixes from archived generated sources."""
from pathlib import Path
import sys,json,hashlib
import numpy as np
from PIL import Image,ImageOps
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from normalize_anim import resize_rgba_canvas
DEST=ROOT.parent/'depth-hammer/assets/entity_sprites/default/monsters'
NAMES=['hyena','kobold','master_yeek','moss_champion','orc_archer','porcupine']
STATE=Path(__file__).with_name('oct12_before.json')
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def capture():
    data={str(p.relative_to(DEST)):digest(p) for n in NAMES for p in (DEST/n).glob('*.png')}
    if not STATE.exists(): STATE.write_text(json.dumps(data,indent=2))
    for n in NAMES:
        print(n)
        for action in ('walk','melee'):
            for d in ('up','right','down'):
                im=Image.open(DEST/n/f'{n}_{action}_{d}.png').convert('RGBA')
                print(action,d,im.size,[bounds(im.crop((j*32,0,j*32+32,32))) for j in range(4)])
def bounds(im,green=False):
    a=np.array(im)
    m=a[:,:,3]>128
    if green=='brown':
        rgb=a[:,:,:3].astype(float)
        m &= (rgb[:,:,0]>rgb[:,:,1]*1.12)&(rgb[:,:,1]>rgb[:,:,2]*1.03)&(rgb[:,:,0]>35)
    elif green:
        rgb=a[:,:,:3].astype(float)
        m &= (rgb[:,:,1]>rgb[:,:,0]*1.05)&(rgb[:,:,1]>rgb[:,:,2]*1.4)
    ys,xs=np.where(m)
    assert len(xs)
    return int(xs.min()),int(ys.min()),int(xs.max())+1,int(ys.max())+1
def gap(p,target,radius):
    lo,hi=max(1,int(target-radius)),min(len(p)-1,int(target+radius))
    opts=np.flatnonzero(p[lo:hi]==0)+lo
    assert len(opts),('no separator',target)
    return int(min(opts,key=lambda x:abs(x-target)))
def extract(path,rows,threshold=128):
    im=Image.open(path).convert('RGBA'); a=np.array(im.getchannel('A')); a[a<=threshold]=0
    im.putalpha(Image.fromarray(a))
    ys=[0]+[gap((a>0).sum(axis=1),im.height*i/rows,im.height/(rows*4)) for i in range(1,rows)]+[im.height]
    out=[]
    for top,bottom in zip(ys,ys[1:]):
        p=(a[top:bottom]>0).sum(axis=0)
        try:
            xs=[0]+[gap(p,im.width*i/4,im.width/10) for i in range(1,4)]+[im.width]
        except AssertionError:
            # Disjoint sprites can overlap in horizontal projection. Separate along
            # transparent per-scanline seams, retaining every visible source pixel.
            row=np.array(im.crop((0,top,im.width,bottom)))
            parts=[np.zeros_like(row) for _ in range(4)]
            for y in range(row.shape[0]):
                cuts=[0]+[gap(row[y,:,3]>0,im.width*i/4,im.width/10) for i in range(1,4)]+[im.width]
                for j,(l,r) in enumerate(zip(cuts,cuts[1:])): parts[j][y,l:r]=row[y,l:r]
            assert sum(np.count_nonzero(part[:,:,3]) for part in parts)==np.count_nonzero(row[:,:,3])
            for part in parts:
                f=Image.fromarray(part); out.append(f.crop(f.getbbox()))
            continue
        for l,r in zip(xs,xs[1:]):
            f=im.crop((l,top,r,bottom)); out.append(f.crop(f.getbbox()))
    return out
def install(cfg):
    n=cfg['name']; action=cfg['action']; ds=cfg['directions']
    fs=extract(ROOT/'source_assets'/n/cfg['source'],len(ds),cfg.get('alpha_threshold',128)); outputs={}
    for ri,d in enumerate(ds):
        frames=fs[ri*4:ri*4+4]
        if d in cfg.get('source_overrides',{}):
            frames=extract(ROOT/'source_assets'/n/cfg['source_overrides'][d],1)
        ref=Image.open(DEST/n/f"{n}_{cfg['reference']}_{d}.png").convert('RGBA')
        green='brown' if cfg.get('brown') else cfg.get('green',False); axis=cfg.get('axis',{}).get(d,'height')
        bb=[bounds(f,green) for f in frames]
        rb=[bounds(ref.crop((j*32,0,j*32+32,32)),green) for j in range(4)]
        dim=lambda b:b[2]-b[0] if axis=='width' else b[3]-b[1]
        target=float(np.median([dim(b) for b in rb]))
        scale=target/float(np.median([dim(b) for b in bb]))
        fit=min(32/max(f.width for f in frames),32/max(f.height for f in frames))
        scale=min(scale,fit)
        actual=[round(dim(b)*scale) for b in bb]
        assert abs(float(np.median(actual))-target)<=cfg.get('tolerance',2),(n,d,target,actual)
        strip=Image.new('RGBA',(128,32))
        for j,f in enumerate(frames):
            size=(max(1,round(f.width*scale)),max(1,round(f.height*scale)))
            r=resize_rgba_canvas(f,size,pixel_art=True,settings={'preblur':.15,'colour_count':64})
            strip.alpha_composite(r,(j*32+(32-size[0])//2,32-size[1]))
        outputs[f'{n}_{action}_{d}.png']=strip
        print(n,d,axis,'reference',target,'new',actual)
    if cfg.get('mirror'):
        right=outputs[f'{n}_{action}_right.png']; left=Image.new('RGBA',right.size)
        for j in range(4): left.paste(ImageOps.mirror(right.crop((j*32,0,j*32+32,32))),(j*32,0))
        outputs[f'{n}_{action}_left.png']=left
    for name,im in outputs.items():
        assert im.size==Image.open(DEST/n/name).size==(128,32)
        lo,hi=im.getchannel('A').getextrema()
        assert lo==0 and hi>=240
        assert all(im.crop((j*32,0,j*32+32,32)).getbbox() for j in range(4))
        assert len({im.crop((j*32,0,j*32+32,32)).tobytes() for j in range(4)})==4
    for name,im in outputs.items(): im.save(DEST/n/name); print('Installed',name)
if __name__=='__main__':
    if sys.argv[1]=='capture': capture()
    else: install(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig')))
