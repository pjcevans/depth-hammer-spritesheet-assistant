import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import install_oct12_batch as base
import numpy as np

def blue_bounds(im,unused=False):
    a=np.array(im); rgb=a[:,:,:3].astype(float)
    m=(a[:,:,3]>128)&(rgb[:,:,2]>rgb[:,:,0]*1.5)&(rgb[:,:,2]>70)
    y,x=np.where(m)
    return int(x.min()),int(y.min()),int(x.max())+1,int(y.max())+1

base.bounds=blue_bounds
base.install(dict(name='uruk_berserker',action='melee',directions=['up','right','down'],source='rigid_spear_melee.png',reference='walk',mirror=True))
n='uruk_berserker'
b=json.loads((base.ROOT/'source_assets'/n/'before_attack_fix.json').read_text())
changed={f for f,h in b.items() if base.digest(base.DEST/n/f)!=h}
assert changed=={n+'_melee_'+d+'.png' for d in ('up','right','left','down')},changed
print('Only four melee sheets changed; all other PNGs unchanged.')
