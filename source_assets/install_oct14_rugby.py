"""Install compact rugby dives at the same body scale as the new walks."""
from install_oct12_batch import *

n='zombie_in_green'
folder=ROOT/'source_assets'/n
frames=extract(folder/'oct14_melee.png',3)
walk=json.loads((folder/'oct14_walk_measurements.json').read_text())
outputs={};report=[]
# These sources have matching head/chest dimensions; do not fit crouch/dive
# silhouette heights to standing height. Lift airborne poses above ground.
for row,d in enumerate(['up','right','down']):
    scale=walk[row]['scale']
    lifts=[0,1,5 if d=='right' else 3,0]
    strip=Image.new('RGBA',(128,32))
    sizes=[]
    for j,f in enumerate(frames[row*4:row*4+4]):
        size=tuple(max(1,round(v*scale)) for v in f.size)
        assert max(size)<=32,(d,j,size)
        r=resize_rgba_canvas(f,size,pixel_art=True,settings={'preblur':.15,'colour_count':64})
        y=32-size[1]-lifts[j]
        assert y>=0
        strip.alpha_composite(r,(j*32+(32-size[0])//2,y))
        sizes.append(size)
    outputs[f'{n}_melee_{d}.png']=strip
    report.append(dict(direction=d,shared_walk_scale=scale,frame_sizes=sizes,lift_pixels=lifts))
right=outputs[f'{n}_melee_right.png'];left=Image.new('RGBA',right.size)
for j in range(4):left.paste(ImageOps.mirror(right.crop((j*32,0,j*32+32,32))),(j*32,0))
outputs[f'{n}_melee_left.png']=left
for filename,im in outputs.items():
    assert im.size==Image.open(DEST/n/filename).size==(128,32)
    assert im.getchannel('A').getextrema()==(0,255)
    assert len({im.crop((j*32,0,j*32+32,32)).tobytes() for j in range(4)})==4
for filename,im in outputs.items():im.save(DEST/n/filename)
(folder/'oct14_melee_measurements.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
