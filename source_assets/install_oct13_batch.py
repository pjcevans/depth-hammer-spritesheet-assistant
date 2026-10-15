"""Body-scale-preserving normalization of the October 13 requested replacements."""
from install_oct12_batch import *

def install_job(cfg):
    n=cfg['name']; folder=ROOT/'source_assets'/n
    fs=extract(folder/cfg['source'],cfg['rows'])
    outputs={}; report=[]
    for job in cfg['jobs']:
        action,d=job['action'],job.get('direction','')
        count=job.get('count',4); start=job['row']*4
        frames=fs[start:start+count]
        if job.get('source'): frames=extract(folder/job['source'],1)
        suffix='_'+d if d else ''
        filename=f'{n}_{action}{suffix}.png'
        refname=job.get('reference',f'{n}_walk_{d or "down"}.png')
        ref=Image.open(folder/cfg.get('original_folder','oct13_originals')/refname).convert('RGBA')
        def measure(f):
            if job.get('metric')=='red':
                a=np.array(f);rgb=a[:,:,:3].astype(float)
                mask=(a[:,:,3]>128)&(rgb[:,:,0]>rgb[:,:,1]*1.6)&(rgb[:,:,0]>rgb[:,:,2]*1.25)&(rgb[:,:,0]>40)
                y,x=np.where(mask)
                return int(x.min()),int(y.min()),int(x.max())+1,int(y.max())+1
            if job.get('metric')!='blue': return bounds(f)
            a=np.array(f);rgb=a[:,:,:3].astype(float)
            y,x=np.where((a[:,:,3]>128)&(rgb[:,:,2]>rgb[:,:,0]*1.5)&(rgb[:,:,2]>rgb[:,:,1]*1.4)&(rgb[:,:,2]>70))
            return int(x.min()),int(y.min()),int(x.max())+1,int(y.max())+1
        rb=[measure(ref.crop((j*32,0,j*32+32,32))) for j in range(ref.width//32)]
        axis=job.get('axis','height'); dim=lambda b:b[2]-b[0] if axis=='width' else b[3]-b[1]
        target=float(np.median([dim(b) for b in rb]))
        bb=[measure(f) for f in frames]
        source_dim=dim(bb[job['scale_frame']]) if 'scale_frame' in job else float(np.median([dim(b) for b in bb]))
        scale=job.get('scale',target/source_dim)
        scale=min(scale,32/max(f.width for f in frames),32/max(f.height for f in frames))
        actual=[round(dim(b)*scale) for b in bb]
        measured=actual[job['scale_frame']] if 'scale_frame' in job else float(np.median(actual))
        assert abs(measured-target)<=job.get('tolerance',2),(n,filename,target,actual)
        strip=Image.new('RGBA',(count*32,32))
        for j,f in enumerate(frames):
            size=(max(1,round(f.width*scale)),max(1,round(f.height*scale)))
            if job.get('fixed_box'): size=tuple(job['fixed_box'])
            r=resize_rgba_canvas(f,size,pixel_art=True,settings={'preblur':.15,'colour_count':64})
            strip.alpha_composite(r,(j*32+(32-size[0])//2,32-size[1]))
        outputs[filename]=strip
        if job.get('mirror'):
            opposite='left' if d=='right' else 'right'; mir=Image.new('RGBA',strip.size)
            for j in range(count): mir.paste(ImageOps.mirror(strip.crop((j*32,0,j*32+32,32))),(j*32,0))
            outputs[f'{n}_{action}_{opposite}.png']=mir
        if job.get('fixed_box'): actual=[job['fixed_box'][0 if axis=='width' else 1]]*count
        report.append(dict(file=filename,target=target,actual=actual,scale=scale,fixed_box=job.get('fixed_box')))
    for name,im in outputs.items():
        assert im.size==Image.open(DEST/n/name).size
        assert im.getchannel('A').getextrema()[0]==0
        assert all(im.crop((j*32,0,j*32+32,32)).getbbox() for j in range(im.width//32))
        assert len({im.crop((j*32,0,j*32+32,32)).tobytes() for j in range(im.width//32)})==im.width//32
    for name,im in outputs.items(): im.save(DEST/n/name)
    print(json.dumps(report));print('Installed',list(outputs))
    (folder/(Path(cfg['source']).stem+'_measurements.json')).write_text(json.dumps(report,indent=2))

if __name__=='__main__': install_job(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig')))
