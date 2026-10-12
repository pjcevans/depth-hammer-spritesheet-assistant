"""Composite only a tiny directional gel-highlight patch into strike frame 3."""
from pathlib import Path
import sys
import hashlib
import numpy as np
from PIL import Image
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from normalize_anim import resize_rgba_canvas
SOURCE = Path(__file__).resolve().parent
DEST = ROOT.parent/'depth-hammer/assets/entity_sprites/default/monsters/gelatinous_cube'
targets = {f'gelatinous_cube_melee_{d}.png' for d in ('down','up')}
protected = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in DEST.glob('*.png') if p.name not in targets}
detail = Image.open(SOURCE/'melee_direction_detail.png').convert('RGBA')
for index,direction in enumerate(('down','up')):
    name = f'gelatinous_cube_melee_{direction}.png'
    backup = SOURCE/f'{name[:-4]}_before_direction_tweak.png'
    if not backup.exists():
        backup.write_bytes((DEST/name).read_bytes())
    original = Image.open(backup).convert('RGBA')
    old = original.crop((64,0,96,32))
    box = old.getbbox()
    cell = detail.crop((round(index*detail.width/2),0,round((index+1)*detail.width/2),detail.height))
    alpha = cell.getchannel('A').point(lambda a: 0 if a <= 128 else a)
    cell.putalpha(alpha)
    cell = cell.crop(cell.getbbox())
    resized = resize_rgba_canvas(cell,(box[2]-box[0],box[3]-box[1]),pixel_art=True)
    candidate = Image.new('RGBA',(32,32))
    candidate.paste(resized,(box[0],box[1]))
    a = np.array(old)
    b = np.array(candidate)
    # Only outer gel surface highlights; leave contents and silhouette intact.
    x0,x1 = 24,31
    y0,y1 = (19,30) if direction=='down' else (7,21)
    allowed = np.zeros((32,32),dtype=bool)
    allowed[y0:y1,x0:x1] = True
    allowed &= (a[:,:,3]>200) & (b[:,:,3]>200)
    edited = a.copy()
    for y,x in zip(*np.where(allowed)):
        edge = min(x-x0+1,x1-x,y-y0+1,y1-y)
        weight = .65 if edge>1 else .30
        edited[y,x,:3] = np.rint((1-weight)*a[y,x,:3]+weight*b[y,x,:3]).astype('uint8')
    assert np.array_equal(a[:,:,3],edited[:,:,3])
    assert np.array_equal(a[~allowed],edited[~allowed])
    original.paste(Image.fromarray(edited),(64,0))
    original.save(DEST/name)
    baseline = Image.open(backup).convert('RGBA')
    assert all(original.crop((i*32,0,i*32+32,32)).tobytes()==baseline.crop((i*32,0,i*32+32,32)).tobytes() for i in (0,1,3))
    print(name, 'changed pixels:',int(np.any(a!=edited,axis=2).sum()),'other frames and alpha unchanged')
assert all(hashlib.sha256((DEST/n).read_bytes()).hexdigest()==h for n,h in protected.items())
