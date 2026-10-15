from install_oct12_batch import DEST,STATE,digest
from PIL import Image,ImageOps
import json
requested={
 'hyena':['walk_'+d for d in ('up','right','left','down')],
 'kobold':['walk_left','walk_right'],
 'master_yeek':['walk_down'],
 'moss_champion':['melee_'+d for d in ('up','right','left','down')],
 'orc_archer':['walk_left','walk_right'],
 'porcupine':['melee_'+d for d in ('up','right','left','down')],
}
allowed={f'{n}/{n}_{a}.png' for n,actions in requested.items() for a in actions}
before=json.loads(STATE.read_text())
changed={p.replace('\\','/') for p,h in before.items() if digest(DEST/p)!=h}
assert changed==allowed,{'missing':sorted(allowed-changed),'unexpected':sorted(changed-allowed)}
for p in allowed:
    im=Image.open(DEST/p).convert('RGBA')
    assert im.size==(128,32)
    lo,hi=im.getchannel('A').getextrema(); assert lo==0 and hi>=240
    assert all(im.crop((j*32,0,j*32+32,32)).getbbox() for j in range(4))
for n,actions in requested.items():
    for a in ('walk','melee'):
        if a+'_left' not in actions: continue
        l=Image.open(DEST/n/f'{n}_{a}_left.png')
        r=Image.open(DEST/n/f'{n}_{a}_right.png')
        for j in range(4):
            box=(j*32,0,j*32+32,32)
            assert l.crop(box).tobytes()==ImageOps.mirror(r.crop(box)).tobytes()
print(f'Validated exactly {len(allowed)} requested sheets across six characters; all other PNGs unchanged.')
