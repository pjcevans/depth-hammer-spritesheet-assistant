---
name: edit-sprite-animation
description: Improve or replace existing Depth Hammer sprite animations using the spritesheet assistant workflow, preserving character style and installing game-ready assets. Use for animation-art fixes, not new mob gameplay registration.
---

# Edit sprite animation

## Locate the actual assets

- The assistant normally reads the sibling game's `assets/entity_sprites/default` folder directly. Check `app_config.json` and `app.py` root discovery if a custom path is configured. A game PNG replacement is therefore visible in the assistant Library; do not change assistant code just to synchronize it.
- Inspect the target sheet and a relevant existing animation or portrait to preserve the character's palette, proportions, clothing and rendering style.
- Identify ambiguous parts from the larger reference before describing them in a generation prompt. User corrections override visual guesses: Glitchwind's floating "fists" are piles of crossed weapons and handles, not metal gauntlets. Keep that construction when animating them.
- Read destination repository instructions before writing. Preserve unrelated artwork and local changes.

## Make only the needed art

- For matching left/right actions, prefer creating or correcting one direction and horizontally mirroring **each frame** to obtain the other. Keep frames in their original temporal order: flipping the whole strip reverses that order. Use a deterministic image transform for mirroring; do not spend a second generation on an equivalent direction.
- Generate separate directions only when meaningful asymmetry (such as fixed weapon handedness or side-specific costume details) needs to be preserved, or the user requests it.
- For walking, ask for alternating contact and passing poses, readable leg separation, lifted passing feet and modest opposing arm motion. Keep torso/head orientation stable; rotating the character between frames reads as pivoting.
- Match movement to anatomy: creeping needs low reaching steps and weight transfer; floating creatures need flowing body details, restrained hover and trailing parts, not walking legs. A whirlwind should remain upright and recognizably the same size while its ribbons turn.
- For attacks, distribute wind-up, extension/strike and recovery across the available frames. One isolated impact pose amid idle poses reads as a jolt. Move the actual attacking parts: spear and arms, jaws for a bite, or detached weapon clusters for Glitchwind. Up/down attacks need foreshortening and displacement toward the top/bottom of the image, not a reused sideways strike or a spear merely raised overhead.
- If an old attack reference keeps reproducing the faulty poses, use the walk/portrait for identity and describe the new action explicitly. Inspect the generated result before installation; a prompt describing a clear attack does not prove the image contains one.
- Keep existing filenames, frame count, dimensions, transparency and timing contract. Consult `config/sprite_profiles.json` rather than assuming every animation is four frames. Typical 1x1 walks are four 32x32 cells in a 128x32 strip.

## Benchmark attack size against movement

- Inspect the **installed directional walk frames**, not just the full-resolution source. Measure the visible body across several frames in each direction: head size, head-to-foot height, torso/vortex width and anchor. Exclude weapons, trails and detached strike effects from body measurements. For example, the goblin body was about 19–21px high in a 32px cell; its raised spear accounted for the remaining height.
- Use those measurements as the attack target. Lowering a spear must not enlarge the body to fill the cell, and extending a fist or effect must not shrink it. Keep one body scale through the attack cycle; normal pose compression or a lunge is different from rescaling the character.
- Reserve room for the attack in the generated composition. Prefer compact, foreshortened weapon poses or shorter effects if they exceed the existing cell. Do not silently shrink the whole character, clip the strike, or distort body proportions to make an oversized weapon fit. Keep walk assets unchanged when only attacks were requested.
- A shared scale across walk and attack sources works only when their source body dimensions already match. Otherwise calibrate each source against the same installed body landmarks. Independently fitting each frame's full alpha bounds causes size changes; even fitting each whole animation separately can make attacks smaller than walks.
- Preserve an appropriate body anchor (feet for grounded creatures, body centre for hovering ones). Cropping and bottom-aligning an effect extending below the feet can lift the character; use stable body landmarks or shared source bounds instead. Colour masks can help measure distinctive bodies, but reject faint edge noise and check that raised hands or coloured weapons do not contaminate the measurement.
- Check the resulting game-size body measurements as well as sheet dimensions. A roughly one-pixel rounding difference can be reasonable for a 32px sprite, but report actual measurements rather than claiming exact scale from identical canvas sizes. For very slight requested edits, preserve untouched frames and pixels where practical instead of regenerating the whole character.

## Normalize and install

- Reuse `normalize_anim.py` or the assistant's existing replacement workflow. Retain full-resolution sources under `source_assets/<character>/` using the matching directional asset names for individual replacements. These sources are local and Git-ignored.
- Use the known grid for evenly spaced generated sheets. Check crop bounds when faint alpha noise makes the character shrink or jitter; raise the alpha threshold only as needed, preserving legitimate detail. The bandit correction used a 4x1 grid, 32px cells, `--fit --quality-mode max --fit-padding 0 --alpha-threshold 128`; this is an example, not a universal preset.
- Mirror within cells after normalization when deriving an equivalent game-ready opposite direction. Preserve the corresponding source in a form that can reproduce the result.
- Put the finished PNGs directly in the game's existing character folder. Update only requested directions; keep `.import` files and unrelated animation sheets intact. Do not add gameplay registrations for an art-only replacement.
- Archive the final prompt and reproducible normalization choices with the local source. Validate every requested sheet before writing the group; hashes of unrelated PNGs provide a lightweight check that walk/death/portrait assets stayed unchanged. Local per-character installers may be useful examples, but their paths, row counts, masks and fitting parameters are not universal defaults.

## Finish without a preview pipeline

- The user's preferred workflow is **install the assets, then let them check in the game or assistant**. Do not generate HTML comparisons, GIFs, contact sheets, preview sites, screenshots or review folders unless requested. Do not open the browser or launch the game merely to demonstrate the change.
- Perform lightweight file checks: readable PNG, expected dimensions/frame count, actual alpha transparency and nonempty frames. No new tests or elaborate visual QA pipeline for an asset-only edit. Honor the game's restriction on running Godot/tests.
- Report which assets were installed and any material limitation briefly. Do not wait for visual approval before installing replacements the user already requested.
