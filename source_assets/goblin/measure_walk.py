from pathlib import Path
from PIL import Image
import numpy as np
DEST=Path(__file__).resolve().parents[3]/'depth-hammer/assets/entity_sprites/default/monsters/goblin'
def body_bounds(im):
    a=np.array(im.convert('RGBA')).astype(float)
    m=(a[:,:,1]>a[:,:,0]*1.2)&(a[:,:,1]>a[:,:,2]*1.6)&(a[:,:,3]>200)
    ys=np.where(m.sum(axis=1)>=max(3,im.width*.035))[0]
    return int(ys.min()),int(ys.max())+1
if __name__=='__main__':
    for f in DEST.glob('*walk*.png'):
        im=Image.open(f)
        print(f.name,[body_bounds(im.crop((32*i,0,32*i+32,32))) for i in range(4)])
