#!/usr/bin/env python3
"""
normalize_anim.py

Drop-in animation normaliser for Godot 32x32 sprite strips.

This version keeps the no-argument batch workflow, but bakes in the sharper
large-image-to-32x32 settings you asked for. Running:

    py normalize_anim.py

now processes the whole current folder using roughly:

    --quality-mode max --preblur 0.15 --sharpen 1.45 --colour-count 48

The no-argument workflow processes any number of top-level PNGs. Completeness
checks now belong to the spritesheet assistant's editable mob/player profiles,
so the reusable normalizer no longer assumes that every folder is a complete
11-file mob or 23-file player animation set.

Install/update dependencies:

    py -m pip install --upgrade pillow numpy

numpy is optional but recommended. Without it, the script falls back to Pillow's
normal RGBA resize path.

The spritesheet assistant imports ``normalize_uploaded_sheet`` directly. That
API accepts any positive frame count; the assistant's editable sprite profiles
are responsible for deciding which finished files are required.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # type: ignore


DIRECTIONS: tuple[str, ...] = ("down", "left", "right", "up")

MOB_ANIMATION_SUFFIXES: tuple[str, ...] = (
    "_death.png",
    "_deathfire.png",
    "_melee_down.png",
    "_melee_left.png",
    "_melee_up.png",
    "_melee_right.png",
    "_wake.png",
    "_walk_down.png",
    "_walk_left.png",
    "_walk_right.png",
    "_walk_up.png",
)

PLAYER_ANIMATION_SUFFIXES: tuple[str, ...] = (
    *(f"_walk_{direction}.png" for direction in DIRECTIONS),
    *(f"_melee_{direction}.png" for direction in DIRECTIONS),
    *(f"_cast_{direction}.png" for direction in DIRECTIONS),
    *(f"_mine_{direction}.png" for direction in DIRECTIONS),
    *(f"_shoot_{direction}.png" for direction in DIRECTIONS),
    "_rest.png",
    "_death.png",
    "_deathfire.png",
)

ANIMATION_PROFILE_SUFFIXES: dict[str, tuple[str, ...]] = {
    "mob": MOB_ANIMATION_SUFFIXES,
    "player": PLAYER_ANIMATION_SUFFIXES,
}

LONG_ANIMATION_SUFFIXES: set[str] = {"_death.png", "_deathfire.png", "_wake.png", "_rest.png"}


class NormalizationError(RuntimeError):
    """A recoverable normalization failure suitable for GUI callers."""


def build_profile_rules(profile: str) -> list[dict[str, Any]]:
    if profile not in ANIMATION_PROFILE_SUFFIXES:
        raise ValueError(f"Unknown animation profile: {profile}")

    rules: list[dict[str, Any]] = []
    for suffix in ANIMATION_PROFILE_SUFFIXES[profile]:
        rule: dict[str, Any] = {
            "suffix": suffix,
            "frames": 8 if suffix in LONG_ANIMATION_SUFFIXES else 4,
            "required": True,
        }
        # Keep existing behaviour: generated left-facing melee sheets are often
        # detected in the opposite order, so reverse only that strip by default.
        if suffix == "_melee_left.png":
            rule["reverse"] = True
        rules.append(rule)
    return rules


MOB_BATCH_RULES = build_profile_rules("mob")
PLAYER_BATCH_RULES = build_profile_rules("player")
MOB_RULE_SUFFIXES = {rule["suffix"] for rule in MOB_BATCH_RULES}
DEFAULT_BATCH_RULES = MOB_BATCH_RULES + [
    {**rule, "required": False}
    for rule in PLAYER_BATCH_RULES
    if rule["suffix"] not in MOB_RULE_SUFFIXES
]


DEFAULT_BATCH_CONFIG: dict[str, Any] = {
    "recursive": False,
    "defaults": {
        "frames": 4,
        "cell": 32,
        "source_cols": None,
        "source_rows": None,
        "blob_detect": True,
        "fit": True,
        "allow_upscale": False,
        "align_hor": "center",
        "align_ver": "bottom",
        "offset_x": 0,
        "offset_y": 0,
        "alpha_threshold": 0,
        "allow_missing": False,
        "debug_preview": None,
        "print_debug": False,
        "row_tolerance": 12,
        "min_blob_area": 64,
        "reverse": False,
        "verbose": False,

        # Sharper whole-batch defaults.
        # These are now applied automatically by `py normalize_anim.py`.
        "quality_mode": "max",       # nearest | clean | max
        "downscale_filter": "auto",  # auto | nearest | box | bilinear | bicubic | lanczos
        "alpha_aware": True,
        "gamma_correct": True,
        "preblur": 0.15,             # lower = less blur; auto = more anti-grain blur
        "sharpen": 1.45,             # higher = crisper; try 1.1-1.8
        "quantize": True,
        "colour_count": 48,          # try 24/32 for chunkier, 64 for smoother
        "posterize_bits": 0,
        "alpha_cleanup": True,
        "alpha_low_cutoff": 6,
        "alpha_high_cutoff": 250,
        "hard_alpha": False,
        "despeckle": False,
        "despeckle_min_area": 2,
        "fit_padding": 0,
    },
    "rules": DEFAULT_BATCH_RULES,
}

RESAMPLE_FILTERS: dict[str, Image.Resampling] = {
    "nearest": Image.Resampling.NEAREST,
    "box": Image.Resampling.BOX,
    "bilinear": Image.Resampling.BILINEAR,
    "bicubic": Image.Resampling.BICUBIC,
    "lanczos": Image.Resampling.LANCZOS,
}


@dataclass
class CropFrame:
    image: Image.Image
    source_bbox: Optional[tuple[int, int, int, int]]
    placement: Optional[tuple[int, int]]
    original_size: Optional[tuple[int, int]] = None
    final_size: Optional[tuple[int, int]] = None
    oversized: bool = False
    scaled: bool = False


@dataclass
class Blob:
    bbox: tuple[int, int, int, int]
    opaque_pixel_count: int


@dataclass
class NormalizeOptions:
    frames: int
    cell: int | str
    source_cols: Optional[int]
    source_rows: Optional[int]
    blob_detect: bool
    fit: bool
    allow_upscale: bool
    align_hor: str
    align_ver: str
    offset_x: int
    offset_y: int
    alpha_threshold: int
    allow_missing: bool
    debug_preview: Optional[Path]
    print_debug: bool
    row_tolerance: int
    min_blob_area: int
    reverse: bool
    verbose: bool = False
    quality_mode: str = "max"
    downscale_filter: str = "auto"
    alpha_aware: bool = True
    gamma_correct: bool = True
    preblur: float | str = 0.15
    sharpen: float = 1.45
    quantize: bool = True
    colour_count: int = 48
    posterize_bits: int = 0
    alpha_cleanup: bool = True
    alpha_low_cutoff: int = 6
    alpha_high_cutoff: int = 250
    hard_alpha: bool = False
    despeckle: bool = False
    despeckle_min_area: int = 2
    fit_padding: int = 0


def warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)


def fail(message: str) -> None:
    raise NormalizationError(message)


def log(message: str, *, verbose: bool = False, force: bool = False) -> None:
    if force or verbose:
        print(message)


def list_top_level_pngs(folder: Path) -> list[Path]:
    return sorted(
        (path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".png"),
        key=lambda path: path.name.lower(),
    )


def format_expected_names(prefix: str, suffixes: tuple[str, ...]) -> list[str]:
    return [f"{prefix}{suffix}" for suffix in suffixes]


def describe_no_arg_override() -> str:
    return (
        "To bypass this safety gate for custom work, pass explicit arguments, for example:\n"
        "  py normalize_anim.py --batch-in-dir . --batch-out-dir output\n"
        "or use single-file mode with input, --out, --frames and --cell."
    )


def validate_expected_animation_files(folder: Path, profile: str, png_files: list[Path]) -> str:
    expected_suffixes = ANIMATION_PROFILE_SUFFIXES[profile]
    file_names = [path.name for path in png_files]
    matched_names: set[str] = set()
    missing_suffixes: list[str] = []
    duplicate_suffixes: dict[str, list[str]] = {}
    prefix_by_suffix: dict[str, str] = {}

    for suffix in expected_suffixes:
        matches = [name for name in file_names if name.endswith(suffix)]
        if not matches:
            missing_suffixes.append(suffix)
            continue
        if len(matches) > 1:
            duplicate_suffixes[suffix] = matches
            continue
        name = matches[0]
        matched_names.add(name)
        prefix_by_suffix[suffix] = name[: -len(suffix)]

    extra_names = sorted(set(file_names) - matched_names)
    prefixes = sorted(set(prefix_by_suffix.values()))
    common_prefix = prefixes[0] if len(prefixes) == 1 else ""

    problems: list[str] = []
    if missing_suffixes:
        problems.append(
            "Missing expected suffixes:\n"
            + "\n".join(f"  {suffix}" for suffix in missing_suffixes)
        )
    if duplicate_suffixes:
        duplicate_lines: list[str] = []
        for suffix, names in duplicate_suffixes.items():
            duplicate_lines.append(f"  {suffix}: {', '.join(names)}")
        problems.append("Multiple files matched the same expected suffix:\n" + "\n".join(duplicate_lines))
    if len(prefixes) > 1:
        prefix_lines = [f"  {suffix} -> {prefix}" for suffix, prefix in sorted(prefix_by_suffix.items())]
        problems.append(
            "Files do not share a single common prefix. Prefixes found:\n"
            + "\n".join(prefix_lines)
        )
    if len(prefixes) == 1 and common_prefix == "" and prefix_by_suffix:
        problems.append(
            "The shared filename prefix is empty. Expected names like "
            "<creaturename>_walk_down.png or player_walk_down.png."
        )
    if extra_names:
        problems.append(
            "Unexpected PNGs in the folder:\n"
            + "\n".join(f"  {name}" for name in extra_names)
        )

    if problems:
        expected_prefix = common_prefix or ("player" if profile == "player" else "creaturename")
        expected_names = format_expected_names(expected_prefix, expected_suffixes)
        fail(
            f"No-argument batch safety check failed for {folder.resolve()}.\n"
            f"Found {len(png_files)} top-level PNG file(s), so this was treated as a {profile} animation folder.\n\n"
            + "\n\n".join(problems)
            + "\n\nExpected exactly these files for this profile:\n"
            + "\n".join(f"  {name}" for name in expected_names)
            + "\n\nNothing was processed.\n"
            + describe_no_arg_override()
        )

    return common_prefix


def validate_no_argument_batch_folder(folder: Path) -> str:
    if not folder.exists() or not folder.is_dir():
        fail(f"No-argument batch input folder does not exist or is not a folder: {folder}")

    png_files = list_top_level_pngs(folder)
    file_count = len(png_files)
    if file_count == len(MOB_ANIMATION_SUFFIXES):
        profile = "mob"
    elif file_count == len(PLAYER_ANIMATION_SUFFIXES):
        profile = "player"
    else:
        found = "\n".join(f"  {path.name}" for path in png_files) or "  <none>"
        fail(
            f"No-argument batch safety check failed for {folder.resolve()}.\n"
            f"Found {file_count} top-level PNG file(s). Expected exactly "
            f"{len(MOB_ANIMATION_SUFFIXES)} for mob animations or "
            f"{len(PLAYER_ANIMATION_SUFFIXES)} for player animations.\n\n"
            f"PNG files found:\n{found}\n\n"
            "Nothing was processed.\n"
            + describe_no_arg_override()
        )

    common_prefix = validate_expected_animation_files(folder, profile, png_files)
    print(
        f"Safety check passed: detected {profile} animation batch "
        f"({file_count} PNGs, prefix='{common_prefix}')."
    )
    return profile


def parse_cell(value: str) -> int | str:
    if value.lower() == "auto":
        return "auto"
    try:
        parsed = int(value)
    except ValueError:
        fail("--cell must be an integer, e.g. 32, 48, 64, or 'auto'.")
    if parsed <= 0:
        fail("--cell must be greater than 0.")
    return parsed


def parse_preblur(value: str) -> float | str:
    if value.lower() == "auto":
        return "auto"
    try:
        parsed = float(value)
    except ValueError:
        fail("--preblur must be 'auto' or a number, e.g. 0, 0.15, 0.35.")
    if parsed < 0:
        fail("--preblur must be 0 or greater, or 'auto'.")
    return parsed


def ensure_rgba(image: Image.Image) -> Image.Image:
    return image if image.mode == "RGBA" else image.convert("RGBA")


def alpha_bbox(image: Image.Image, alpha_threshold: int) -> Optional[tuple[int, int, int, int]]:
    alpha = ensure_rgba(image).getchannel("A")
    if alpha_threshold <= 0:
        return alpha.getbbox()
    mask = alpha.point(lambda a: 255 if a > alpha_threshold else 0)
    return mask.getbbox()


def crop_visible(image: Image.Image, alpha_threshold: int) -> tuple[Image.Image, Optional[tuple[int, int, int, int]]]:
    bbox = alpha_bbox(image, alpha_threshold)
    if bbox is None:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0)), None
    return image.crop(bbox), bbox


def extract_grid_frames(
    image: Image.Image,
    source_cols: int,
    source_rows: int,
    alpha_threshold: int,
) -> list[CropFrame]:
    if source_cols <= 0 or source_rows <= 0:
        fail("--source-cols and --source-rows must be greater than 0.")

    frames: list[CropFrame] = []
    width, height = image.size
    for row in range(source_rows):
        for col in range(source_cols):
            left = round(col * width / source_cols)
            right = round((col + 1) * width / source_cols)
            top = round(row * height / source_rows)
            bottom = round((row + 1) * height / source_rows)
            source_cell = image.crop((left, top, right, bottom))
            cropped, local_bbox = crop_visible(source_cell, alpha_threshold)
            if local_bbox is None:
                full_bbox = None
            else:
                lx1, ly1, lx2, ly2 = local_bbox
                full_bbox = (left + lx1, top + ly1, left + lx2, top + ly2)
            frames.append(CropFrame(cropped, full_bbox, None))
    return frames


def find_blobs(image: Image.Image, alpha_threshold: int) -> list[Blob]:
    rgba = ensure_rgba(image)
    width, height = rgba.size
    alpha = rgba.getchannel("A")
    alpha_pixels = alpha.load()
    visited = bytearray(width * height)
    blobs: list[Blob] = []

    def idx(x: int, y: int) -> int:
        return y * width + x

    neighbours = (
        (-1, -1), (0, -1), (1, -1),
        (-1, 0),           (1, 0),
        (-1, 1),  (0, 1),  (1, 1),
    )

    for y in range(height):
        for x in range(width):
            i = idx(x, y)
            if visited[i]:
                continue
            visited[i] = 1
            if alpha_pixels[x, y] <= alpha_threshold:
                continue

            min_x = max_x = x
            min_y = max_y = y
            count = 0
            queue: deque[tuple[int, int]] = deque([(x, y)])
            while queue:
                cx, cy = queue.popleft()
                count += 1
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                for dx, dy in neighbours:
                    nx, ny = cx + dx, cy + dy
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    ni = idx(nx, ny)
                    if visited[ni]:
                        continue
                    visited[ni] = 1
                    if alpha_pixels[nx, ny] > alpha_threshold:
                        queue.append((nx, ny))
            blobs.append(Blob((min_x, min_y, max_x + 1, max_y + 1), count))
    return blobs


def sort_blobs_reading_order(blobs: list[Blob], row_tolerance: int) -> list[Blob]:
    if not blobs:
        return []
    blobs = sorted(blobs, key=lambda blob: (blob.bbox[1], blob.bbox[0]))
    rows: list[list[Blob]] = []
    for blob in blobs:
        y_center = (blob.bbox[1] + blob.bbox[3]) / 2
        placed = False
        for row in rows:
            row_y = sum((b.bbox[1] + b.bbox[3]) / 2 for b in row) / len(row)
            if abs(y_center - row_y) <= row_tolerance:
                row.append(blob)
                placed = True
                break
        if not placed:
            rows.append([blob])
    result: list[Blob] = []
    for row in rows:
        result.extend(sorted(row, key=lambda blob: blob.bbox[0]))
    return result


def extract_blob_frames(
    image: Image.Image,
    alpha_threshold: int,
    row_tolerance: int,
    min_blob_area: int,
) -> list[CropFrame]:
    blobs = [blob for blob in find_blobs(image, alpha_threshold) if blob.opaque_pixel_count >= min_blob_area]
    frames: list[CropFrame] = []
    for blob in sort_blobs_reading_order(blobs, row_tolerance):
        frames.append(CropFrame(image.crop(blob.bbox), blob.bbox, None))
    return frames


def choose_auto_cell_size(frames: Iterable[CropFrame]) -> int:
    max_side = 1
    for frame in frames:
        w, h = frame.image.size
        max_side = max(max_side, w, h)
    return max_side


def choose_resample_filter(options: NormalizeOptions, scale: float) -> Image.Resampling:
    if options.quality_mode == "nearest":
        return Image.Resampling.NEAREST
    if options.downscale_filter == "auto":
        return Image.Resampling.NEAREST if scale >= 1.0 else Image.Resampling.LANCZOS
    return RESAMPLE_FILTERS[options.downscale_filter]


def resolve_preblur_radius(preblur: float | str, scale: float) -> float:
    if isinstance(preblur, str):
        if preblur != "auto":
            fail("preblur must be 'auto' or a number.")
        if scale >= 1.0:
            return 0.0
        if scale < 0.075:
            return 0.65
        if scale < 0.125:
            return 0.50
        if scale < 0.25:
            return 0.35
        if scale < 0.5:
            return 0.20
        return 0.0
    return float(preblur)


def srgb_to_linear(values: Any) -> Any:
    if np is None:
        return values
    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(values: Any) -> Any:
    if np is None:
        return values
    values = np.clip(values, 0.0, 1.0)
    return np.where(values <= 0.0031308, values * 12.92, 1.055 * (values ** (1.0 / 2.4)) - 0.055)


def resize_float_channel(channel: Any, size: tuple[int, int], resample: Image.Resampling) -> Any:
    if np is None:
        raise RuntimeError("numpy unavailable")
    float_image = Image.fromarray(channel.astype("float32"), mode="F")
    resized = float_image.resize(size, resample=resample)
    return np.asarray(resized, dtype=np.float32)


def resize_rgba_alpha_aware_numpy(
    image: Image.Image,
    size: tuple[int, int],
    resample: Image.Resampling,
    gamma_correct: bool,
) -> Image.Image:
    if np is None:
        raise RuntimeError("numpy unavailable")

    arr = np.asarray(ensure_rgba(image), dtype=np.float32) / 255.0
    rgb = arr[..., :3]
    alpha = arr[..., 3:4]

    if gamma_correct:
        rgb = srgb_to_linear(rgb)

    premultiplied = rgb * alpha
    resized_channels = [
        resize_float_channel(premultiplied[..., 0], size, resample),
        resize_float_channel(premultiplied[..., 1], size, resample),
        resize_float_channel(premultiplied[..., 2], size, resample),
        resize_float_channel(alpha[..., 0], size, resample),
    ]
    resized_alpha = np.clip(resized_channels[3], 0.0, 1.0)
    resized_premultiplied = np.stack(resized_channels[:3], axis=-1)

    with np.errstate(divide="ignore", invalid="ignore"):
        resized_rgb = np.where(
            resized_alpha[..., None] > 1e-6,
            resized_premultiplied / resized_alpha[..., None],
            0.0,
        )

    resized_rgb = np.clip(resized_rgb, 0.0, 1.0)
    if gamma_correct:
        resized_rgb = linear_to_srgb(resized_rgb)

    out = np.dstack([resized_rgb, resized_alpha])
    out = np.clip(out * 255.0 + 0.5, 0, 255).astype("uint8")
    return Image.fromarray(out, mode="RGBA")


def clear_fully_transparent_rgb(image: Image.Image) -> Image.Image:
    rgba = ensure_rgba(image)
    if np is not None:
        arr = np.asarray(rgba).copy()
        arr[arr[..., 3] == 0, 0:3] = 0
        return Image.fromarray(arr, mode="RGBA")

    r, g, b, a = rgba.split()
    mask = a.point(lambda v: 255 if v == 0 else 0)
    zero = Image.new("L", rgba.size, 0)
    r.paste(zero, mask=mask)
    g.paste(zero, mask=mask)
    b.paste(zero, mask=mask)
    return Image.merge("RGBA", (r, g, b, a))


def resize_rgba_canvas(image: Image.Image, size: tuple[int, int], *, pixel_art: bool = False) -> Image.Image:
    """Resize a complete transparent canvas with the shrinker's alpha-safe path.

    The assistant's crop tool uses this for its 256px examine image and again
    for the derived 32px sprite.  Keeping it here means those images use the
    same premultiplied-alpha, gamma-correct downscaling as animation frames.
    """

    working = clear_fully_transparent_rgb(image)
    if working.size == size:
        resized = working.copy()
    else:
        scale = min(size[0] / max(1, working.width), size[1] / max(1, working.height))
        resample = Image.Resampling.LANCZOS if scale < 1.0 else Image.Resampling.BICUBIC
        if np is not None:
            resized = resize_rgba_alpha_aware_numpy(working, size, resample, gamma_correct=True)
        else:
            resized = working.resize(size, resample=resample)

    if pixel_art:
        resized = ImageEnhance.Sharpness(resized).enhance(1.45)
        resized = quantize_rgba_colours(resized, 48)
        resized = cleanup_alpha(resized, low_cutoff=6, high_cutoff=250, hard_alpha=False)
    return clear_fully_transparent_rgb(resized)


def cleanup_alpha(image: Image.Image, low_cutoff: int, high_cutoff: int, hard_alpha: bool) -> Image.Image:
    rgba = ensure_rgba(image)
    r, g, b, a = rgba.split()
    low_cutoff = max(0, min(255, low_cutoff))
    high_cutoff = max(0, min(255, high_cutoff))
    if hard_alpha:
        a = a.point(lambda v: 255 if v >= 128 else 0)
    else:
        a = a.point(lambda v: 0 if v <= low_cutoff else (255 if v >= high_cutoff else v))
    return Image.merge("RGBA", (r, g, b, a))


def quantize_rgba_colours(image: Image.Image, colour_count: int) -> Image.Image:
    if colour_count <= 0:
        return ensure_rgba(image)
    colour_count = max(2, min(256, int(colour_count)))
    rgba = ensure_rgba(image)
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    try:
        quantized = rgb.quantize(colors=colour_count, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    except Exception:
        quantized = rgb.quantize(colors=colour_count, dither=Image.Dither.NONE)
    out = quantized.convert("RGBA")
    out.putalpha(alpha)
    return out


def posterize_rgba(image: Image.Image, bits: int) -> Image.Image:
    if bits <= 0:
        return ensure_rgba(image)
    bits = max(1, min(8, bits))
    rgba = ensure_rgba(image)
    alpha = rgba.getchannel("A")
    out = ImageOps.posterize(rgba.convert("RGB"), bits).convert("RGBA")
    out.putalpha(alpha)
    return out


def remove_small_alpha_specks(image: Image.Image, min_area: int) -> Image.Image:
    if min_area <= 1:
        return ensure_rgba(image)

    rgba = ensure_rgba(image)
    width, height = rgba.size
    alpha = rgba.getchannel("A")
    pixels = alpha.load()
    visited = bytearray(width * height)
    kill: list[tuple[int, int]] = []

    def idx(x: int, y: int) -> int:
        return y * width + x

    for y in range(height):
        for x in range(width):
            i = idx(x, y)
            if visited[i]:
                continue
            visited[i] = 1
            if pixels[x, y] == 0:
                continue
            component: list[tuple[int, int]] = []
            queue: deque[tuple[int, int]] = deque([(x, y)])
            while queue:
                cx, cy = queue.popleft()
                component.append((cx, cy))
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nx, ny = cx + dx, cy + dy
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    ni = idx(nx, ny)
                    if visited[ni]:
                        continue
                    visited[ni] = 1
                    if pixels[nx, ny] > 0:
                        queue.append((nx, ny))
            if len(component) < min_area:
                kill.extend(component)

    if not kill:
        return rgba
    alpha = alpha.copy()
    pixels = alpha.load()
    for x, y in kill:
        pixels[x, y] = 0
    rgba.putalpha(alpha)
    return clear_fully_transparent_rgb(rgba)


def fit_sprite_to_cell(sprite: Image.Image, cell_size: int, allow_upscale: bool, options: NormalizeOptions) -> Image.Image:
    sprite = ensure_rgba(sprite)
    sprite_w, sprite_h = sprite.size
    if sprite_w <= 0 or sprite_h <= 0:
        return sprite

    fit_padding = max(0, min(cell_size // 2 - 1 if cell_size > 2 else 0, options.fit_padding))
    target_box = max(1, cell_size - (fit_padding * 2))
    scale = min(target_box / sprite_w, target_box / sprite_h)

    if scale >= 1.0 and not allow_upscale:
        return sprite

    new_w = max(1, int(round(sprite_w * scale)))
    new_h = max(1, int(round(sprite_h * scale)))
    if (new_w, new_h) == (sprite_w, sprite_h):
        return sprite

    if options.quality_mode == "nearest":
        return sprite.resize((new_w, new_h), Image.Resampling.NEAREST)

    resample = choose_resample_filter(options, scale)
    working = clear_fully_transparent_rgb(sprite)

    blur_radius = resolve_preblur_radius(options.preblur, scale)
    if blur_radius > 0:
        working = working.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    if options.alpha_aware and np is not None:
        resized = resize_rgba_alpha_aware_numpy(
            working,
            size=(new_w, new_h),
            resample=resample,
            gamma_correct=options.gamma_correct,
        )
    else:
        if options.alpha_aware and np is None and options.verbose:
            warn("numpy is not installed; falling back to Pillow RGBA resize.")
        resized = working.resize((new_w, new_h), resample=resample)

    if options.sharpen != 1.0:
        resized = ImageEnhance.Sharpness(resized).enhance(options.sharpen)

    if options.quality_mode == "max":
        if options.posterize_bits > 0:
            resized = posterize_rgba(resized, options.posterize_bits)
        if options.quantize:
            resized = quantize_rgba_colours(resized, options.colour_count)

    if options.alpha_cleanup:
        resized = cleanup_alpha(
            resized,
            low_cutoff=options.alpha_low_cutoff,
            high_cutoff=options.alpha_high_cutoff,
            hard_alpha=options.hard_alpha,
        )

    if options.despeckle:
        resized = remove_small_alpha_specks(resized, options.despeckle_min_area)

    return clear_fully_transparent_rgb(resized)


def calculate_placement(
    sprite_size: tuple[int, int],
    cell_size: int,
    align_hor: str,
    align_ver: str,
    offset_x: int,
    offset_y: int,
) -> tuple[int, int]:
    sprite_w, sprite_h = sprite_size

    if align_hor == "left":
        x = 0
    elif align_hor == "center":
        x = (cell_size - sprite_w) // 2
    elif align_hor == "right":
        x = cell_size - sprite_w
    else:
        fail(f"Unknown horizontal alignment: {align_hor}")

    if align_ver == "top":
        y = 0
    elif align_ver == "center":
        y = (cell_size - sprite_h) // 2
    elif align_ver == "bottom":
        y = cell_size - sprite_h
    else:
        fail(f"Unknown vertical alignment: {align_ver}")

    return x + offset_x, y + offset_y


def build_output_strip(frames: list[CropFrame], cell_size: int, options: NormalizeOptions) -> Image.Image:
    output = Image.new("RGBA", (options.frames * cell_size, cell_size), (0, 0, 0, 0))

    for index in range(options.frames):
        if index >= len(frames):
            continue
        frame = frames[index]
        original = frame.image
        original_w, original_h = original.size
        frame.original_size = (original_w, original_h)

        sprite = fit_sprite_to_cell(original, cell_size, options.allow_upscale, options) if options.fit else original
        sprite_w, sprite_h = sprite.size
        frame.final_size = (sprite_w, sprite_h)
        frame.scaled = (sprite_w, sprite_h) != (original_w, original_h)
        frame.oversized = sprite_w > cell_size or sprite_h > cell_size

        if frame.scaled:
            log(f"Frame {index}: scaled {original_w}x{original_h} -> {sprite_w}x{sprite_h}", verbose=options.verbose)
        if frame.oversized:
            warn(
                f"Frame {index} is larger than cell: {sprite_w}x{sprite_h} into {cell_size}x{cell_size}. "
                "It will be clipped unless you use --fit, --cell auto, or a larger --cell."
            )

        local_x, local_y = calculate_placement(
            sprite.size,
            cell_size,
            options.align_hor,
            options.align_ver,
            options.offset_x,
            options.offset_y,
        )
        frame.placement = (index * cell_size + local_x, local_y)
        output.alpha_composite(sprite, dest=frame.placement)

    return clear_fully_transparent_rgb(output)


def save_debug_preview(strip: Image.Image, debug_path: Path, cell_size: int, frame_count: int) -> None:
    preview = strip.copy()
    draw = ImageDraw.Draw(preview)
    grid_colour = (255, 0, 0, 180)
    for frame_index in range(frame_count + 1):
        x = frame_index * cell_size
        draw.line((x, 0, x, cell_size), fill=grid_colour)
    draw.line((0, 0, frame_count * cell_size, 0), fill=grid_colour)
    draw.line((0, cell_size - 1, frame_count * cell_size, cell_size - 1), fill=grid_colour)
    preview.save(debug_path, optimize=True)


def print_debug_info(frames: list[CropFrame], frame_count: int) -> None:
    print("Frame debug info:")
    for index in range(frame_count):
        if index >= len(frames):
            print(f"  frame {index}: missing / transparent")
            continue
        frame = frames[index]
        original_size = frame.original_size or frame.image.size
        final_size = frame.final_size or frame.image.size
        print(
            f"  frame {index}: crop={frame.source_bbox}, "
            f"original_size={original_size[0]}x{original_size[1]}, "
            f"final_size={final_size[0]}x{final_size[1]}, "
            f"scaled={frame.scaled}, placement={frame.placement}, oversized={frame.oversized}"
        )


def validate_frame_count(frames: list[CropFrame], expected: int, allow_missing: bool) -> list[CropFrame]:
    detected = len(frames)
    if detected == expected:
        return frames
    message = f"Detected {detected} frame(s), expected {expected}."
    if not allow_missing:
        fail(message + " Use --allow-missing to continue anyway.")
    warn(message)
    if detected > expected:
        warn(f"Using the first {expected} frame(s) and ignoring the rest.")
        return frames[:expected]
    warn("Missing frames will be left transparent.")
    return frames


def validate_options(options: NormalizeOptions) -> None:
    if options.frames <= 0:
        fail("--frames must be greater than 0.")
    if not 0 <= options.alpha_threshold <= 255:
        fail("--alpha-threshold must be between 0 and 255.")
    if options.min_blob_area < 1:
        fail("--min-blob-area must be greater than 0.")
    if options.row_tolerance < 0:
        fail("--row-tolerance must be 0 or greater.")
    if options.quality_mode not in ("nearest", "clean", "max"):
        fail("quality_mode must be nearest, clean, or max.")
    if options.downscale_filter not in ("auto", *RESAMPLE_FILTERS.keys()):
        fail("downscale_filter must be auto, nearest, box, bilinear, bicubic, or lanczos.")
    if options.colour_count < 2 or options.colour_count > 256:
        fail("colour_count must be between 2 and 256.")
    if options.posterize_bits < 0 or options.posterize_bits > 8:
        fail("posterize_bits must be 0-8. Use 0 to disable.")
    if options.sharpen <= 0:
        fail("sharpen must be greater than 0. Use 1.0 for no sharpening.")
    if options.fit_padding < 0:
        fail("fit_padding must be 0 or greater.")
    if options.allow_upscale and not options.fit:
        warn("--allow-upscale has no effect unless --fit is also used.")


def normalize_file(input_path: Path, output_path: Path, options: NormalizeOptions) -> None:
    validate_options(options)
    if options.gamma_correct and np is None and options.verbose:
        warn("numpy is not installed; gamma-correct linear downscaling is unavailable.")
    if not input_path.exists():
        fail(f"Input file does not exist: {input_path}")

    image = ensure_rgba(Image.open(input_path))
    using_grid = options.source_cols is not None or options.source_rows is not None

    if using_grid:
        if options.source_cols is None or options.source_rows is None:
            fail("Grid mode requires both --source-cols and --source-rows.")
        frames = extract_grid_frames(image, options.source_cols, options.source_rows, options.alpha_threshold)
    else:
        frames = extract_blob_frames(image, options.alpha_threshold, options.row_tolerance, options.min_blob_area)

    if options.reverse:
        frames = list(reversed(frames))

    frames = validate_frame_count(frames, options.frames, options.allow_missing)
    cell_size = choose_auto_cell_size(frames) if options.cell == "auto" else int(options.cell)
    output = build_output_strip(frames, cell_size, options)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.save(output_path, optimize=True)

    if options.debug_preview:
        options.debug_preview.parent.mkdir(parents=True, exist_ok=True)
        save_debug_preview(output, options.debug_preview, cell_size, options.frames)
    if options.print_debug:
        print_debug_info(frames, options.frames)

    print(f"Processed: {input_path} -> {output_path}")
    log(f"Quality: {options.quality_mode}, preblur={options.preblur}, sharpen={options.sharpen}, colours={options.colour_count}", verbose=options.verbose)


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        fail(f"Batch config does not exist: {path}")
    except json.JSONDecodeError as exc:
        fail(f"Batch config is not valid JSON: {path}\n{exc}")
    if not isinstance(data, dict):
        fail("Batch config root must be a JSON object.")
    return data


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def select_profile_rules(profile: str, configured_rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    configured_by_suffix: dict[str, dict[str, Any]] = {}
    for rule in configured_rules:
        if not isinstance(rule, dict):
            fail("Each batch rule must be an object.")
        suffix = rule.get("suffix")
        if isinstance(suffix, str):
            configured_by_suffix[suffix] = rule

    selected: list[dict[str, Any]] = []
    for base_rule in build_profile_rules(profile):
        suffix = base_rule["suffix"]
        override = configured_by_suffix.get(suffix)
        merged = deep_merge(base_rule, override) if override is not None else dict(base_rule)
        # In a selected profile, every suffix is required because the safety
        # check has already decided this is exactly that kind of animation set.
        merged["required"] = True
        selected.append(merged)
    return selected


def require_int(config: dict[str, Any], key: str, context: str) -> int:
    value = config.get(key)
    if not isinstance(value, int):
        fail(f"{context}: '{key}' must be an integer.")
    return value


def require_float(config: dict[str, Any], key: str, context: str) -> float:
    value = config.get(key)
    if not isinstance(value, (int, float)):
        fail(f"{context}: '{key}' must be a number.")
    return float(value)


def require_bool(config: dict[str, Any], key: str, context: str) -> bool:
    value = config.get(key)
    if not isinstance(value, bool):
        fail(f"{context}: '{key}' must be true or false.")
    return value


def optional_int(config: dict[str, Any], key: str, context: str) -> Optional[int]:
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        fail(f"{context}: '{key}' must be an integer or null.")
    return value


def optional_path(value: Any, context: str) -> Optional[Path]:
    if value is None:
        return None
    if not isinstance(value, str):
        fail(f"{context}: debug_preview must be a string path or null.")
    return Path(value)


def optional_preblur(config: dict[str, Any], key: str, context: str) -> float | str:
    value = config.get(key, 0.15)
    if isinstance(value, str):
        if value != "auto":
            fail(f"{context}: '{key}' must be 'auto' or a number.")
        return value
    if isinstance(value, (int, float)):
        if float(value) < 0:
            fail(f"{context}: '{key}' must be 0 or greater, or 'auto'.")
        return float(value)
    fail(f"{context}: '{key}' must be 'auto' or a number.")


def require_choice(config: dict[str, Any], key: str, context: str, choices: tuple[str, ...]) -> str:
    value = config.get(key)
    if not isinstance(value, str) or value not in choices:
        fail(f"{context}: '{key}' must be one of: {', '.join(choices)}.")
    return value


def config_to_options(config: dict[str, Any], context: str) -> NormalizeOptions:
    cell = config.get("cell", 32)
    if isinstance(cell, str):
        cell = parse_cell(cell)
    elif isinstance(cell, int):
        if cell <= 0:
            fail(f"{context}: 'cell' must be greater than 0.")
    else:
        fail(f"{context}: 'cell' must be an integer or 'auto'.")

    align_hor = config.get("align_hor", "center")
    align_ver = config.get("align_ver", "bottom")
    if align_hor not in ("left", "center", "right"):
        fail(f"{context}: align_hor must be left, center, or right.")
    if align_ver not in ("top", "center", "bottom"):
        fail(f"{context}: align_ver must be top, center, or bottom.")

    quality_mode = require_choice(config, "quality_mode", context, ("nearest", "clean", "max"))
    downscale_filter = require_choice(config, "downscale_filter", context, ("auto", "nearest", "box", "bilinear", "bicubic", "lanczos"))
    quantize = require_bool(config, "quantize", context)
    posterize_bits = require_int(config, "posterize_bits", context)

    if quality_mode in ("clean", "nearest"):
        quantize = False
        posterize_bits = 0

    return NormalizeOptions(
        frames=require_int(config, "frames", context),
        cell=cell,
        source_cols=optional_int(config, "source_cols", context),
        source_rows=optional_int(config, "source_rows", context),
        blob_detect=require_bool(config, "blob_detect", context),
        fit=require_bool(config, "fit", context),
        allow_upscale=require_bool(config, "allow_upscale", context),
        align_hor=align_hor,
        align_ver=align_ver,
        offset_x=require_int(config, "offset_x", context),
        offset_y=require_int(config, "offset_y", context),
        alpha_threshold=require_int(config, "alpha_threshold", context),
        allow_missing=require_bool(config, "allow_missing", context),
        debug_preview=optional_path(config.get("debug_preview"), context),
        print_debug=require_bool(config, "print_debug", context),
        row_tolerance=require_int(config, "row_tolerance", context),
        min_blob_area=require_int(config, "min_blob_area", context),
        reverse=require_bool(config, "reverse", context),
        verbose=bool(config.get("verbose", False)),
        quality_mode=quality_mode,
        downscale_filter=downscale_filter,
        alpha_aware=require_bool(config, "alpha_aware", context),
        gamma_correct=require_bool(config, "gamma_correct", context),
        preblur=optional_preblur(config, "preblur", context),
        sharpen=require_float(config, "sharpen", context),
        quantize=quantize,
        colour_count=require_int(config, "colour_count", context),
        posterize_bits=posterize_bits,
        alpha_cleanup=require_bool(config, "alpha_cleanup", context),
        alpha_low_cutoff=require_int(config, "alpha_low_cutoff", context),
        alpha_high_cutoff=require_int(config, "alpha_high_cutoff", context),
        hard_alpha=require_bool(config, "hard_alpha", context),
        despeckle=require_bool(config, "despeckle", context),
        despeckle_min_area=require_int(config, "despeckle_min_area", context),
        fit_padding=require_int(config, "fit_padding", context),
    )


def normalize_uploaded_sheet(
    input_path: Path,
    output_path: Path,
    *,
    frames: int,
    cell: int = 32,
    config_overrides: Optional[dict[str, Any]] = None,
) -> Path:
    """Normalize one uploaded sheet without imposing a mob/player file count.

    ``frames`` may be any positive integer.  This is deliberately separate from
    the guarded no-argument batch workflow, whose exact 11/23 file checks remain
    useful when the script is run manually in an animation folder.
    """

    config = dict(DEFAULT_BATCH_CONFIG["defaults"])
    if config_overrides:
        config = deep_merge(config, config_overrides)
    config["frames"] = frames
    config["cell"] = cell
    options = config_to_options(config, context=f"uploaded sheet {input_path.name}")
    normalize_file(input_path, output_path, options)
    return output_path


def apply_cli_overrides_to_config(config: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    result = dict(config)
    override_fields = {
        "frames": args.frames,
        "cell": args.cell,
        "source_cols": args.source_cols,
        "source_rows": args.source_rows,
        "fit": True if args.fit else None,
        "allow_upscale": True if args.allow_upscale else None,
        "align_hor": args.align_hor,
        "align_ver": args.align_ver,
        "offset_x": args.offset_x if args.offset_x_set else None,
        "offset_y": args.offset_y if args.offset_y_set else None,
        "alpha_threshold": args.alpha_threshold if args.alpha_threshold_set else None,
        "allow_missing": True if args.allow_missing else None,
        "print_debug": True if args.print_debug else None,
        "row_tolerance": args.row_tolerance if args.row_tolerance_set else None,
        "min_blob_area": args.min_blob_area if args.min_blob_area_set else None,
        "reverse": True if args.reverse else None,
        "verbose": True if args.verbose else None,
        "quality_mode": args.quality_mode,
        "downscale_filter": args.downscale_filter,
        "preblur": args.preblur,
        "sharpen": args.sharpen if args.sharpen_set else None,
        "colour_count": args.colour_count if args.colour_count_set else None,
        "posterize_bits": args.posterize_bits if args.posterize_bits_set else None,
        "alpha_low_cutoff": args.alpha_low_cutoff if args.alpha_low_cutoff_set else None,
        "alpha_high_cutoff": args.alpha_high_cutoff if args.alpha_high_cutoff_set else None,
        "despeckle_min_area": args.despeckle_min_area if args.despeckle_min_area_set else None,
        "fit_padding": args.fit_padding if args.fit_padding_set else None,
    }
    for key, value in override_fields.items():
        if value is not None:
            result[key] = value

    if args.no_alpha_aware:
        result["alpha_aware"] = False
    if args.no_gamma_correct:
        result["gamma_correct"] = False
    if args.quantize:
        result["quantize"] = True
    if args.no_quantize:
        result["quantize"] = False
    if args.alpha_cleanup:
        result["alpha_cleanup"] = True
    if args.no_alpha_cleanup:
        result["alpha_cleanup"] = False
    if args.hard_alpha:
        result["hard_alpha"] = True
    if args.no_hard_alpha:
        result["hard_alpha"] = False
    if args.despeckle:
        result["despeckle"] = True
    if args.no_despeckle:
        result["despeckle"] = False
    if args.blob_detect:
        result["source_cols"] = None
        result["source_rows"] = None
        result["blob_detect"] = True

    if args.align:
        if args.align == "center":
            result["align_hor"] = "center"
            result["align_ver"] = "center"
        elif args.align == "bottom-center":
            result["align_hor"] = "center"
            result["align_ver"] = "bottom"
        elif args.align == "top-center":
            result["align_hor"] = "center"
            result["align_ver"] = "top"
        else:
            fail(f"Unknown alignment preset: {args.align}")

    return result


def match_rule_for_file(file_name: str, rules: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    matching = [rule for rule in rules if isinstance(rule.get("suffix"), str) and file_name.endswith(rule["suffix"])]
    if not matching:
        return None
    matching.sort(key=lambda rule: len(rule["suffix"]), reverse=True)
    return matching[0]


def load_batch_config_or_default(path: Optional[Path], *, verbose: bool = False) -> dict[str, Any]:
    config = DEFAULT_BATCH_CONFIG
    if path is not None:
        if path.exists():
            config = deep_merge(config, read_json(path))
            log(f"Using batch config: {path}", verbose=verbose)
        else:
            warn(f"Batch config not found at {path}; using built-in defaults.")
    return config


def run_batch(args: argparse.Namespace) -> None:
    input_dir = args.batch_in_dir
    output_dir = args.batch_out_dir
    if input_dir is None:
        fail("Batch mode requires --batch-in-dir.")
    if output_dir is None:
        fail("Batch mode requires --batch-out-dir.")
    if not input_dir.exists():
        fail(f"Batch input folder does not exist: {input_dir}")
    if not input_dir.is_dir():
        fail(f"Batch input path is not a folder: {input_dir}")

    batch_config = load_batch_config_or_default(args.batch_config, verbose=args.verbose)
    defaults = batch_config.get("defaults", {})
    rules = batch_config.get("rules", [])
    if not isinstance(defaults, dict):
        fail("Batch config 'defaults' must be an object.")
    if not isinstance(rules, list):
        fail("Batch config 'rules' must be an array.")

    batch_profile = getattr(args, "batch_profile", None)
    if batch_profile:
        rules = select_profile_rules(batch_profile, rules)
        log(f"Using built-in {batch_profile} batch profile rules.", verbose=args.verbose)

    recursive = bool(batch_config.get("recursive", False)) or bool(args.batch_recursive)
    if recursive:
        all_pngs = sorted(
            (path for path in input_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".png"),
            key=lambda path: str(path).lower(),
        )
    else:
        all_pngs = list_top_level_pngs(input_dir)
    if not all_pngs:
        warn(f"No PNG files found in: {input_dir}")
        return

    missing_suffixes: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            fail("Each batch rule must be an object.")
        suffix = rule.get("suffix")
        required = rule.get("required", True)
        if not isinstance(suffix, str) or not suffix.endswith(".png"):
            fail("Each batch rule must have a PNG 'suffix', e.g. '_death.png'.")
        if not isinstance(required, bool):
            fail(f"Batch config rule for {suffix}: 'required' must be true or false.")
        if required and not any(path.name.endswith(suffix) for path in all_pngs):
            missing_suffixes.append(suffix)

    extra_files: list[Path] = []
    jobs: list[tuple[Path, Path, NormalizeOptions]] = []
    for input_path in all_pngs:
        relative_path = input_path.relative_to(input_dir)
        output_path = output_dir / relative_path
        rule = match_rule_for_file(input_path.name, rules)
        if rule is None:
            merged_config = dict(defaults)
            extra_files.append(input_path)
        else:
            merged_config = deep_merge(defaults, {k: v for k, v in rule.items() if k not in ("suffix", "required")})

        merged_config = apply_cli_overrides_to_config(merged_config, args)
        options = config_to_options(merged_config, context=f"file {input_path.name}")
        if args.batch_debug_dir:
            debug_relative = relative_path.with_name(f"{relative_path.stem}_debug.png")
            options = NormalizeOptions(**{**options.__dict__, "debug_preview": args.batch_debug_dir / debug_relative})
        jobs.append((input_path, output_path, options))

    if missing_suffixes:
        warn("Expected animation file types missing from this folder:\n" + "\n".join(f"  {s}" for s in missing_suffixes))
    if extra_files:
        warn("Found extra PNGs not matching configured suffix rules; processing them with defaults:\n" + "\n".join(f"  {p.name}" for p in extra_files))

    log(f"Batch input: {input_dir}", verbose=args.verbose)
    log(f"Batch output: {output_dir}", verbose=args.verbose)
    log(f"Batch jobs: {len(jobs)}", verbose=args.verbose)

    processed = 0
    for input_path, output_path, options in jobs:
        normalize_file(input_path, output_path, options)
        processed += 1
    print(f"Done: processed {processed} PNG file(s).")


def resolve_alignment(args: argparse.Namespace) -> tuple[str, str]:
    align_hor = args.align_hor
    align_ver = args.align_ver
    if args.align:
        if args.align == "center":
            preset_hor, preset_ver = "center", "center"
        elif args.align == "bottom-center":
            preset_hor, preset_ver = "center", "bottom"
        elif args.align == "top-center":
            preset_hor, preset_ver = "center", "top"
        else:
            fail(f"Unknown alignment preset: {args.align}")
        align_hor = align_hor or preset_hor
        align_ver = align_ver or preset_ver
    return align_hor or "center", align_ver or "bottom"


def run_single(args: argparse.Namespace) -> None:
    if args.input is None:
        fail("Single-file mode requires an input PNG.")
    if args.out is None:
        fail("Single-file mode requires --out.")
    if args.frames is None:
        fail("Single-file mode requires --frames.")
    if args.cell is None:
        fail("Single-file mode requires --cell.")

    align_hor, align_ver = resolve_alignment(args)
    config = dict(DEFAULT_BATCH_CONFIG["defaults"])
    config.update(
        {
            "frames": args.frames,
            "cell": args.cell,
            "source_cols": args.source_cols,
            "source_rows": args.source_rows,
            "blob_detect": args.blob_detect,
            "fit": args.fit,
            "allow_upscale": args.allow_upscale,
            "align_hor": align_hor,
            "align_ver": align_ver,
            "offset_x": args.offset_x,
            "offset_y": args.offset_y,
            "alpha_threshold": args.alpha_threshold,
            "allow_missing": args.allow_missing,
            "debug_preview": str(args.debug_preview) if args.debug_preview else None,
            "print_debug": args.print_debug,
            "row_tolerance": args.row_tolerance,
            "min_blob_area": args.min_blob_area,
            "reverse": args.reverse,
            "verbose": args.verbose,
        }
    )
    config = apply_cli_overrides_to_config(config, args)
    options = config_to_options(config, context=f"file {args.input.name}")
    normalize_file(args.input, args.out, options)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalise generated pixel-art animation sheets into strict Godot-ready frame strips.")
    parser.add_argument("input", nargs="?", type=Path, help="Input transparent PNG. Omit this in batch mode.")
    parser.add_argument("--out", type=Path, default=None, help="Output transparent PNG strip. Used in single-file mode.")
    parser.add_argument("--frames", type=int, default=None, help="Expected output frame count.")
    parser.add_argument("--cell", "--cell-size", dest="cell", type=parse_cell, default=None, help="Output square cell size, e.g. 32, 48, 64, or 'auto'.")
    parser.add_argument("--source-cols", type=int, default=None, help="Source grid columns. Enables source grid mode when used with --source-rows.")
    parser.add_argument("--source-rows", type=int, default=None, help="Source grid rows. Enables source grid mode when used with --source-cols.")
    parser.add_argument("--blob-detect", action="store_true", help="Use blob detection mode. This is the default when no source grid is provided.")
    parser.add_argument("--fit", action="store_true", help="Scale each cropped frame to fit inside the output cell.")
    parser.add_argument("--allow-upscale", action="store_true", help="Allow --fit to scale small sprites up as well as large sprites down.")
    parser.add_argument("--reverse", action="store_true", help="Reverse the detected/extracted frame order. Does not mirror the images.")
    parser.add_argument("--align", choices=["center", "bottom-center", "top-center"], default=None, help="Convenience alignment preset.")
    parser.add_argument("--align-hor", choices=["left", "center", "right"], default=None, help="Horizontal alignment inside each output cell.")
    parser.add_argument("--align-ver", choices=["top", "center", "bottom"], default=None, help="Vertical alignment inside each output cell.")
    parser.add_argument("--offset-x", type=int, default=0, help="Manual x offset applied after alignment.")
    parser.add_argument("--offset-y", type=int, default=0, help="Manual y offset applied after alignment.")
    parser.add_argument("--alpha-threshold", type=int, default=0, help="Pixels with alpha <= threshold are treated as transparent.")
    parser.add_argument("--allow-missing", action="store_true", help="Do not fail if detected frame count differs from --frames.")
    parser.add_argument("--debug-preview", type=Path, default=None, help="Optional path for a debug preview PNG with grid lines.")
    parser.add_argument("--print-debug", action="store_true", help="Print crop bounds and placement information.")
    parser.add_argument("--row-tolerance", type=int, default=12, help="Blob sorting row tolerance in pixels.")
    parser.add_argument("--min-blob-area", type=int, default=64, help="Ignore detected blobs with fewer opaque pixels than this.")

    parser.add_argument("--quality-mode", choices=["nearest", "clean", "max"], default=None, help="Resize/cleanup preset.")
    parser.add_argument("--downscale-filter", choices=["auto", "nearest", "box", "bilinear", "bicubic", "lanczos"], default=None, help="Filter used when resizing.")
    parser.add_argument("--no-alpha-aware", action="store_true", help="Disable premultiplied-alpha resizing.")
    parser.add_argument("--no-gamma-correct", action="store_true", help="Disable linear-light/gamma-correct resizing.")
    parser.add_argument("--preblur", type=parse_preblur, default=None, help="Tiny blur before heavy downscale. Use 'auto' or a number.")
    parser.add_argument("--sharpen", type=float, default=1.45, help="Post-resize sharpness factor. 1.0 disables. Good range: 1.1-1.8.")
    parser.add_argument("--quantize", action="store_true", help="Enable colour quantization after resizing.")
    parser.add_argument("--no-quantize", action="store_true", help="Disable colour quantization after resizing.")
    parser.add_argument("--colour-count", "--color-count", dest="colour_count", type=int, default=48, help="Palette size for quantization. Try 24, 32, 48, or 64.")
    parser.add_argument("--posterize-bits", type=int, default=0, help="Optional RGB posterize bits. 0 disables.")
    parser.add_argument("--alpha-cleanup", action="store_true", help="Enable alpha cleanup after resizing.")
    parser.add_argument("--no-alpha-cleanup", action="store_true", help="Disable alpha cleanup after resizing.")
    parser.add_argument("--alpha-low-cutoff", type=int, default=6, help="Alpha <= this becomes transparent when alpha cleanup is enabled.")
    parser.add_argument("--alpha-high-cutoff", type=int, default=250, help="Alpha >= this becomes opaque when alpha cleanup is enabled.")
    parser.add_argument("--hard-alpha", action="store_true", help="Force alpha to 0 or 255 after resize.")
    parser.add_argument("--no-hard-alpha", action="store_true", help="Disable hard alpha.")
    parser.add_argument("--despeckle", action="store_true", help="Remove tiny isolated alpha components after resize.")
    parser.add_argument("--no-despeckle", action="store_true", help="Disable alpha despeckle.")
    parser.add_argument("--despeckle-min-area", type=int, default=2, help="Minimum final connected alpha component size when --despeckle is enabled.")
    parser.add_argument("--fit-padding", type=int, default=0, help="Reserve transparent padding inside each cell before fitting.")

    parser.add_argument("--verbose", "--extended-logging", dest="verbose", action="store_true", help="Enable extended logging.")
    parser.add_argument("--batch-config", type=Path, default=None, help="Batch JSON config path. Optional.")
    parser.add_argument("--batch-in-dir", type=Path, default=None, help="Batch input folder containing raw PNGs.")
    parser.add_argument("--batch-out-dir", type=Path, default=None, help="Batch output folder. Output filenames match input filenames.")
    parser.add_argument("--batch-debug-dir", type=Path, default=None, help="Optional folder for batch debug preview PNGs.")
    parser.add_argument("--batch-recursive", action="store_true", help="Search batch input folder recursively.")
    parser.add_argument("--batch-profile", choices=["mob", "player"], default=None, help="Use built-in mob or player batch rules.")
    return parser


def mark_explicit_flags(args: argparse.Namespace, argv: list[str]) -> None:
    args.offset_x_set = "--offset-x" in argv
    args.offset_y_set = "--offset-y" in argv
    args.alpha_threshold_set = "--alpha-threshold" in argv
    args.row_tolerance_set = "--row-tolerance" in argv
    args.min_blob_area_set = "--min-blob-area" in argv
    args.sharpen_set = "--sharpen" in argv
    args.colour_count_set = "--colour-count" in argv or "--color-count" in argv
    args.posterize_bits_set = "--posterize-bits" in argv
    args.alpha_low_cutoff_set = "--alpha-low-cutoff" in argv
    args.alpha_high_cutoff_set = "--alpha-high-cutoff" in argv
    args.despeckle_min_area_set = "--despeckle-min-area" in argv
    args.fit_padding_set = "--fit-padding" in argv


def main() -> None:
    argv = sys.argv[1:]
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    mark_explicit_flags(args, argv)

    if not argv:
        cwd = Path(".")
        cfg = cwd / "mob_anim_batch.json"
        args.batch_config = cfg if cfg.exists() else None
        args.batch_in_dir = cwd
        args.batch_out_dir = cwd / "output"
        args.batch_profile = None
        run_batch(args)
        return

    if args.batch_config or args.batch_in_dir or args.batch_out_dir:
        if args.batch_in_dir is None:
            args.batch_in_dir = Path(".")
        if args.batch_out_dir is None:
            args.batch_out_dir = Path("output")
        run_batch(args)
    else:
        run_single(args)


if __name__ == "__main__":
    try:
        main()
    except NormalizationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
