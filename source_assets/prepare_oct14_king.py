from install_oct12_batch import *
folder=ROOT/'source_assets/zombie_king'
im=Image.open(folder/'oct14_walk.png').convert('RGBA')
a=np.array(im);a[a[:,:,3]<=128]=0
# Row silhouettes overlap in global projection; separate in each column.
out=Image.new('RGBA',(1600,1600))
for j in range(4):
    left=round(j*im.width/4);right=round((j+1)*im.width/4)
    part=a[:,left:right].copy()
    parts=[np.zeros_like(part) for _ in range(4)]
    for x in range(part.shape[1]):
        cuts=[0]+[gap(part[:,x,3]>0,target,60) for target in [330,630,920]]+[im.height]
        for row,(t,b) in enumerate(zip(cuts,cuts[1:])):parts[row][t:b,x]=part[t:b,x]
    assert sum(np.count_nonzero(p[:,:,3]) for p in parts)==np.count_nonzero(part[:,:,3])
    for row,p in enumerate(parts):
        f=Image.fromarray(p);f=f.crop(f.getbbox())
        assert max(f.size)<400
        out.alpha_composite(f,(j*400+(400-f.width)//2,row*400+(400-f.height)//2))
out.save(folder/'oct14_walk_separated.png')
