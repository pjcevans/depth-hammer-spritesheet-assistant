"""Re-export original right melee art at one shared scale and registration."""
from pathlib import Path
import sys,hashlib
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from normalize_anim import resize_rgba_canvas
HERE=Path(__file__).resolve().parent
NAME='safir_king_of_the_yeeks'
DEST=ROOT.parent/'depth-hammer/assets/entity_sprites/default/monsters'/NAME
target=DEST/f'{NAME}_melee_right.png'
before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in DEST.glob('*.png') if p!=target}
backup=HERE/'melee_right_before_scale_fix.png'
if not backup.exists(): backup.write_bytes(target.read_bytes())
source=Image.open(HERE/f'{NAME}_melee.png').convert('RGBA')
cw,ch=source.width//4,source.height//4
frames=[source.crop((j*cw,ch,(j+1)*cw,2*ch)) for j in range(4)]
for f in frames:
    a=np.array(f.getchannel('A')); a[a<=128]=0; f.putalpha(Image.fromarray(a))
boxes=[f.getbbox() for f in frames]
shared=(min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes))
width,height=shared[2]-shared[0],shared[3]-shared[1]
scale=min(32/width,32/height)
size=(round(width*scale),round(height*scale))
out=Image.new('RGBA',(128,32))
for j,f in enumerate(frames):
    # Identical source crop and resize for all poses: staff/glow bounds cannot
    # change head magnification or character registration between frames.
    r=resize_rgba_canvas(f.crop(shared),size,pixel_art=True,settings={'preblur':.15,'colour_count':64})
    out.alpha_composite(r,(j*32+(32-size[0])//2,32-size[1]))
assert out.size==Image.open(target).size==(128,32)
assert out.getchannel('A').getextrema()==(0,255)
assert all(out.crop((j*32,0,j*32+32,32)).getbbox() for j in range(4))
out.save(target)
assert all(hashlib.sha256((DEST/n).read_bytes()).hexdigest()==h for n,h in before.items())
print('Installed right melee only; original artwork, shared crop',shared,'scale',round(scale,5),'output',size)
print('All other Safir PNGs unchanged.')
