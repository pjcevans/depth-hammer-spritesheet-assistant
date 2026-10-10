---
name: edit-sprite-animation
description: Improve or replace existing Depth Hammer sprite animations using the spritesheet assistant workflow, preserving character style and installing game-ready assets. Use for animation-art fixes, not new mob gameplay registration.
---

# Edit sprite animation

## Locate the actual assets

- The assistant normally reads the sibling game's `assets/entity_sprites/default` folder directly. Check `app_config.json` and `app.py` root discovery if a custom path is configured. A game PNG replacement is therefore visible in the assistant Library; do not change assistant code just to synchronize it.
- Inspect the target sheet and a relevant existing animation or portrait to preserve the character's palette, proportions, clothing and rendering style.
- Read destination repository instructions before writing. Preserve unrelated artwork and local changes.

## Make only the needed art

- For matching left/right actions, prefer creating or correcting one direction and horizontally mirroring **each frame** to obtain the other. Keep frames in their original temporal order: flipping the whole strip reverses that order. Use a deterministic image transform for mirroring; do not spend a second generation on an equivalent direction.
- Generate separate directions only when meaningful asymmetry (such as fixed weapon handedness or side-specific costume details) needs to be preserved, or the user requests it.
- For walking, ask for alternating contact and passing poses, readable leg separation, lifted passing feet and modest opposing arm motion. Keep torso/head orientation stable; rotating the character between frames reads as pivoting.
- Keep existing filenames, frame count, dimensions, transparency and timing contract. Consult `config/sprite_profiles.json` rather than assuming every animation is four frames. Typical 1x1 walks are four 32x32 cells in a 128x32 strip.

## Normalize and install

- Reuse `normalize_anim.py` or the assistant's existing replacement workflow. Retain full-resolution sources under `source_assets/<character>/` using the matching directional asset names for individual replacements. These sources are local and Git-ignored.
- Use the known grid for evenly spaced generated sheets. Check crop bounds when faint alpha noise makes the character shrink or jitter; raise the alpha threshold only as needed, preserving legitimate detail. The bandit correction used a 4x1 grid, 32px cells, `--fit --quality-mode max --fit-padding 0 --alpha-threshold 128`; this is an example, not a universal preset.
- Mirror within cells after normalization when deriving an equivalent game-ready opposite direction. Preserve the corresponding source in a form that can reproduce the result.
- Put the finished PNGs directly in the game's existing character folder. Update only requested directions; keep `.import` files and unrelated animation sheets intact. Do not add gameplay registrations for an art-only replacement.

## Finish without a preview pipeline

- The user's preferred workflow is **install the assets, then let them check in the game or assistant**. Do not generate HTML comparisons, GIFs, contact sheets, preview sites, screenshots or review folders unless requested. Do not open the browser or launch the game merely to demonstrate the change.
- Perform lightweight file checks: readable PNG, expected dimensions/frame count, actual alpha transparency and nonempty frames. No new tests or elaborate visual QA pipeline for an asset-only edit. Honor the game's restriction on running Godot/tests.
- Report which assets were installed and any material limitation briefly. Do not wait for visual approval before installing replacements the user already requested.
