"""Install the October 2026 retouches with one scale per animation.

Uses the assistant's alpha-aware resizer. Preserves collapse/wake height changes
instead of fitting each cropped pose independently to a full-size cell.
"""
from pathlib import Path
import sys
import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from normalize_anim import resize_rgba_canvas

SOURCE = Path(__file__).resolve().parent
DEST = ROOT.parent / 'depth-hammer/assets/entity_sprites/default/monsters/bouldogre'
COUNTS = {'walk_right': 4, 'melee_down': 4, 'melee_right': 4,
          'melee_up': 4, 'death': 8, 'deathfire': 8, 'wake': 8}

def gap(projection, expected, radius):
    lo, hi = max(1, int(expected-radius)), min(len(projection)-1, int(expected+radius))
    candidates = np.flatnonzero(projection[lo:hi] == 0) + lo
    if not len(candidates):
        raise ValueError(f'No transparent cell separator near {expected}')
    return int(min(candidates, key=lambda x: abs(x-expected)))

for action, count in COUNTS.items():
    path = SOURCE / f'bouldogre_{action}.png'
    image = Image.open(path).convert('RGBA')
    alpha = np.array(image.getchannel('A'))
    alpha[alpha <= 128] = 0
    image.putalpha(Image.fromarray(alpha))
    ys = [0, image.height]
    if count == 8:
        ys.insert(1, gap((alpha > 0).sum(axis=1), image.height / 2, image.height * .2))
    frames = []
    for top, bottom in zip(ys, ys[1:]):
        projection = (alpha[top:bottom] > 0).sum(axis=0)
        xs = [0] + [gap(projection, image.width*i/4, image.width/12) for i in range(1,4)] + [image.width]
        for left, right in zip(xs, xs[1:]):
            cell = image.crop((left, top, right, bottom))
            box = cell.getbbox()
            if box is None:
                raise ValueError(f'Empty frame in {action}')
            frames.append(cell.crop(box))
    scale = min(64 / max(f.width for f in frames), 64 / max(f.height for f in frames))
    output = Image.new('RGBA', (count*64, 64))
    sizes = []
    for i, frame in enumerate(frames):
        size = (max(1, round(frame.width*scale)), max(1, round(frame.height*scale)))
        sprite = resize_rgba_canvas(frame, size, pixel_art=True,
                                   settings={'preblur': .15, 'sharpen': 1.45})
        output.alpha_composite(sprite, (i*64+(64-size[0])//2, 64-size[1]))
        sizes.append(size)
    output.save(DEST / path.name)
    print(action, sizes)

for action in ('walk', 'melee'):
    right = Image.open(DEST / f'bouldogre_{action}_right.png').convert('RGBA')
    left = Image.new('RGBA', right.size)
    for i in range(4):
        left.paste(ImageOps.mirror(right.crop((i*64, 0, (i+1)*64, 64))), (i*64, 0))
    name = f'bouldogre_{action}_left.png'
    left.save(DEST / name)
    left.save(SOURCE / name)

for action, count in {**COUNTS, 'walk_left': 4, 'melee_left': 4}.items():
    image = Image.open(DEST / f'bouldogre_{action}.png')
    assert image.size == (count*64, 64) and image.mode == 'RGBA'
    assert image.getchannel('A').getextrema() == (0, 255)
    assert all(image.crop((i*64, 0, (i+1)*64, 64)).getbbox() for i in range(count))
print('Validated all nine installed sheets.')
