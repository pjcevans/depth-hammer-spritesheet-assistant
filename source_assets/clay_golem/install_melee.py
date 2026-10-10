"""Normalize authored melee poses at shared scale, anchored to the feet."""
from pathlib import Path
import sys
from collections import deque
import numpy as np
from PIL import Image, ImageOps, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from normalize_anim import resize_rgba_canvas, find_blobs

SOURCE = Path(__file__).resolve().parent
DEST = ROOT.parent / 'depth-hammer/assets/entity_sprites/default/monsters/clay_golem'
outputs = {}
for direction in ('down', 'right', 'up'):
    image = Image.open(SOURCE / f'clay_golem_melee_{direction}.png').convert('RGBA')
    alpha = np.array(image.getchannel('A'))
    alpha[alpha <= 128] = 0
    image.putalpha(Image.fromarray(alpha))
    blobs = sorted([b for b in find_blobs(image, 128) if b.opaque_pixel_count > 10000], key=lambda b:b.bbox[0])
    assert len(blobs) == 4, (direction, len(blobs))
    frames = []
    for blob in blobs:
        l, t, r, b = blob.bbox
        seed_x = l + int(np.flatnonzero(alpha[t,l:r] > 128)[0])
        available = alpha[t:b,l:r] > 128
        component = np.zeros(available.shape, dtype=bool)
        queue = deque([(seed_x-l, 0)])
        component[0,seed_x-l] = True
        while queue:
            x,y = queue.popleft()
            for dy in (-1,0,1):
                for dx in (-1,0,1):
                    nx,ny = x+dx,y+dy
                    if 0 <= nx < r-l and 0 <= ny < b-t and available[ny,nx] and not component[ny,nx]:
                        component[ny,nx] = True
                        queue.append((nx,ny))
        frame = image.crop(blob.bbox)
        frame.putalpha(Image.fromarray(np.where(component, np.array(frame.getchannel('A')), 0).astype('uint8')))
        foot_mask = np.array(frame.getchannel('A'))[int(frame.height*.90):] > 128
        xs = np.where(foot_mask)[1]
        anchor = (int(xs.min()) + int(xs.max()) + 1)/2
        frames.append((frame, anchor))
    left = max(a for f, a in frames)
    right = max(f.width-a for f, a in frames)
    scale = min(32/(left+right), 32/max(f.height for f, a in frames))
    anchor_out = left*scale + (32-(left+right)*scale)/2
    strip = Image.new('RGBA', (128, 32))
    for i, (frame, anchor) in enumerate(frames):
        size = (max(1, round(frame.width*scale)), max(1, round(frame.height*scale)))
        sprite = resize_rgba_canvas(frame, size, pixel_art=True, settings={'preblur': .15})
        x = max(0, min(32-size[0], round(anchor_out-anchor*scale)))
        strip.alpha_composite(sprite, (i*32+x, 32-size[1]))
    outputs[direction] = strip
    print(direction, 'shared scale', round(scale, 4), 'feet anchored')

left = Image.new('RGBA', (128,32))
for i in range(4):
    left.paste(ImageOps.mirror(outputs['right'].crop((i*32,0,(i+1)*32,32))), (i*32,0))
outputs['left'] = left
for direction, strip in outputs.items():
    assert strip.size == (128,32) and strip.getchannel('A').getextrema() == (0,255)
    frames = [strip.crop((i*32,0,(i+1)*32,32)) for i in range(4)]
    assert all(f.getbbox() for f in frames)
    assert len({f.tobytes() for f in frames}) == 4
    strip.save(DEST / f'clay_golem_melee_{direction}.png')
left.save(SOURCE / 'clay_golem_melee_left.png')
print('Installed and validated four 128x32 RGBA melee strips.')
