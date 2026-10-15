"""Install full redesigned Hoarder set; preserve Hunter, Fiend and PNG contracts."""
from pathlib import Path
import sys,json,hashlib
import numpy as np
from PIL import Image,ImageOps
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT/'source_assets'))
from install_oct12_batch import extract,resize_rgba_canvas,DEST,digest
NAME='trophy_hoarder'
OUT=DEST/NAME

def strip(frames):
    # A common scale across the entire sequence preserves real crouch/death height.
    w=max(f.width for f in frames); h=max(f.height for f in frames)
    scale=min(32/w,32/h)
    out=Image.new('RGBA',(len(frames)*32,32))
    for j,f in enumerate(frames):
        size=(max(1,round(f.width*scale)),max(1,round(f.height*scale)))
        r=resize_rgba_canvas(f,size,pixel_art=True,settings={'preblur':.15,'colour_count':64})
        out.alpha_composite(r,(j*32+(32-size[0])//2,32-size[1]))
    print('Sequence heights',[round(f.height*scale) for f in frames])
    return out

outputs={}
for action in ('walk','melee'):
    fs=extract(HERE/f'hench_{action}.png',3)
    for row,d in enumerate(('up','right','down')):
        outputs[f'{NAME}_{action}_{d}.png']=strip(fs[row*4:row*4+4])
    r=outputs[f'{NAME}_{action}_right.png']; l=Image.new('RGBA',r.size)
    for j in range(4): l.paste(ImageOps.mirror(r.crop((32*j,0,32*j+32,32))),(32*j,0))
    outputs[f'{NAME}_{action}_left.png']=l
for action in ('death','deathfire','wake'):
    outputs[f'{NAME}_{action}.png']=strip(extract(HERE/f'hench_{action}.png',2))
ref=Image.open(HERE/'hench_reference.png').convert('RGBA')
a=np.array(ref.getchannel('A')); a[a<=128]=0; ref.putalpha(Image.fromarray(a)); ref=ref.crop(ref.getbbox())
for size,name in [(32,f'{NAME}.png'),(256,f'{NAME}_large.png')]:
    s=min(size/ref.width,size/ref.height); wh=(round(ref.width*s),round(ref.height*s))
    r=resize_rgba_canvas(ref,wh,pixel_art=True,settings={'preblur':.15,'colour_count':64 if size==32 else 128})
    im=Image.new('RGBA',(size,size)); im.alpha_composite(r,((size-wh[0])//2,size-wh[1])); outputs[name]=im
assert set(outputs)=={p.name for p in OUT.glob('*.png')}
for name,im in outputs.items():
    assert im.size==Image.open(OUT/name).size,(name,im.size)
    lo,hi=im.getchannel('A').getextrema(); assert lo==0 and hi>=240,(name,lo,hi)
    if im.width>im.height:
        fs=[im.crop((x,0,x+32,32)) for x in range(0,im.width,32)]
        assert all(f.getbbox() for f in fs)
        assert len({f.tobytes() for f in fs})==len(fs),name
for name,im in outputs.items(): im.save(OUT/name); print('Installed',name,im.size)
before=json.loads((HERE/'hench_before.json').read_text())
changed={p.replace('\\','/') for p,h in before.items() if digest(DEST/p)!=h}
assert changed=={f'{NAME}/{n}' for n in outputs},changed
print('All 13 Hoarder PNGs replaced; Hunter and Fiend unchanged.')
