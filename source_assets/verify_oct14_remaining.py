from install_oct12_batch import *
names=['zombie_in_green','zombie_king','zombie_mac','zombie_punk','zombie_redcoat','zombie_santa','zombie_sumo']
changed=[];unchanged=[]
for n in names:
    before=json.loads((ROOT/'source_assets'/n/'oct14_before.json').read_text())
    actions=['walk','melee'] if n=='zombie_in_green' else ['walk']
    expected={f'{n}_{a}_{d}.png' for a in actions for d in ['up','right','down','left']}
    actual=set()
    for key,h in before.items():
        filename=Path(key).name;p=DEST/n/filename
        if digest(p)!=h:actual.add(filename);changed.append(str(p))
        else:unchanged.append(str(p))
    assert actual==expected,(n,actual^expected)
    for filename in expected:
        im=Image.open(DEST/n/filename).convert('RGBA')
        assert im.size==(128,32) and im.getchannel('A').getextrema()==(0,255)
        cells=[im.crop((j*32,0,j*32+32,32)) for j in range(4)]
        assert all(c.getbbox() for c in cells)
        assert len({c.tobytes() for c in cells})==4
    for a in actions:
        l=Image.open(DEST/n/f'{n}_{a}_left.png');r=Image.open(DEST/n/f'{n}_{a}_right.png')
        for j in range(4):assert l.crop((j*32,0,j*32+32,32)).tobytes()==ImageOps.mirror(r.crop((j*32,0,j*32+32,32))).tobytes()
print(f'Validated {len(changed)} changed PNGs; {len(unchanged)} unrelated PNGs unchanged. All side frames mirrored in original time order.')
