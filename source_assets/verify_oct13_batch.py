from install_oct12_batch import *

all_dirs=('up','right','left','down')
scope={
 'uruk_warrior':{'walk':('left','right'),'melee':all_dirs},
 'white_dragon':{'walk':all_dirs,'melee':all_dirs},
 'white_worms':{'walk':all_dirs},
 'wolf':{'walk':('down',),'melee':all_dirs},
 'yeek':{'walk':all_dirs,'melee':all_dirs},
 'zombie1':{'walk':('left','right')},
 'zombie2':{'wake':('',)},
 'zombie3':{'walk':('left','right')},
 'zombie_beekeeper':{'walk':all_dirs},
}
expected={f'{n}/{n}_{a}'+('_'+d if d else '')+'.png' for n,actions in scope.items() for a,ds in actions.items() for d in ds}
before=json.loads((ROOT/'source_assets'/'oct13_large_batch_before.json').read_text())
changed={Path(p).as_posix() for p,h in before.items() if digest(DEST/p)!=h}
assert changed==expected,dict(missing=sorted(expected-changed),unexpected=sorted(changed-expected))
report={}
for rel in sorted(expected):
    path=DEST/rel;im=Image.open(path).convert('RGBA');n=Path(rel).parts[0]
    old=Image.open(ROOT/'source_assets'/n/'oct13_originals'/path.name)
    assert im.size==old.size
    count=8 if '_wake.' in rel else 4
    assert im.size==(count*32,32)
    assert im.getchannel('A').getextrema()==(0,255)
    cells=[im.crop((j*32,0,j*32+32,32)) for j in range(count)]
    assert all(c.getbbox() for c in cells)
    assert len({c.tobytes() for c in cells})==count
    report[rel]={'size':im.size,'bounds':[bounds(c) for c in cells]}
for n,actions in scope.items():
    for a,ds in actions.items():
        if 'left' not in ds or 'right' not in ds: continue
        r=Image.open(DEST/n/f'{n}_{a}_right.png').convert('RGBA');l=Image.open(DEST/n/f'{n}_{a}_left.png').convert('RGBA')
        assert all(ImageOps.mirror(r.crop((j*32,0,j*32+32,32))).tobytes()==l.crop((j*32,0,j*32+32,32)).tobytes() for j in range(4))
for d in all_dirs:
    bs=report[f'white_worms/white_worms_walk_{d}.png']['bounds']
    assert len(set(bs))==1
    assert all(b[2]-b[0]==27 and b[3]-b[1]==27 for b in bs)
(ROOT/'source_assets'/'oct13_batch_verification.json').write_text(json.dumps(report,indent=2))
print(f'PASS: {len(expected)} requested PNGs, correct frame counts/dimensions/alpha, per-frame mirrors, constant worm footprint; {len(before)-len(expected)} unrelated PNGs unchanged.')
