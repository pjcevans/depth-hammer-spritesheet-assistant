# Depth Hammer Spritesheet Assistant

A local browser interface backed by Python. It browses existing player and monster artwork, animates sprite strips, builds editable AI prompts, normalises generated sheets and safely exports complete asset folders.

## Install and run on Windows

1. Extract the complete `depth-hammer-spritesheet-assistant` folder beside the `depth-hammer` repository.
2. Double-click `run_spritesheet_assistant.bat`.
3. The first run creates a private `.venv` and installs Pillow and NumPy. Later runs start immediately. After a Python reinstall, the launcher automatically preserves an unusable environment in `.venv-backup-*` and creates a fresh one.
4. Chrome opens automatically. Keep the launcher window open while using the app.

The recommended layout is:

```text
C:\code\
├─ depth-hammer\
│  └─ assets\entity_sprites\default\
│     ├─ monsters\
│     └─ player\
└─ depth-hammer-spritesheet-assistant\
   ├─ run_spritesheet_assistant.bat
   └─ source_assets\
```

The app automatically finds the sibling `depth-hammer` repository. If either folder has a different name or location, open **Settings** and enter the absolute path to `depth-hammer\assets\entity_sprites\default`.

## Creation workflow

1. Open **New Monster** or **New Player**.
2. Enter the name, lowercase folder prefix and visual description.
3. Choose a **Footprint**: 1×1 produces 32×32 frames and 2×2 produces 64×64 frames. The choice is saved with the draft and can be changed later; existing uploads are reprocessed automatically.
4. Optionally choose **Save details file** to keep a portable JSON copy of the name, folder, description, footprint, crop, detection choices and processing presets. **Load details file** restores them later. The live draft is also saved automatically.
5. Generate the prompts and copy each prompt into your image-generation conversation. Use the same base reference image for every animation prompt.
6. Upload one PNG, JPEG or WebP source artwork image. Drag, scale and position it in the crop tool; one crop produces the 256×256 `_large` image and the footprint-sized base sprite.
7. Open the size-labelled **output controls** to choose a quick preset or directly adjust the downscale filter, pre-shrink blur, sharpness, palette size, alpha-aware resizing and edge cleanup. The same controls are available for every animation upload.
8. Upload the returned PNG for each animation group. The normaliser runs immediately and shows the original-resolution frame in a large preview stage (up to 512×512) beside the true-size 32×32 or 64×64 in-game result. Uploading a replacement uses a new preview version, so stale browser-cached artwork is not shown. Existing Sprite Library assets are shown once at their actual stored size.
9. Resolve any remaining frame-count or transparency errors. Open **Sprite detection controls** to adjust the grid, alpha threshold, component area or row tolerance and reprocess the original upload without uploading it again.
10. Choose **Export to folder**. The game-ready files go to the sibling repository's `depth-hammer\assets\entity_sprites\default`, and the untouched full-size uploads stay local to the app in `depth-hammer-spritesheet-assistant\source_assets\<folder-name>`.

Combined directional sheets are expected in this order:

```text
north frames, east frames, west frames, south frames
```

A 16-sprite directional upload becomes four 4-frame Godot strips. Eight-frame death, fire-death, wake and rest uploads remain eight-frame strips. Wake sheets automatically use the best matching grid and are always flattened in standard reading order: left-to-right across the top row, then left-to-right across each following row. Other animations first use connected-sprite detection; if detached pieces such as floating fists produce too many blobs, the app ranks valid grid layouts by occupancy, clear boundaries and image geometry. This lets both 4×2 and single-row 8×1 sheets resolve correctly.

## Sprite Library details

Every existing player and monster folder has editable **Name**, **Description** and **Footprint** fields in the Sprite Library. New exports populate them automatically. Older folders show empty description fields and default to 1×1 until changed. Changing an existing set's footprint updates its metadata and validation expectations; it does not rewrite exported artwork. These details are stored in `entity_sprites\default\sprite_library_metadata.json`, outside the assistant folder, so replacing or updating the app does not discard them.

Directional animations can be replaced in logical blocks. **Replace all walk directions**, for example, accepts one combined 16-frame upload in north/east/west/south order, previews the four resulting strips, and updates `walk_up`, `walk_right`, `walk_left` and `walk_down` together. Melee, cast, mine and shoot use the same grouped workflow when present. Every directional card also retains **Replace only this direction** for exceptional one-strip fixes; that path updates only the selected PNG.

Standalone assets—base, large, death, deathfire, wake, rest and custom sheets—retain **Replace this asset**. Both editors provide the same relevant crop, 1×1/2×2 footprint, sprite-detection, grid, alpha, filter, sharpness, palette and cleanup controls used during creation. They show the full-resolution upload beside the finished game-size result and keep the current game files unchanged until **Apply replacement** is confirmed.

Applying a replacement is atomic: either every file in its block is updated or none are. An individual-direction replacement is independently atomic and does not touch the other three strips. The Library preview refreshes immediately, the untouched grouped upload is saved as `source_assets\<folder-name>\<folder-name>_<action>.png`, and standalone sources keep their exact asset name. Processing settings are retained for every affected asset and restored the next time that block or individual sheet is edited.

## New-folder export safety

- Monster exports contain 13 PNGs: base, large, and 11 animation strips.
- Player exports contain 25 PNGs: base, large, and 23 animation strips.
- Every export also keeps one full-resolution PNG per upload group in `source_assets\<folder-name>`, using names such as `bandit.png`, `bandit_walk.png` and `bandit_death.png`.
- Every processed animation frame must be present, readable and use real transparency. The crop tool warns, but does not block, when source artwork is fully opaque.
- Final animation cells are exactly 32×32 for 1×1 sets or 64×64 for 2×2 sets.
- The destination folder must not already exist, including a case-only name match.
- Files are staged and verified before the destination folder becomes visible.
- New-folder export never merges, replaces or partially updates an existing game folder.
- New-folder export never overwrites an existing full-size source folder. Confirmed single-asset Library edits update only their matching source PNG.
- `.import` files are deliberately not created; Godot generates them.

## Editing prompts and profiles

Prompt wording can be changed in the **Prompt Templates** tab. It is stored in `config\prompt_templates.json`; **Restore defaults** uses `config\prompt_templates.defaults.json`.

The required groups, output filenames, frame counts, direction order and playback speeds are data-driven in `config\sprite_profiles.json`.

The normaliser settings are in `mob_anim_batch.json`. The supplied defaults retain the existing alpha-aware downscaling, colour treatment, blob detection and left-facing melee reversal. Per-upload quality controls override only the current draft group; those overrides are saved with the draft and in portable creature details files.

## Drafts

Uploads, crop settings, detection settings, quality presets, name, folder, description and footprint are kept under `workspace\drafts`. This directory, `.venv` and `source_assets` are ignored by Git. **Start fresh draft** removes only that draft's temporary files; it never touches exported game or full-size source assets.

## Troubleshooting

- **Wrong number of frames detected:** leave detection on **Automatic** first. For an evenly arranged sheet, choose **Fixed grid** and set columns × rows to the required count. Connected-sprite controls remain available for unusually spaced sheets.
- **Transparent background required:** remove any solid or checkerboard background from the uploaded PNG.
- **Sprites root not found:** set it explicitly in **Settings**.
- **First-run installation failed:** check Python and internet access, then run the batch file again. Missing dependencies are retried automatically. Git Bash users can run `sh run_spritesheet_assistant.sh`; it uses the Windows launcher and does not depend on interactive Bash aliases.
- **Port already occupied:** the app automatically tries the next available local port.

To run the automated checks from the app folder:

```bat
.venv\Scripts\python -m unittest discover -s tests -v
```
