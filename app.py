#!/usr/bin/env python3
"""Depth Hammer Spritesheet Assistant local server.

The browser is only the interface.  All filesystem access, validation and image
normalisation happen here, bound to 127.0.0.1 and protected by a per-run token.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import sys
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote, urlparse

from PIL import Image, UnidentifiedImageError

from normalize_anim import (
    DEFAULT_BATCH_CONFIG,
    NormalizationError,
    normalize_uploaded_sheet,
    resize_rgba_canvas,
)


APP_VERSION = "1.3.1"
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
CONFIG_DIR = APP_DIR / "config"
APP_CONFIG_PATH = APP_DIR / "app_config.json"
PROFILE_PATH = CONFIG_DIR / "sprite_profiles.json"
TEMPLATE_PATH = CONFIG_DIR / "prompt_templates.json"
DEFAULT_TEMPLATE_PATH = CONFIG_DIR / "prompt_templates.defaults.json"
NORMALIZER_CONFIG_PATH = APP_DIR / "mob_anim_batch.json"
DEFAULT_WORKSPACE = APP_DIR / "workspace"
SOURCE_ASSETS_DIR = APP_DIR / "source_assets"
LIBRARY_METADATA_FILENAME = "sprite_library_metadata.json"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
SAFE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
FOOTPRINT_SIZES = {
    "1x1": 32,
    "2x2": 64,
}

Image.MAX_IMAGE_PIXELS = 80_000_000


class AppError(RuntimeError):
    def __init__(self, message: str, status: int = 400, *, details: Any = None):
        super().__init__(message)
        self.status = status
        self.details = details


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AppError(f"Required configuration file is missing: {path.name}", 500) from exc
    except json.JSONDecodeError as exc:
        raise AppError(f"Invalid JSON in {path.name}: {exc}", 500) from exc
    if not isinstance(data, dict):
        raise AppError(f"{path.name} must contain a JSON object.", 500)
    return data


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate_slug(value: str) -> str:
    value = value.strip()
    if not SAFE_SLUG_RE.fullmatch(value):
        raise AppError("Folder name must use 1–64 lowercase letters, numbers, hyphens or underscores.")
    if value.casefold() in WINDOWS_RESERVED:
        raise AppError(f"'{value}' is a reserved Windows filename.")
    return value


def validate_identity(name: Any, description: Any, *, require_name: bool = False) -> tuple[str, str]:
    if not isinstance(name, str) or not isinstance(description, str):
        raise AppError("Name and description must be text.")
    name = name.strip()
    description = description.strip()
    if require_name and not name:
        raise AppError("Name cannot be empty.")
    if len(name) > 120:
        raise AppError("Name must be 120 characters or fewer.")
    if len(description) > 5000:
        raise AppError("Description must be 5,000 characters or fewer.")
    return name, description


def validate_draft_id(value: str) -> str:
    if not SAFE_ID_RE.fullmatch(value):
        raise AppError("Invalid draft identifier.")
    return value


def validate_footprint(value: Any) -> str:
    if not isinstance(value, str):
        raise AppError("Footprint must be 1x1 or 2x2.")
    normalized = value.strip().lower().replace("×", "x")
    if normalized not in FOOTPRINT_SIZES:
        raise AppError("Footprint must be 1x1 or 2x2.")
    return normalized


def footprint_cell_size(value: Any) -> int:
    return FOOTPRINT_SIZES[validate_footprint(value)]


def resolved_crop_output_size(spec: dict[str, Any], cell_size: int) -> tuple[int, int]:
    size = spec.get("size", [cell_size, cell_size])
    if bool(spec.get("footprint_scaled", False)):
        return cell_size, cell_size
    return int(size[0]), int(size[1])


def unique_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve()).casefold()
        if key not in seen:
            seen.add(key)
            result.append(path.resolve())
    return result


def discover_sprites_root(configured: str = "") -> tuple[Path, bool, str]:
    if configured.strip():
        expanded = Path(os.path.expandvars(os.path.expanduser(configured.strip())))
        path = expanded if expanded.is_absolute() else APP_DIR / expanded
        resolved = path.resolve()
        valid = resolved.is_dir() and (resolved / "player").is_dir() and (resolved / "monsters").is_dir()
        return resolved, valid, "configured"

    candidates = [
        # Recommended layout:
        # C:\code\depth-hammer-spritesheet-assistant
        # C:\code\depth-hammer\assets\entity_sprites\default
        APP_DIR.parent / "depth-hammer" / "assets" / "entity_sprites" / "default",
        APP_DIR / "entity_sprites" / "default",
        APP_DIR.parent / "entity_sprites" / "default",
        APP_DIR.parent.parent / "assets" / "entity_sprites" / "default",
        Path.cwd() / "entity_sprites" / "default",
        Path.cwd() / "assets" / "entity_sprites" / "default",
    ]
    for ancestor in [APP_DIR, *APP_DIR.parents]:
        candidates.append(ancestor / "assets" / "entity_sprites" / "default")
        candidates.append(ancestor / "entity_sprites" / "default")

    candidates = unique_paths(candidates)
    for candidate in candidates:
        if candidate.is_dir() and ((candidate / "player").is_dir() or (candidate / "monsters").is_dir()):
            return candidate, True, "auto-detected"
    return candidates[0], False, "not found"


class ProfileCatalog:
    def __init__(self, path: Path):
        self.path = path
        data = read_json(path)
        profiles = data.get("profiles")
        if not isinstance(profiles, dict) or not profiles:
            raise AppError("sprite_profiles.json has no profiles.", 500)
        self.data = data
        self.profiles: dict[str, dict[str, Any]] = profiles
        self._validate()

    def _validate(self) -> None:
        for profile_key, profile in self.profiles.items():
            if not isinstance(profile, dict) or not isinstance(profile.get("groups"), list):
                raise AppError(f"Profile '{profile_key}' has invalid groups.", 500)
            group_ids: set[str] = set()
            suffixes: set[str] = set()
            for group in profile["groups"]:
                group_id = group.get("id")
                if not isinstance(group_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", group_id):
                    raise AppError(f"Profile '{profile_key}' contains an invalid group id.", 500)
                if group_id in group_ids:
                    raise AppError(f"Profile '{profile_key}' repeats group '{group_id}'.", 500)
                group_ids.add(group_id)
                source_frames = group.get("source_frames")
                if not isinstance(source_frames, int) or source_frames < 1:
                    raise AppError(f"Group '{group_id}' has an invalid source frame count.", 500)
                grid_hint = group.get("grid_hint")
                if grid_hint is not None and (
                    not isinstance(grid_hint, list)
                    or len(grid_hint) != 2
                    or not all(isinstance(value, int) and value > 0 for value in grid_hint)
                    or grid_hint[0] * grid_hint[1] != source_frames
                ):
                    raise AppError(f"Group '{group_id}' has an invalid grid hint.", 500)
                if not isinstance(group.get("prefer_grid_reading_order", False), bool):
                    raise AppError(f"Group '{group_id}' has an invalid grid-reading preference.", 500)
                outputs = group.get("outputs")
                if not isinstance(outputs, list) or not outputs:
                    raise AppError(f"Group '{group_id}' has no outputs.", 500)
                for output in outputs:
                    suffix = output.get("suffix")
                    frames = output.get("frames")
                    start = output.get("start")
                    if not isinstance(suffix, str) or not suffix.endswith(".png"):
                        raise AppError(f"Group '{group_id}' has an invalid PNG suffix.", 500)
                    if suffix in suffixes:
                        raise AppError(f"Profile '{profile_key}' repeats suffix '{suffix}'.", 500)
                    suffixes.add(suffix)
                    if not isinstance(frames, int) or frames < 1 or not isinstance(start, int) or start < 0:
                        raise AppError(f"Group '{group_id}' has invalid output frames.", 500)
                    if start + frames > source_frames:
                        raise AppError(f"Group '{group_id}' output exceeds its source frame count.", 500)
                    size = output.get("size")
                    if group.get("mode") == "crop_pair" and (
                        not isinstance(size, list)
                        or len(size) != 2
                        or not all(isinstance(value, int) and value > 0 for value in size)
                    ):
                        raise AppError(f"Crop output '{suffix}' needs a valid size.", 500)

    def public_data(self) -> dict[str, Any]:
        return self.data

    def profile(self, key: str) -> dict[str, Any]:
        try:
            return self.profiles[key]
        except KeyError as exc:
            raise AppError(f"Unknown profile: {key}") from exc

    def group(self, profile_key: str, group_id: str) -> dict[str, Any]:
        for group in self.profile(profile_key)["groups"]:
            if group["id"] == group_id:
                return group
        raise AppError(f"Unknown {profile_key} upload group: {group_id}")

    def all_output_specs(self, profile_key: str) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for group in self.profile(profile_key)["groups"]:
            for output in group["outputs"]:
                specs.append({**output, "group_id": group["id"], "mode": group["mode"]})
        return specs


def load_normalizer_defaults() -> dict[str, Any]:
    data = read_json(NORMALIZER_CONFIG_PATH)
    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        raise AppError("mob_anim_batch.json defaults must be an object.", 500)
    built_in_defaults = DEFAULT_BATCH_CONFIG.get("defaults", {})
    if not isinstance(built_in_defaults, dict):
        raise AppError("The normalizer's built-in defaults are invalid.", 500)
    # Uploaded combined sheets must be processed in reading order. Per-output
    # reversal is handled after splitting.
    return {
        **built_in_defaults,
        **defaults,
        "reverse": False,
        "allow_missing": False,
        "fit": True,
        "cell": 32,
    }


def png_dimensions(path: Path) -> tuple[int, int, str]:
    try:
        with Image.open(path) as image:
            return image.width, image.height, image.mode
    except (OSError, UnidentifiedImageError) as exc:
        raise AppError(f"Unreadable PNG: {path.name}") from exc


def has_useful_alpha(image: Image.Image) -> bool:
    alpha = image.convert("RGBA").getchannel("A")
    low, high = alpha.getextrema()
    return low < 255 and high > 0


def reverse_strip_frames(strip: Image.Image, frames: int, cell: int = 32) -> Image.Image:
    reversed_strip = Image.new("RGBA", (frames * cell, cell), (0, 0, 0, 0))
    for target_index in range(frames):
        source_index = frames - target_index - 1
        frame = strip.crop((source_index * cell, 0, (source_index + 1) * cell, cell))
        reversed_strip.alpha_composite(frame, (target_index * cell, 0))
    return reversed_strip


def validate_finished_png(
    path: Path,
    spec: dict[str, Any],
    group: dict[str, Any],
    cell_size: int = 32,
) -> list[str]:
    warnings: list[str] = []
    with Image.open(path) as source:
        image = source.convert("RGBA")
    frames = int(spec["frames"])
    if group["mode"] == "crop_pair":
        expected = resolved_crop_output_size(spec, cell_size)
        if image.size != expected:
            raise AppError(f"{path.name} is {image.width}x{image.height}; expected {expected[0]}x{expected[1]}.")
        if image.getchannel("A").getbbox() is None:
            raise AppError(f"{path.name} is completely transparent.")
    elif group["mode"] == "passthrough":
        minimum = group.get("minimum_size", [1, 1])
        if image.width < int(minimum[0]) or image.height < int(minimum[1]):
            raise AppError(
                f"{path.name} is {image.width}x{image.height}; it must be at least {minimum[0]}x{minimum[1]}."
            )
    else:
        expected = (frames * cell_size, cell_size)
        if image.size != expected:
            raise AppError(f"{path.name} is {image.width}x{image.height}; expected {expected[0]}x{expected[1]}.")
        hashes: list[bytes] = []
        for index in range(frames):
            frame = image.crop(
                (index * cell_size, 0, (index + 1) * cell_size, cell_size)
            )
            if frame.getchannel("A").getbbox() is None:
                raise AppError(f"{path.name} has an empty frame at position {index + 1}.")
            hashes.append(frame.tobytes())
        if len(set(hashes)) < len(hashes):
            warnings.append(f"{path.name} contains identical frames; visually check that this is intentional.")
    if not has_useful_alpha(image):
        if group["mode"] == "crop_pair":
            warnings.append(f"{path.name} is fully opaque; consider using source artwork with a transparent background.")
        else:
            raise AppError(f"{path.name} needs a genuine transparent background.")
    return warnings


@dataclass
class DraftValidation:
    files: list[tuple[Path, str]]
    source_files: list[tuple[Path, str]]
    warnings: list[str]


class DraftManager:
    def __init__(
        self,
        workspace: Path,
        catalog: ProfileCatalog,
        source_assets_root: Optional[Path] = None,
    ):
        self.workspace = workspace.resolve()
        self.drafts_root = self.workspace / "drafts"
        self.source_assets_root = (
            source_assets_root.resolve()
            if source_assets_root is not None
            else self.workspace.parent / "source_assets"
        )
        self.catalog = catalog
        self.normalizer_defaults = load_normalizer_defaults()
        self._lock = threading.RLock()
        self.drafts_root.mkdir(parents=True, exist_ok=True)

    def draft_dir(self, draft_id: str) -> Path:
        return self.drafts_root / validate_draft_id(draft_id)

    def metadata_path(self, draft_id: str) -> Path:
        return self.draft_dir(draft_id) / "draft.json"

    def load(self, draft_id: str, profile_key: Optional[str] = None) -> dict[str, Any]:
        path = self.metadata_path(draft_id)
        if path.exists():
            data = read_json(path)
            if profile_key and data.get("profile") != profile_key:
                raise AppError("This draft belongs to a different sprite profile.")
            return data
        if profile_key is None:
            raise AppError("Draft not found.", 404)
        self.catalog.profile(profile_key)
        return {
            "version": 2,
            "draft_id": draft_id,
            "profile": profile_key,
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
            "footprint": "1x1",
            "identity": {"name": "", "slug": "", "description": ""},
            "groups": {},
        }

    def save(self, draft_id: str, metadata: dict[str, Any]) -> None:
        metadata["updated_at"] = int(time.time())
        atomic_write_json(self.metadata_path(draft_id), metadata)

    def reset(self, draft_id: str) -> None:
        path = self.draft_dir(draft_id)
        if path.exists() and is_within(path, self.drafts_root):
            shutil.rmtree(path)

    def save_identity(self, draft_id: str, profile_key: str, name: Any, slug: Any, description: Any) -> dict[str, Any]:
        name, description = validate_identity(name, description)
        if not isinstance(slug, str):
            raise AppError("Folder name must be text.")
        slug = slug.strip()
        if slug:
            slug = validate_slug(slug)
        with self._lock:
            metadata = self.load(draft_id, profile_key)
            metadata["identity"] = {"name": name, "slug": slug, "description": description}
            self.save(draft_id, metadata)
            return self.status(draft_id, profile_key)

    def set_footprint(self, draft_id: str, profile_key: str, footprint: Any) -> dict[str, Any]:
        footprint = validate_footprint(footprint)
        with self._lock:
            metadata = self.load(draft_id, profile_key)
            previous = validate_footprint(metadata.get("footprint", "1x1"))
            metadata["footprint"] = footprint
            metadata["version"] = 2
            self.save(draft_id, metadata)
            if previous == footprint:
                return self.status(draft_id, profile_key)

            uploaded_groups = [
                group["id"]
                for group in self.catalog.profile(profile_key)["groups"]
                if metadata.get("groups", {}).get(group["id"], {}).get("raw_name")
            ]
            # Each group records its own processing error, so one unusual sheet
            # cannot prevent the remaining uploads from being resized.
            for group_id in uploaded_groups:
                try:
                    self._process_existing(draft_id, profile_key, str(group_id))
                except AppError:
                    continue
            return self.status(draft_id, profile_key)

    def _inspect_upload(self, data: bytes, *, require_png: bool = True) -> tuple[int, int]:
        if require_png and not data.startswith(PNG_SIGNATURE):
            raise AppError("The uploaded file is not a PNG.")
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise AppError("The uploaded image is invalid or too large.") from exc
        if width < 1 or height < 1 or width * height > 80_000_000:
            raise AppError("The uploaded image has unsafe dimensions.")
        return width, height

    @staticmethod
    def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise AppError(f"{label} must be a number.") from exc
        if not math.isfinite(result) or result < minimum or result > maximum:
            raise AppError(f"{label} must be between {minimum:g} and {maximum:g}.")
        return result

    @staticmethod
    def _boolean(value: Any, label: str) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        if isinstance(value, int) and value in {0, 1}:
            return bool(value)
        raise AppError(f"{label} must be on or off.")

    def _quality_settings(self, supplied: Optional[dict[str, Any]]) -> dict[str, Any]:
        supplied = supplied or {}
        nested = supplied.get("quality")
        if isinstance(nested, dict):
            supplied = {**supplied, **nested}
        defaults = self.normalizer_defaults

        filter_name = str(supplied.get("downscale_filter", defaults.get("downscale_filter", "auto"))).strip().lower()
        valid_filters = {"auto", "nearest", "box", "bilinear", "bicubic", "lanczos"}
        if filter_name not in valid_filters:
            raise AppError("Downscale filter must be automatic, nearest, box, bilinear, bicubic or Lanczos.")

        try:
            colour_count = int(supplied.get("colour_count", defaults.get("colour_count", 48)))
        except (TypeError, ValueError) as exc:
            raise AppError("Palette colours must be a whole number.") from exc
        if colour_count < 2 or colour_count > 256:
            raise AppError("Palette colours must be between 2 and 256.")

        default_preblur = defaults.get("preblur", 0.15)
        if isinstance(default_preblur, str):
            default_preblur = 0.15
        try:
            alpha_low_cutoff = int(defaults.get("alpha_low_cutoff", 6))
            alpha_high_cutoff = int(defaults.get("alpha_high_cutoff", 250))
            despeckle_min_area = int(defaults.get("despeckle_min_area", 2))
            posterize_bits = int(defaults.get("posterize_bits", 0))
        except (TypeError, ValueError) as exc:
            raise AppError("The normalizer quality defaults are invalid.", 500) from exc

        return {
            "quality_mode": "max",
            "downscale_filter": filter_name,
            "alpha_aware": self._boolean(
                supplied.get("alpha_aware", defaults.get("alpha_aware", True)),
                "Alpha-aware resizing",
            ),
            "gamma_correct": self._boolean(
                supplied.get("gamma_correct", defaults.get("gamma_correct", True)),
                "Gamma-correct resizing",
            ),
            "preblur": self._number(
                supplied.get("preblur", default_preblur),
                "Pre-shrink blur",
                0.0,
                2.0,
            ),
            "sharpen": self._number(
                supplied.get("sharpen", defaults.get("sharpen", 1.45)),
                "Sharpness",
                0.0,
                4.0,
            ),
            "quantize": self._boolean(
                supplied.get("quantize", defaults.get("quantize", True)),
                "Palette reduction",
            ),
            "colour_count": colour_count,
            "posterize_bits": max(0, min(8, posterize_bits)),
            "alpha_cleanup": self._boolean(
                supplied.get("alpha_cleanup", defaults.get("alpha_cleanup", True)),
                "Alpha cleanup",
            ),
            "alpha_low_cutoff": max(0, min(255, alpha_low_cutoff)),
            "alpha_high_cutoff": max(0, min(255, alpha_high_cutoff)),
            "hard_alpha": self._boolean(
                supplied.get("hard_alpha", defaults.get("hard_alpha", False)),
                "Hard alpha edges",
            ),
            "despeckle": self._boolean(
                supplied.get("despeckle", defaults.get("despeckle", False)),
                "Despeckle",
            ),
            "despeckle_min_area": max(1, min(64, despeckle_min_area)),
        }

    def _detection_settings(
        self,
        group: dict[str, Any],
        supplied: Optional[dict[str, Any]],
        source_width: int,
        source_height: int,
    ) -> dict[str, Any]:
        supplied = supplied or {}
        mode = str(supplied.get("mode", "auto")).strip().lower()
        if mode not in {"auto", "blobs", "grid"}:
            raise AppError("Detection mode must be automatic, connected sprites or fixed grid.")
        hint = group.get("grid_hint")
        if isinstance(hint, list) and len(hint) == 2:
            default_cols, default_rows = int(hint[0]), int(hint[1])
        else:
            frame_count = int(group["source_frames"])
            candidates = [(cols, frame_count // cols) for cols in range(1, frame_count + 1) if frame_count % cols == 0]
            target_ratio = source_width / max(1, source_height)
            default_cols, default_rows = min(candidates, key=lambda pair: abs((pair[0] / pair[1]) - target_ratio))

        def integer(key: str, default: int, minimum: int, maximum: int, label: str) -> int:
            try:
                value = int(supplied.get(key, default))
            except (TypeError, ValueError) as exc:
                raise AppError(f"{label} must be a whole number.") from exc
            if value < minimum or value > maximum:
                raise AppError(f"{label} must be between {minimum} and {maximum}.")
            return value

        columns = integer("columns", default_cols, 1, 32, "Grid columns")
        rows = integer("rows", default_rows, 1, 32, "Grid rows")
        if columns * rows != int(group["source_frames"]):
            raise AppError(
                f"The grid must contain exactly {group['source_frames']} cells; {columns}×{rows} contains {columns * rows}."
            )
        return {
            "mode": mode,
            "columns": columns,
            "rows": rows,
            "alpha_threshold": integer(
                "alpha_threshold", int(self.normalizer_defaults.get("alpha_threshold", 0)), 0, 254, "Alpha threshold"
            ),
            "min_blob_area": integer(
                "min_blob_area", int(self.normalizer_defaults.get("min_blob_area", 64)), 1, 100_000, "Minimum component area"
            ),
            "row_tolerance": integer(
                "row_tolerance", int(self.normalizer_defaults.get("row_tolerance", 12)), 0, 500, "Row tolerance"
            ),
        }

    def _crop_settings(self, supplied: Optional[dict[str, Any]]) -> dict[str, float]:
        supplied = supplied or {}
        nested = supplied.get("crop")
        if isinstance(nested, dict):
            supplied = {**supplied, **nested}
        return {
            "zoom": self._number(supplied.get("zoom", 1.0), "Scale", 0.1, 6.0),
            "pan_x": self._number(supplied.get("pan_x", 0.0), "Horizontal position", -2.0, 2.0),
            "pan_y": self._number(supplied.get("pan_y", 0.0), "Vertical position", -2.0, 2.0),
        }

    @staticmethod
    def _clear_previous_outputs(saved: dict[str, Any], output_dir: Path) -> None:
        for record_key in ("outputs", "original_previews"):
            for old_output in saved.get(record_key, []):
                candidate = output_dir / str(old_output.get("stored_name", ""))
                if candidate.is_file() and is_within(candidate, output_dir):
                    candidate.unlink()

    def process_upload(
        self,
        draft_id: str,
        profile_key: str,
        group_id: str,
        data: bytes,
        processing: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        group = self.catalog.group(profile_key, group_id)
        crop_mode = group["mode"] == "crop_pair"
        source_width, source_height = self._inspect_upload(data, require_png=not crop_mode)
        with self._lock:
            metadata = self.load(draft_id, profile_key)
            cell_size = footprint_cell_size(metadata.get("footprint", "1x1"))
            draft_dir = self.draft_dir(draft_id)
            raw_dir = draft_dir / "raw"
            output_dir = draft_dir / "processed"
            raw_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)
            raw_path = raw_dir / f"{group_id}.png"
            if crop_mode:
                with Image.open(io.BytesIO(data)) as source:
                    converted = source.convert("RGBA")
                    source_bbox = converted.getchannel("A").getbbox()
                    stream = io.BytesIO()
                    converted.save(stream, format="PNG", optimize=True)
                atomic_write_bytes(raw_path, stream.getvalue())
            else:
                atomic_write_bytes(raw_path, data)

            old_group = metadata.setdefault("groups", {}).get(group_id, {})
            self._clear_previous_outputs(old_group, output_dir)
            saved: dict[str, Any] = {
                "ready": False,
                "source_width": source_width,
                "source_height": source_height,
                "raw_name": raw_path.name,
                "raw_version": uuid.uuid4().hex,
                "cell_size": cell_size,
                "outputs": [],
                "warnings": [],
            }
            if crop_mode:
                saved["crop"] = self._crop_settings(processing)
                saved["crop_bbox"] = list(source_bbox) if source_bbox is not None else [0, 0, source_width, source_height]
            else:
                saved["settings"] = self._detection_settings(group, processing, source_width, source_height)
            saved["quality"] = self._quality_settings(processing)
            metadata["groups"][group_id] = saved
            self.save(draft_id, metadata)
            return self._process_existing(draft_id, profile_key, group_id)

    def reprocess(
        self,
        draft_id: str,
        profile_key: str,
        group_id: str,
        processing: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        group = self.catalog.group(profile_key, group_id)
        with self._lock:
            metadata = self.load(draft_id, profile_key)
            saved = metadata.get("groups", {}).get(group_id)
            if not saved or not saved.get("raw_name"):
                raise AppError("Upload an image for this group before reprocessing it.")
            processing = processing or {}
            if group["mode"] == "crop_pair":
                saved["crop"] = self._crop_settings({**saved.get("crop", {}), **processing})
            else:
                saved["settings"] = self._detection_settings(
                    group,
                    {**saved.get("settings", {}), **processing},
                    int(saved.get("source_width", 1)),
                    int(saved.get("source_height", 1)),
                )
            saved["quality"] = self._quality_settings({**saved.get("quality", {}), **processing})
            self.save(draft_id, metadata)
            return self._process_existing(draft_id, profile_key, group_id)

    def _render_crop_pair(
        self,
        raw_path: Path,
        output_dir: Path,
        group_id: str,
        group: dict[str, Any],
        crop: dict[str, Any],
        quality: dict[str, Any],
        cell_size: int,
    ) -> tuple[list[dict[str, Any]], list[str], list[int]]:
        with Image.open(raw_path) as source_file:
            source = source_file.convert("RGBA")
        bbox = source.getchannel("A").getbbox()
        if bbox is None:
            raise AppError("The source image is completely transparent.")
        subject = source.crop(bbox)
        large_spec = max(group["outputs"], key=lambda item: int(item["size"][0]) * int(item["size"][1]))
        large_size = (int(large_spec["size"][0]), int(large_spec["size"][1]))
        zoom = float(crop["zoom"])
        scale = 0.9 * min(large_size[0] / subject.width, large_size[1] / subject.height) * zoom
        scaled_size = (max(1, round(subject.width * scale)), max(1, round(subject.height * scale)))
        scaled = resize_rgba_canvas(subject, scaled_size)
        canvas = Image.new("RGBA", large_size, (0, 0, 0, 0))
        x = round((large_size[0] - scaled.width) / 2 + float(crop["pan_x"]) * large_size[0] / 2)
        y = round((large_size[1] - scaled.height) / 2 + float(crop["pan_y"]) * large_size[1] / 2)
        canvas.alpha_composite(scaled, dest=(x, y))

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        for index, spec in enumerate(group["outputs"]):
            size = resolved_crop_output_size(spec, cell_size)
            image = (
                canvas.copy()
                if size == large_size
                else resize_rgba_canvas(canvas, size, pixel_art=True, settings=quality)
            )
            stored_name = f"{group_id}__{index}.png"
            output_path = output_dir / stored_name
            image.save(output_path, optimize=True)
            warnings.extend(validate_finished_png(output_path, spec, group, cell_size))
            records.append(self._record_output(stored_name, output_path, spec))
        return records, warnings, list(bbox)

    @staticmethod
    def _strip_has_all_frames(path: Path, frame_count: int, cell: int = 32) -> bool:
        if not path.is_file():
            return False
        with Image.open(path) as source:
            alpha = source.convert("RGBA").getchannel("A")
        if alpha.size != (frame_count * cell, cell):
            return False
        return all(
            alpha.crop((index * cell, 0, (index + 1) * cell, cell)).getbbox() is not None
            for index in range(frame_count)
        )

    @staticmethod
    def _rank_grid_candidates(
        raw_path: Path,
        frame_count: int,
        alpha_threshold: int,
        preferred: tuple[int, int],
    ) -> list[tuple[int, int]]:
        """Rank factor-pair grids without changing fixed-grid behaviour.

        Automatic fallback should cope with both 4x2 and 8x1 eight-frame
        sheets.  A good grid has artwork in every cell, cuts through little or
        no artwork at its internal boundaries, and produces reasonably shaped
        cells.  The configured profile hint is retained as a small tie-breaker.
        """

        with Image.open(raw_path) as source:
            alpha = source.convert("RGBA").getchannel("A")
        maximum_dimension = 1024
        if max(alpha.size) > maximum_dimension:
            scale = maximum_dimension / max(alpha.size)
            alpha = alpha.resize(
                (max(1, round(alpha.width * scale)), max(1, round(alpha.height * scale))),
                Image.Resampling.NEAREST,
            )
        if alpha_threshold > 0:
            alpha = alpha.point(lambda value: 255 if value > alpha_threshold else 0)

        candidates: list[tuple[float, int, int]] = []
        for columns in range(1, frame_count + 1):
            if frame_count % columns:
                continue
            rows = frame_count // columns
            cell_width = alpha.width / columns
            cell_height = alpha.height / rows
            empty_cells = 0
            for row in range(rows):
                for column in range(columns):
                    box = (
                        round(column * alpha.width / columns),
                        round(row * alpha.height / rows),
                        round((column + 1) * alpha.width / columns),
                        round((row + 1) * alpha.height / rows),
                    )
                    if alpha.crop(box).getbbox() is None:
                        empty_cells += 1

            boundary_opaque = 0
            boundary_area = 0
            band = max(1, round(min(cell_width, cell_height) * 0.01))
            for column in range(1, columns):
                x = round(column * alpha.width / columns)
                strip = alpha.crop((max(0, x - band), 0, min(alpha.width, x + band + 1), alpha.height))
                histogram = strip.histogram()
                boundary_opaque += sum(histogram[1:])
                boundary_area += strip.width * strip.height
            for row in range(1, rows):
                y = round(row * alpha.height / rows)
                strip = alpha.crop((0, max(0, y - band), alpha.width, min(alpha.height, y + band + 1)))
                histogram = strip.histogram()
                boundary_opaque += sum(histogram[1:])
                boundary_area += strip.width * strip.height

            cut_ratio = boundary_opaque / max(1, boundary_area)
            cell_aspect = max(1e-6, cell_width / max(1e-6, cell_height))
            shape_penalty = abs(math.log(cell_aspect))
            preference_penalty = 0.0 if (columns, rows) == preferred else 0.05
            score = empty_cells * 1000.0 + cut_ratio * 25.0 + shape_penalty + preference_penalty
            candidates.append((score, columns, rows))
        candidates.sort()
        return [(columns, rows) for _score, columns, rows in candidates]

    def _run_normalizer(
        self,
        raw_path: Path,
        temporary_strip: Path,
        group: dict[str, Any],
        settings: dict[str, Any],
        quality: dict[str, Any],
        cell_size: int,
    ) -> tuple[str, str]:
        common = {
            **self.normalizer_defaults,
            **quality,
            "alpha_threshold": settings["alpha_threshold"],
            "min_blob_area": settings["min_blob_area"],
            "row_tolerance": settings["row_tolerance"],
        }

        def run_grid() -> None:
            normalize_uploaded_sheet(
                raw_path,
                temporary_strip,
                frames=int(group["source_frames"]),
                cell=cell_size,
                config_overrides={
                    **common,
                    "source_cols": settings["columns"],
                    "source_rows": settings["rows"],
                },
            )

        def run_ranked_grids() -> tuple[Optional[tuple[str, str]], list[str]]:
            preferred = (int(settings["columns"]), int(settings["rows"]))
            candidates = self._rank_grid_candidates(
                raw_path,
                int(group["source_frames"]),
                int(settings["alpha_threshold"]),
                preferred,
            )
            grid_errors: list[str] = []
            for columns, rows in candidates:
                temporary_strip.unlink(missing_ok=True)
                settings["columns"] = columns
                settings["rows"] = rows
                try:
                    run_grid()
                except NormalizationError as grid_error:
                    grid_errors.append(f"{columns}×{rows}: {grid_error}")
                    continue
                if self._strip_has_all_frames(
                    temporary_strip,
                    int(group["source_frames"]),
                    cell_size,
                ):
                    return ("grid_auto", f"{columns}×{rows}"), grid_errors
                grid_errors.append(f"{columns}×{rows}: one or more empty cells")
            return None, grid_errors

        if settings["mode"] == "grid":
            run_grid()
            return "grid", f"Fixed {settings['columns']}×{settings['rows']} grid"

        grid_result: Optional[tuple[str, str]] = None
        grid_errors: list[str] = []
        if settings["mode"] == "auto" and bool(group.get("prefer_grid_reading_order", False)):
            grid_result, grid_errors = run_ranked_grids()
            if grid_result is not None:
                method, layout = grid_result
                return method, f"Automatic grid: {layout}, reading left-to-right then top-to-bottom"
        try:
            normalize_uploaded_sheet(
                raw_path,
                temporary_strip,
                frames=int(group["source_frames"]),
                cell=cell_size,
                config_overrides={**common, "source_cols": None, "source_rows": None},
            )
            return "blobs", "Connected sprite detection"
        except NormalizationError as blob_error:
            if settings["mode"] != "auto":
                raise
            if not grid_errors:
                grid_result, grid_errors = run_ranked_grids()
            if grid_result is not None:
                method, layout = grid_result
                return method, f"Automatic fallback: {layout} grid"
            detail = "; ".join(grid_errors[:4])
            raise NormalizationError(
                f"Connected-sprite detection failed ({blob_error}) and no automatic grid layout produced "
                f"{group['source_frames']} complete frames. Tried {detail}."
            )

    def _render_original_animation_previews(
        self,
        raw_path: Path,
        temporary_strip: Path,
        output_dir: Path,
        group_id: str,
        group: dict[str, Any],
        settings: dict[str, Any],
        detection_method: str,
    ) -> list[dict[str, Any]]:
        """Build animated previews from the detected source frames without shrinking them to 32px."""

        use_grid = detection_method in {"grid", "grid_auto"}
        normalize_uploaded_sheet(
            raw_path,
            temporary_strip,
            frames=int(group["source_frames"]),
            cell="auto",
            config_overrides={
                **self.normalizer_defaults,
                "source_cols": int(settings["columns"]) if use_grid else None,
                "source_rows": int(settings["rows"]) if use_grid else None,
                "alpha_threshold": int(settings["alpha_threshold"]),
                "min_blob_area": int(settings["min_blob_area"]),
                "row_tolerance": int(settings["row_tolerance"]),
                # These previews preserve the detected source artwork. The
                # 32x32 quality controls apply only to the game output.
                "quality_mode": "nearest",
                "downscale_filter": "nearest",
                "alpha_aware": False,
                "gamma_correct": False,
                "preblur": 0,
                "sharpen": 1,
                "quantize": False,
                "alpha_cleanup": False,
                "hard_alpha": False,
                "despeckle": False,
                "fit_padding": 0,
            },
        )
        with Image.open(temporary_strip) as source:
            strip = source.convert("RGBA")
        cell = strip.height
        expected_width = int(group["source_frames"]) * cell
        if cell < 1 or strip.width != expected_width:
            raise AppError(
                f"Original preview strip is {strip.width}x{strip.height}; "
                f"expected {expected_width}x{cell}.",
                500,
            )

        records: list[dict[str, Any]] = []
        created: list[Path] = []
        try:
            for index, spec in enumerate(group["outputs"]):
                start = int(spec["start"])
                frames = int(spec["frames"])
                preview = strip.crop((start * cell, 0, (start + frames) * cell, cell))
                if bool(spec.get("reverse", False)):
                    preview = reverse_strip_frames(preview, frames, cell)
                stored_name = f"{group_id}__original__{index}.png"
                output_path = output_dir / stored_name
                preview.save(output_path, optimize=True)
                created.append(output_path)
                records.append(self._record_output(stored_name, output_path, spec))
            return records
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            raise

    def _process_existing(self, draft_id: str, profile_key: str, group_id: str) -> dict[str, Any]:
        group = self.catalog.group(profile_key, group_id)
        metadata = self.load(draft_id, profile_key)
        footprint = validate_footprint(metadata.get("footprint", "1x1"))
        cell_size = footprint_cell_size(footprint)
        saved = metadata.get("groups", {}).get(group_id)
        if not saved or not saved.get("raw_name"):
            raise AppError("The original upload is missing.")
        raw_root = self.draft_dir(draft_id) / "raw"
        raw_path = (raw_root / str(saved["raw_name"])).resolve()
        if not is_within(raw_path, raw_root) or not raw_path.is_file():
            raise AppError("The original upload is missing.")
        output_dir = self.draft_dir(draft_id) / "processed"
        output_dir.mkdir(parents=True, exist_ok=True)
        self._clear_previous_outputs(saved, output_dir)
        saved["outputs"] = []
        saved["original_previews"] = []
        saved["warnings"] = []
        saved["processed_version"] = uuid.uuid4().hex
        saved["cell_size"] = cell_size
        saved.pop("error", None)
        saved.pop("processing_method", None)
        saved.pop("detection_method", None)
        temporary_strip = output_dir / f".{group_id}.{uuid.uuid4().hex}.strip.png"
        temporary_original_strip = output_dir / f".{group_id}.{uuid.uuid4().hex}.original-strip.png"
        try:
            if group["mode"] == "crop_pair":
                records, warnings, bbox = self._render_crop_pair(
                    raw_path,
                    output_dir,
                    group_id,
                    group,
                    saved.get("crop", self._crop_settings(None)),
                    saved.get("quality", self._quality_settings(None)),
                    cell_size,
                )
                saved["crop_bbox"] = bbox
                saved["processing_method"] = "Interactive crop"
            elif group["mode"] == "passthrough":
                records = []
                warnings = []
                with Image.open(raw_path) as source:
                    image = source.convert("RGBA")
                for index, spec in enumerate(group["outputs"]):
                    stored_name = f"{group_id}__{index}.png"
                    output_path = output_dir / stored_name
                    image.save(output_path, optimize=True)
                    warnings.extend(validate_finished_png(output_path, spec, group, cell_size))
                    records.append(self._record_output(stored_name, output_path, spec))
            else:
                method, method_label = self._run_normalizer(
                    raw_path,
                    temporary_strip,
                    group,
                    saved.get("settings", self._detection_settings(group, None, 1, 1)),
                    saved.get("quality", self._quality_settings(None)),
                    cell_size,
                )
                saved["detection_method"] = method
                saved["processing_method"] = method_label
                original_previews = []
                preview_warnings: list[str] = []
                try:
                    original_previews = self._render_original_animation_previews(
                        raw_path,
                        temporary_original_strip,
                        output_dir,
                        group_id,
                        group,
                        saved.get("settings", self._detection_settings(group, None, 1, 1)),
                        method,
                    )
                except Exception as preview_error:
                    preview_warnings.append(
                        f"Could not build animated original previews ({preview_error}); "
                        "the untouched source upload will be shown instead."
                    )
                with Image.open(temporary_strip) as source:
                    strip = source.convert("RGBA")
                expected_size = (int(group["source_frames"]) * cell_size, cell_size)
                if strip.size != expected_size:
                    raise AppError(
                        f"Normalizer produced {strip.width}x{strip.height}; expected {expected_size[0]}x{expected_size[1]}."
                    )
                records = []
                warnings = preview_warnings
                for index, spec in enumerate(group["outputs"]):
                    start = int(spec["start"])
                    frames = int(spec["frames"])
                    output = strip.crop(
                        (
                            start * cell_size,
                            0,
                            (start + frames) * cell_size,
                            cell_size,
                        )
                    )
                    if bool(spec.get("reverse", False)):
                        output = reverse_strip_frames(output, frames, cell_size)
                    stored_name = f"{group_id}__{index}.png"
                    output_path = output_dir / stored_name
                    output.save(output_path, optimize=True)
                    warnings.extend(validate_finished_png(output_path, spec, group, cell_size))
                    records.append(self._record_output(stored_name, output_path, spec))
            saved["ready"] = True
            saved["outputs"] = records
            if group["mode"] not in {"crop_pair", "passthrough"}:
                saved["original_previews"] = original_previews
            saved["warnings"] = sorted(set(warnings))
            self.save(draft_id, metadata)
            return self.status(draft_id, profile_key)
        except Exception as exc:
            message = str(exc) if isinstance(exc, (AppError, NormalizationError)) else f"Unexpected processing error: {exc}"
            message = message.replace(" Use --allow-missing to continue anyway.", "")
            if isinstance(exc, NormalizationError):
                message += " Open Sprite detection controls to adjust the grid or component settings, then reprocess."
            saved["ready"] = False
            saved["error"] = message
            saved["outputs"] = []
            saved["original_previews"] = []
            self.save(draft_id, metadata)
            if isinstance(exc, AppError):
                raise
            if isinstance(exc, NormalizationError):
                raise AppError(f"Could not process {group['label']}: {message}") from exc
            raise AppError(message, 500) from exc
        finally:
            temporary_strip.unlink(missing_ok=True)
            temporary_original_strip.unlink(missing_ok=True)

    @staticmethod
    def _record_output(stored_name: str, path: Path, spec: dict[str, Any]) -> dict[str, Any]:
        width, height, _mode = png_dimensions(path)
        return {
            "stored_name": stored_name,
            "suffix": spec["suffix"],
            "frames": spec["frames"],
            "frame_ms": spec.get("frame_ms", 160),
            "width": width,
            "height": height,
        }

    def status(self, draft_id: str, profile_key: str) -> dict[str, Any]:
        metadata = self.load(draft_id, profile_key)
        profile = self.catalog.profile(profile_key)
        footprint = validate_footprint(metadata.get("footprint", "1x1"))
        groups = metadata.get("groups", {})
        missing = [group["id"] for group in profile["groups"] if not groups.get(group["id"], {}).get("ready")]
        return {
            "draft_id": draft_id,
            "profile": profile_key,
            "footprint": footprint,
            "cell_size": footprint_cell_size(footprint),
            "identity": metadata.get("identity", {"name": "", "slug": "", "description": ""}),
            "groups": groups,
            "missing_groups": missing,
            "ready": not missing,
        }

    def processed_file(self, draft_id: str, stored_name: str) -> Path:
        validate_draft_id(draft_id)
        if not re.fullmatch(r"[a-z][a-z0-9_-]*__(?:\d+|original__\d+)\.png", stored_name):
            raise AppError("Invalid draft image name.")
        root = self.draft_dir(draft_id) / "processed"
        path = (root / stored_name).resolve()
        if not is_within(path, root) or not path.is_file():
            raise AppError("Draft image not found.", 404)
        return path

    def raw_file(self, draft_id: str, profile_key: str, group_id: str) -> Path:
        group = self.catalog.group(profile_key, group_id)
        metadata = self.load(draft_id, profile_key)
        saved = metadata.get("groups", {}).get(group["id"], {})
        stored_name = str(saved.get("raw_name", ""))
        if stored_name != f"{group_id}.png":
            raise AppError("Original upload not found.", 404)
        root = self.draft_dir(draft_id) / "raw"
        path = (root / stored_name).resolve()
        if not is_within(path, root) or not path.is_file():
            raise AppError("Original upload not found.", 404)
        return path

    def validate_for_export(self, draft_id: str, profile_key: str) -> DraftValidation:
        metadata = self.load(draft_id, profile_key)
        profile = self.catalog.profile(profile_key)
        cell_size = footprint_cell_size(metadata.get("footprint", "1x1"))
        files: list[tuple[Path, str]] = []
        source_files: list[tuple[Path, str]] = []
        warnings: list[str] = []
        missing: list[str] = []
        groups = metadata.get("groups", {})
        for group in profile["groups"]:
            saved = groups.get(group["id"])
            if not saved or not saved.get("ready"):
                missing.append(group["label"])
                continue
            records = saved.get("outputs", [])
            if len(records) != len(group["outputs"]):
                missing.append(group["label"])
                continue
            try:
                source_files.append((self.raw_file(draft_id, profile_key, str(group["id"])), str(group["id"])))
            except AppError:
                missing.append(f"{group['label']} (original full-size upload)")
                continue
            for spec, record in zip(group["outputs"], records):
                path = self.processed_file(draft_id, str(record["stored_name"]))
                if record.get("suffix") != spec["suffix"]:
                    raise AppError(f"Draft metadata mismatch for {group['label']}.")
                warnings.extend(validate_finished_png(path, spec, group, cell_size))
                files.append((path, str(spec["suffix"])))
        if missing:
            raise AppError("Missing required images: " + ", ".join(missing))
        expected_count = sum(len(group["outputs"]) for group in profile["groups"])
        if len(files) != expected_count:
            raise AppError(f"Draft contains {len(files)} final images; expected {expected_count}.")
        if len(source_files) != len(profile["groups"]):
            raise AppError(
                f"Draft contains {len(source_files)} original full-size uploads; expected {len(profile['groups'])}."
            )
        return DraftValidation(files=files, source_files=source_files, warnings=sorted(set(warnings)))

    @staticmethod
    def _source_asset_name(slug: str, group_id: str) -> str:
        return f"{slug}.png" if group_id == "base" else f"{slug}_{group_id}.png"

    @staticmethod
    def _reject_casefold_collision(parent: Path, slug: str, label: str) -> None:
        if not parent.exists():
            return
        for child in parent.iterdir():
            if child.name.casefold() == slug.casefold():
                raise AppError(f"The {label} folder '{child.name}' already exists. Nothing was exported.", 409)

    def export(self, draft_id: str, profile_key: str, slug: str, sprites_root: Path) -> dict[str, Any]:
        slug = validate_slug(slug)
        footprint = validate_footprint(self.load(draft_id, profile_key).get("footprint", "1x1"))
        validation = self.validate_for_export(draft_id, profile_key)
        profile = self.catalog.profile(profile_key)
        parent = (sprites_root / str(profile["folder"])).resolve()
        if not parent.is_dir() or not is_within(parent, sprites_root):
            raise AppError(f"Export folder does not exist: {parent}")
        self._reject_casefold_collision(parent, slug, "game asset")

        source_parent = self.source_assets_root
        source_parent.mkdir(parents=True, exist_ok=True)
        self._reject_casefold_collision(source_parent, slug, "full-size source asset")

        target = parent / slug
        source_target = source_parent / slug
        staging = parent / f".spritesheet-assistant-{slug}-{uuid.uuid4().hex}"
        source_staging = source_parent / f".spritesheet-assistant-{slug}-{uuid.uuid4().hex}"
        game_published = False
        sources_published = False
        try:
            staging.mkdir()
            source_staging.mkdir()
            for source, suffix in validation.files:
                destination = staging / f"{slug}{suffix}"
                shutil.copy2(source, destination)
            for source, group_id in validation.source_files:
                destination = source_staging / self._source_asset_name(slug, group_id)
                shutil.copy2(source, destination)
            # Re-open every staged image before making the new folder visible.
            for staged in [*staging.glob("*.png"), *source_staging.glob("*.png")]:
                with Image.open(staged) as image:
                    image.verify()
            if target.exists() or source_target.exists():
                raise AppError(f"The folder '{slug}' appeared during export. Nothing was overwritten.", 409)
            source_staging.rename(source_target)
            sources_published = True
            staging.rename(target)
            game_published = True
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if source_staging.exists():
                shutil.rmtree(source_staging, ignore_errors=True)
            if game_published and target.exists():
                shutil.rmtree(target, ignore_errors=True)
            if sources_published and source_target.exists():
                shutil.rmtree(source_target, ignore_errors=True)
            raise

        try:
            relative = target.relative_to(sprites_root).as_posix()
        except ValueError:
            relative = str(target)
        try:
            source_relative = source_target.relative_to(APP_DIR).as_posix()
        except ValueError:
            source_relative = str(source_target)
        return {
            "ok": True,
            "path": str(target),
            "relative_path": relative,
            "file_count": len(validation.files),
            "source_path": str(source_target),
            "source_relative_path": source_relative,
            "source_file_count": len(validation.source_files),
            "footprint": footprint,
            "cell_size": footprint_cell_size(footprint),
            "warnings": validation.warnings,
            "note": "Godot will generate .import files automatically.",
        }


def infer_asset_spec(file_name: str, folder_name: str, specs: list[dict[str, Any]]) -> dict[str, Any]:
    lower = file_name.casefold()
    explicit = [spec for spec in specs if spec["suffix"] != ".png"]
    explicit.sort(key=lambda item: len(str(item["suffix"])), reverse=True)
    for spec in explicit:
        if lower.endswith(str(spec["suffix"]).casefold()):
            return spec
    if Path(file_name).stem.casefold() == folder_name.casefold():
        for spec in specs:
            if spec["suffix"] == ".png":
                return spec
    return {"frames": 1, "frame_ms": 180, "mode": "unknown", "suffix": ""}


def load_library_metadata(root: Path) -> dict[str, Any]:
    path = root / LIBRARY_METADATA_FILENAME
    if not path.exists():
        return {"version": 2, "entities": {}}
    data = read_json(path)
    entities = data.get("entities")
    if not isinstance(entities, dict):
        raise AppError(f"{LIBRARY_METADATA_FILENAME} must contain an entities object.")
    return {"version": 2, "entities": entities}


def scan_sprite_library(
    root: Path,
    catalog: ProfileCatalog,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if not root.is_dir():
        return {"root": str(root), "available": False, "entities": [], "errors": ["Sprites root was not found."]}
    entities: list[dict[str, Any]] = []
    errors: list[str] = []
    if metadata is None:
        try:
            metadata = load_library_metadata(root)
        except AppError as exc:
            metadata = {"version": 1, "entities": {}}
            errors.append(str(exc))
    metadata_entities = metadata.get("entities", {}) if isinstance(metadata, dict) else {}
    for profile_key, profile in catalog.profiles.items():
        profile_root = root / str(profile["folder"])
        if not profile_root.is_dir():
            errors.append(f"Missing {profile['folder']} folder: {profile_root}")
            continue
        specs = catalog.all_output_specs(profile_key)
        for entity_dir in sorted((p for p in profile_root.iterdir() if p.is_dir() and not p.name.startswith(".")), key=lambda p: p.name.casefold()):
            metadata_key = f"{profile_key}/{entity_dir.name}"
            saved_details = metadata_entities.get(metadata_key, {}) if isinstance(metadata_entities, dict) else {}
            if not isinstance(saved_details, dict):
                saved_details = {}
            saved_name = saved_details.get("name", "")
            saved_description = saved_details.get("description", "")
            if not isinstance(saved_name, str):
                saved_name = ""
            if not isinstance(saved_description, str):
                saved_description = ""
            try:
                footprint = validate_footprint(saved_details.get("footprint", "1x1"))
            except AppError:
                footprint = "1x1"
                errors.append(
                    f"{profile_key}/{entity_dir.name} has an invalid saved footprint; using 1x1."
                )
            expected_cell_size = footprint_cell_size(footprint)
            assets: list[dict[str, Any]] = []
            for path in sorted(entity_dir.rglob("*.png"), key=lambda p: str(p).casefold()):
                try:
                    width, height, mode = png_dimensions(path)
                    spec = infer_asset_spec(path.name, entity_dir.name, specs)
                    frames = int(spec.get("frames", 1))
                    # Keep the gallery useful for custom/legacy strips that are
                    # not yet listed in sprite_profiles.json.
                    if spec.get("mode") == "unknown" and height > 0 and width > height and width % height == 0:
                        inferred = width // height
                        if 2 <= inferred <= 32:
                            frames = inferred
                    animated = frames > 1
                    warnings: list[str] = []
                    if animated and width % frames != 0:
                        warnings.append(f"Width {width} is not divisible by {frames} frames.")
                    frame_width = width // frames if animated and width % frames == 0 else width
                    frame_height = height
                    if animated and (frame_width != expected_cell_size or height != expected_cell_size):
                        warnings.append(
                            f"Frames are {frame_width}x{height}, not "
                            f"{expected_cell_size}x{expected_cell_size} for a {footprint} footprint."
                        )
                    if (
                        not animated
                        and bool(spec.get("footprint_scaled", False))
                        and (width != expected_cell_size or height != expected_cell_size)
                    ):
                        warnings.append(
                            f"Base sprite is {width}x{height}, not "
                            f"{expected_cell_size}x{expected_cell_size} for a {footprint} footprint."
                        )
                    relative = path.relative_to(root).as_posix()
                    assets.append({
                        "name": path.name,
                        "relative_path": relative,
                        "width": width,
                        "height": height,
                        "mode": mode,
                        "frames": frames,
                        "frame_width": frame_width,
                        "frame_height": frame_height,
                        "frame_ms": int(spec.get("frame_ms", 170)),
                        "animated": animated,
                        "warnings": warnings,
                    })
                except AppError as exc:
                    assets.append({"name": path.name, "error": str(exc), "relative_path": path.relative_to(root).as_posix()})
            entities.append({
                "profile": profile_key,
                "profile_label": profile["label"],
                "name": entity_dir.name,
                "folder": entity_dir.name,
                "display_name": saved_name or entity_dir.name,
                "description": saved_description,
                "footprint": footprint,
                "cell_size": expected_cell_size,
                "metadata_present": bool(saved_name or saved_description or footprint != "1x1"),
                "relative_path": entity_dir.relative_to(root).as_posix(),
                "assets": assets,
                "asset_count": len(assets),
            })
    return {"root": str(root), "available": True, "entities": entities, "errors": errors}


class AppState:
    def __init__(self, workspace: Path, cli_sprites_root: Optional[str] = None):
        self.lock = threading.RLock()
        self.app_config = read_json(APP_CONFIG_PATH)
        configured = cli_sprites_root if cli_sprites_root is not None else str(self.app_config.get("sprites_root", ""))
        self.sprites_root, self.root_available, self.root_source = discover_sprites_root(configured)
        self.catalog = ProfileCatalog(PROFILE_PATH)
        self.drafts = DraftManager(workspace, self.catalog, SOURCE_ASSETS_DIR)

    def templates(self) -> dict[str, Any]:
        return read_json(TEMPLATE_PATH)

    def save_templates(self, data: dict[str, Any]) -> dict[str, Any]:
        expected_keys = {group["prompt_key"] for profile in self.catalog.profiles.values() for group in profile["groups"]}
        templates = data.get("templates")
        if not isinstance(data.get("style"), str) or not str(data["style"]).strip():
            raise AppError("The generic style prompt cannot be empty.")
        if not isinstance(data.get("reference"), str):
            raise AppError("The reference instruction must be text.")
        if not isinstance(templates, dict):
            raise AppError("Prompt templates must be an object.")
        missing = sorted(key for key in expected_keys if not isinstance(templates.get(key), str) or not templates[key].strip())
        if missing:
            raise AppError("Missing prompt templates: " + ", ".join(missing))
        cleaned = {
            "version": 1,
            "style": str(data["style"]),
            "reference": str(data["reference"]),
            "templates": {key: str(value) for key, value in templates.items() if isinstance(value, str)},
        }
        atomic_write_json(TEMPLATE_PATH, cleaned)
        return cleaned

    def reset_templates(self) -> dict[str, Any]:
        defaults = read_json(DEFAULT_TEMPLATE_PATH)
        atomic_write_json(TEMPLATE_PATH, defaults)
        return defaults

    def set_sprites_root(self, value: str) -> dict[str, Any]:
        root, available, source = discover_sprites_root(value)
        if not available:
            raise AppError(f"That sprites root does not exist: {root}")
        missing = [name for name in ("player", "monsters") if not (root / name).is_dir()]
        if missing:
            raise AppError("That folder is missing: " + ", ".join(missing))
        with self.lock:
            self.sprites_root = root
            self.root_available = True
            self.root_source = source
            self.app_config["sprites_root"] = str(root)
            atomic_write_json(APP_CONFIG_PATH, self.app_config)
        return self.root_info()

    def root_info(self) -> dict[str, Any]:
        return {
            "path": str(self.sprites_root),
            "available": self.root_available and self.sprites_root.is_dir(),
            "source": self.root_source,
        }

    def library_scan(self) -> dict[str, Any]:
        with self.lock:
            try:
                metadata = load_library_metadata(self.sprites_root)
            except AppError:
                metadata = None
            return scan_sprite_library(self.sprites_root, self.catalog, metadata)

    def save_library_metadata(
        self,
        profile_key: str,
        folder: str,
        name: Any,
        description: Any,
        footprint: Any = "1x1",
    ) -> dict[str, Any]:
        profile = self.catalog.profile(profile_key)
        if not isinstance(folder, str) or not folder or Path(folder).name != folder or "/" in folder or "\\" in folder:
            raise AppError("Invalid sprite folder name.")
        name, description = validate_identity(name, description)
        footprint = validate_footprint(footprint)
        profile_root = (self.sprites_root / str(profile["folder"])).resolve()
        entity_dir = (profile_root / folder).resolve()
        if not is_within(entity_dir, profile_root) or not entity_dir.is_dir():
            raise AppError("That sprite folder no longer exists.", 404)
        with self.lock:
            metadata = load_library_metadata(self.sprites_root)
            key = f"{profile_key}/{folder}"
            record = {
                "profile": profile_key,
                "folder": folder,
                "name": name,
                "description": description,
                "footprint": footprint,
                "updated_at": int(time.time()),
            }
            metadata.setdefault("entities", {})[key] = record
            atomic_write_json(self.sprites_root / LIBRARY_METADATA_FILENAME, metadata)
            return record


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], state: AppState, token: str, max_upload: int):
        super().__init__(address, handler)
        self.state = state
        self.token = token
        self.max_upload = max_upload


class RequestHandler(BaseHTTPRequestHandler):
    server: LocalServer

    def log_message(self, fmt: str, *args: Any) -> None:
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def _parsed(self):
        return urlparse(self.path)

    def _query(self) -> dict[str, list[str]]:
        return parse_qs(self._parsed().query, keep_blank_values=True)

    def _one(self, name: str, default: Optional[str] = None) -> str:
        values = self._query().get(name)
        if not values:
            if default is not None:
                return default
            raise AppError(f"Missing query parameter: {name}")
        return values[0]

    def _authorised(self) -> bool:
        supplied = self.headers.get("X-Depth-Token", "") or self._one("token", "")
        return secrets.compare_digest(supplied, self.server.token)

    def _require_auth(self) -> None:
        if not self._authorised():
            raise AppError("This app link is no longer authorised. Restart the assistant.", 403)

    def _send_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, path: Path, content_type: Optional[str] = None, *, cache: bool = False) -> None:
        if not path.is_file():
            raise AppError("File not found.", 404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=60" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self, limit: Optional[int] = None) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise AppError("Invalid Content-Length.") from exc
        maximum = limit if limit is not None else self.server.max_upload
        if length < 1:
            raise AppError("Request body is empty.")
        if length > maximum:
            raise AppError(f"Upload exceeds the {maximum // (1024 * 1024)} MB limit.", 413)
        return self.rfile.read(length)

    def _read_json_body(self) -> dict[str, Any]:
        data = self._read_body(1_000_000)
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("Request body must be valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise AppError("JSON request must be an object.")
        return parsed

    def _processing_query(self) -> dict[str, Any]:
        query = self._query()
        result: dict[str, Any] = {}
        for key in (
            "mode",
            "columns",
            "rows",
            "alpha_threshold",
            "min_blob_area",
            "row_tolerance",
            "zoom",
            "pan_x",
            "pan_y",
            "downscale_filter",
            "preblur",
            "sharpen",
            "quantize",
            "colour_count",
            "alpha_aware",
            "gamma_correct",
            "alpha_cleanup",
            "hard_alpha",
            "despeckle",
        ):
            values = query.get(key)
            if values and values[0] != "":
                result[key] = values[0]
        return result

    def _handle_error(self, exc: Exception) -> None:
        if isinstance(exc, AppError):
            self._send_json({"ok": False, "error": str(exc), "details": exc.details}, exc.status)
        else:
            print(f"Unhandled request error: {exc}", file=sys.stderr)
            self._send_json({"ok": False, "error": "Unexpected server error. See the launcher window for details."}, 500)

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = self._parsed()
            path = parsed.path
            if path == "/":
                self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
                return
            if path.startswith("/static/"):
                relative = unquote(path.removeprefix("/static/"))
                candidate = (STATIC_DIR / relative).resolve()
                if not is_within(candidate, STATIC_DIR):
                    raise AppError("Invalid static path.", 403)
                self._send_file(candidate)
                return

            self._require_auth()
            state = self.server.state
            if path == "/api/bootstrap":
                self._send_json({
                    "ok": True,
                    "version": APP_VERSION,
                    "root": state.root_info(),
                    "profiles": state.catalog.public_data(),
                    "templates": state.templates(),
                    "detection_defaults": {
                        "mode": "auto",
                        "alpha_threshold": int(state.drafts.normalizer_defaults.get("alpha_threshold", 0)),
                        "min_blob_area": int(state.drafts.normalizer_defaults.get("min_blob_area", 64)),
                        "row_tolerance": int(state.drafts.normalizer_defaults.get("row_tolerance", 12)),
                    },
                    "quality_defaults": state.drafts._quality_settings(None),
                    "footprints": {
                        key: {"cell_size": size}
                        for key, size in FOOTPRINT_SIZES.items()
                    },
                })
            elif path == "/api/library":
                self._send_json({"ok": True, **state.library_scan()})
            elif path == "/api/draft":
                self._send_json({"ok": True, **state.drafts.status(self._one("draft"), self._one("profile"))})
            elif path == "/media":
                relative = unquote(self._one("path"))
                candidate = (state.sprites_root / Path(relative)).resolve()
                if not is_within(candidate, state.sprites_root) or candidate.suffix.casefold() != ".png":
                    raise AppError("Invalid sprite path.", 403)
                self._send_file(candidate, "image/png", cache=True)
            elif path == "/draft-media":
                candidate = state.drafts.processed_file(self._one("draft"), self._one("file"))
                self._send_file(candidate, "image/png")
            elif path == "/draft-raw":
                candidate = state.drafts.raw_file(self._one("draft"), self._one("profile"), self._one("group"))
                self._send_file(candidate, "image/png")
            else:
                raise AppError("Route not found.", 404)
        except Exception as exc:
            self._handle_error(exc)

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._require_auth()
            path = self._parsed().path
            state = self.server.state
            if path == "/api/upload":
                result = state.drafts.process_upload(
                    self._one("draft"), self._one("profile"), self._one("group"), self._read_body(), self._processing_query()
                )
                self._send_json({"ok": True, **result})
            elif path == "/api/reprocess":
                body = self._read_json_body()
                result = state.drafts.reprocess(
                    str(body.get("draft", "")),
                    str(body.get("profile", "")),
                    str(body.get("group", "")),
                    body.get("processing") if isinstance(body.get("processing"), dict) else {},
                )
                self._send_json({"ok": True, **result})
            elif path == "/api/draft/identity":
                body = self._read_json_body()
                result = state.drafts.save_identity(
                    str(body.get("draft", "")),
                    str(body.get("profile", "")),
                    body.get("name", ""),
                    body.get("slug", ""),
                    body.get("description", ""),
                )
                self._send_json({"ok": True, **result})
            elif path == "/api/draft/footprint":
                body = self._read_json_body()
                result = state.drafts.set_footprint(
                    str(body.get("draft", "")),
                    str(body.get("profile", "")),
                    body.get("footprint", "1x1"),
                )
                self._send_json({"ok": True, **result})
            elif path == "/api/export":
                body = self._read_json_body()
                profile_key = str(body.get("profile", ""))
                slug = str(body.get("slug", ""))
                name, description = validate_identity(body.get("name", ""), body.get("description", ""), require_name=True)
                result = state.drafts.export(
                    str(body.get("draft", "")), profile_key, slug, state.sprites_root
                )
                result["metadata"] = state.save_library_metadata(
                    profile_key,
                    validate_slug(slug),
                    name,
                    description,
                    result["footprint"],
                )
                self._send_json(result)
            elif path == "/api/library/metadata":
                body = self._read_json_body()
                record = state.save_library_metadata(
                    str(body.get("profile", "")),
                    str(body.get("folder", "")),
                    body.get("name", ""),
                    body.get("description", ""),
                    body.get("footprint", "1x1"),
                )
                self._send_json({"ok": True, "metadata": record})
            elif path == "/api/templates":
                self._send_json({"ok": True, "templates": state.save_templates(self._read_json_body())})
            elif path == "/api/templates/reset":
                self._read_json_body()
                self._send_json({"ok": True, "templates": state.reset_templates()})
            elif path == "/api/settings/root":
                body = self._read_json_body()
                self._send_json({"ok": True, "root": state.set_sprites_root(str(body.get("path", "")))})
            elif path == "/api/draft/reset":
                body = self._read_json_body()
                state.drafts.reset(str(body.get("draft", "")))
                self._send_json({"ok": True})
            elif path == "/api/shutdown":
                self._read_json_body()
                self._send_json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                raise AppError("Route not found.", 404)
        except Exception as exc:
            self._handle_error(exc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Depth Hammer Spritesheet Assistant")
    parser.add_argument("--sprites-root", default=None, help="Override the entity_sprites/default folder.")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE, help="Draft workspace folder.")
    parser.add_argument("--host", default=None, help="Listening host; defaults to 127.0.0.1.")
    parser.add_argument("--port", type=int, default=None, help="Listening port; defaults to app_config.json.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically.")
    parser.add_argument("--token", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--verbose", action="store_true")
    return parser


def create_server(state: AppState, host: str, preferred_port: int, token: str, max_upload: int) -> LocalServer:
    ports = [0] if preferred_port == 0 else list(range(preferred_port, preferred_port + 20))
    last_error: Optional[OSError] = None
    for port in ports:
        try:
            return LocalServer((host, port), RequestHandler, state, token, max_upload)
        except OSError as exc:
            last_error = exc
    raise AppError(f"Could not open a local port near {preferred_port}: {last_error}", 500)


def main() -> None:
    args = build_parser().parse_args()
    state = AppState(args.workspace, args.sprites_root)
    config = state.app_config
    host = args.host or str(config.get("host", "127.0.0.1"))
    if host not in ("127.0.0.1", "localhost"):
        raise AppError("For safety, the assistant may only listen on 127.0.0.1.", 500)
    host = "127.0.0.1"
    preferred_port = args.port if args.port is not None else int(config.get("port", 8765))
    max_upload = int(config.get("max_upload_mb", 64)) * 1024 * 1024
    token = args.token or secrets.token_urlsafe(24)
    server = create_server(state, host, preferred_port, token, max_upload)
    server.verbose = args.verbose  # type: ignore[attr-defined]
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/?token={quote(token)}"
    print()
    print("Depth Hammer Spritesheet Assistant is running")
    print(f"Open: {url}")
    print(f"Sprites root: {state.sprites_root} ({state.root_source})")
    print("Close this window or press Ctrl+C to stop.")
    print()

    should_open = not args.no_browser and bool(config.get("open_browser", True))
    if should_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("Spritesheet Assistant stopped.")


if __name__ == "__main__":
    try:
        main()
    except AppError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
