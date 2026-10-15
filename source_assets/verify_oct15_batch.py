from install_oct12_batch import *
names=['zombie_the_giant','zombie_wizzard','zombie_wrestler','zzorag']
changed=[];unchanged=[]
for n in names:
    before=json.loads((ROOT/'source_assets'/n/'oct15_before.json').read_text())
    actions=['walk'] if n=='zombie_wrestler' else ['walk','melee']
    expected=set()
    for a in actions:
        ds=['left','right'] if a=='walk' and n in ['zombie_wrestler','zzorag'] else ['up','left','right','down']
        expected.update(f'{n}_{a}_{d}.png' for d in ds)
    actual=set()
    for filename,h in before.items():
        if digest(DEST/n/filename)!=h:actual.add(filename);changed.append(filename)
        else:unchanged.append(filename)
    assert actual==expected,(n,actual^expected)
    for filename in expected:
        im=Image.open(DEST/n/filename).convert('RGBA')
        assert im.size==(128,32) and im.getchannel('A').getextrema()==(0,255)
        cells=[im.crop((j*32,0,j*32+32,32)) for j in range(4)]
        assert all(c.getbbox() for c in cells) and len({c.tobytes() for c in cells})==4
    for a in actions:
        l=Image.open(DEST/n/f'{n}_{a}_left.png');r=Image.open(DEST/n/f'{n}_{a}_right.png')
        for j in range(4):assert l.crop((j*32,0,j*32+32,32)).tobytes()==ImageOps.mirror(r.crop((j*32,0,j*32+32,32))).tobytes()
print(f'Validated {len(changed)} changed sheets; {len(unchanged)} unrelated PNGs unchanged; dimensions, alpha, unique frames and per-frame mirrors pass.')
