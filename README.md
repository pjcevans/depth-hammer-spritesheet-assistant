# Depth Hammer Spritesheet Assistant

A local browser interface backed by Python. It browses existing player and monster artwork, animates sprite strips, builds editable AI prompts, normalises generated sheets and safely exports complete asset folders.

## Install and run on Windows

1. Extract the complete `depth-hammer-spritesheet-assistant` folder inside `depth-hammer\assets`.
2. Double-click `run_spritesheet_assistant.bat`.
3. The first run creates a private `.venv` and installs Pillow and NumPy. Later runs start immediately.
4. Chrome opens automatically. Keep the launcher window open while using the app.

The expected layout is:

```text
depth-hammer\assets\
├─ entity_sprites\default\
│  ├─ monsters\
│  └─ player\
└─ depth-hammer-spritesheet-assistant\
   └─ run_spritesheet_assistant.bat
```

If the app is stored elsewhere, open **Settings** and enter the absolute path to `depth-hammer\assets\entity_sprites\default`.

## Creation workflow

1. Open **New Monster** or **New Player**.
2. Enter the name, lowercase folder prefix and visual description.
3. Generate the prompts and copy each prompt into your image-generation conversation. Use the same base reference image for every animation prompt.
4. Upload the returned PNG for each prompt group. The normaliser runs immediately and shows the finished animations.
5. Resolve any frame-count or transparency errors.
6. Choose **Export to folder**.

Combined directional sheets are expected in this order:

```text
north frames, east frames, west frames, south frames
```

A 16-sprite directional upload becomes four 4-frame Godot strips. Eight-frame death, fire-death, wake and rest uploads remain eight-frame strips. The large examine image is preserved at its uploaded resolution and converted to RGBA.

## Export safety

- Monster exports contain 13 PNGs: base, large, and 11 animation strips.
- Player exports contain 25 PNGs: base, large, and 23 animation strips.
- Every processed frame must be present, readable and use real transparency.
- Final animation cells are exactly 32×32.
- The destination folder must not already exist, including a case-only name match.
- Files are staged and verified before the destination folder becomes visible.
- Existing folders are never merged, replaced or partially updated.
- `.import` files are deliberately not created; Godot generates them.

## Editing prompts and profiles

Prompt wording can be changed in the **Prompt Templates** tab. It is stored in `config\prompt_templates.json`; **Restore defaults** uses `config\prompt_templates.defaults.json`.

The required groups, output filenames, frame counts, direction order and playback speeds are data-driven in `config\sprite_profiles.json`.

The normaliser settings are in `mob_anim_batch.json`. The supplied defaults retain the existing alpha-aware downscaling, colour treatment, blob detection and left-facing melee reversal.

## Drafts

Uploads and processed previews are kept under `workspace\drafts`. This directory and `.venv` are ignored by Git. **Start fresh draft** removes only that draft's temporary files; it never touches exported game assets.

## Troubleshooting

- **Wrong number of frames detected:** ensure every sprite is separated by transparent space and there are no grid lines, labels, scenery or large detached effects.
- **Transparent background required:** remove any solid or checkerboard background from the uploaded PNG.
- **Sprites root not found:** set it explicitly in **Settings**.
- **First-run installation failed:** delete `.venv`, check Python and internet access, then run the batch file again.
- **Port already occupied:** the app automatically tries the next available local port.

To run the automated checks from the app folder:

```bat
.venv\Scripts\python -m unittest discover -s tests -v
```
