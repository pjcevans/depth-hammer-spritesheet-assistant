#!/usr/bin/env python3
"""Depth Hammer Spritesheet Assistant local server.

The browser is only the interface.  All filesystem access, validation and image
normalisation happen here, bound to 127.0.0.1 and protected by a per-run token.
"""

from __future__ import annotations

import argparse
import io
import json
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

from normalize_anim import NormalizationError, normalize_uploaded_sheet


APP_VERSION = "1.0.0"
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
CONFIG_DIR = APP_DIR / "config"
APP_CONFIG_PATH = APP_DIR / "app_config.json"
PROFILE_PATH = CONFIG_DIR / "sprite_profiles.json"
TEMPLATE_PATH = CONFIG_DIR / "prompt_templates.json"
DEFAULT_TEMPLATE_PATH = CONFIG_DIR / "prompt_templates.defaults.json"
NORMALIZER_CONFIG_PATH = APP_DIR / "mob_anim_batch.json"
DEFAULT_WORKSPACE = APP_DIR / "workspace"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
SAFE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
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


def validate_draft_id(value: str) -> str:
    if not SAFE_ID_RE.fullmatch(value):
        raise AppError("Invalid draft identifier.")
    return value


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
    # Uploaded combined sheets must be processed in reading order. Per-output
    # reversal is handled after splitting.
    return {**defaults, "reverse": False, "allow_missing": False, "fit": True, "cell": 32}


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


def validate_finished_png(path: Path, spec: dict[str, Any], group: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    with Image.open(path) as source:
        image = source.convert("RGBA")
    frames = int(spec["frames"])
    if group["mode"] == "passthrough":
        minimum = group.get("minimum_size", [1, 1])
        if image.width < int(minimum[0]) or image.height < int(minimum[1]):
            raise AppError(
                f"{path.name} is {image.width}x{image.height}; it must be at least {minimum[0]}x{minimum[1]}."
            )
    else:
        expected = (frames * 32, 32)
        if image.size != expected:
            raise AppError(f"{path.name} is {image.width}x{image.height}; expected {expected[0]}x{expected[1]}.")
        hashes: list[bytes] = []
        for index in range(frames):
            frame = image.crop((index * 32, 0, (index + 1) * 32, 32))
            if frame.getchannel("A").getbbox() is None:
                raise AppError(f"{path.name} has an empty frame at position {index + 1}.")
            hashes.append(frame.tobytes())
        if len(set(hashes)) < len(hashes):
            warnings.append(f"{path.name} contains identical frames; visually check that this is intentional.")
    if not has_useful_alpha(image):
        raise AppError(f"{path.name} needs a genuine transparent background.")
    return warnings


@dataclass
class DraftValidation:
    files: list[tuple[Path, str]]
    warnings: list[str]


class DraftManager:
    def __init__(self, workspace: Path, catalog: ProfileCatalog):
        self.workspace = workspace.resolve()
        self.drafts_root = self.workspace / "drafts"
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
            "version": 1,
            "draft_id": draft_id,
            "profile": profile_key,
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
            "groups": {},
        }

    def save(self, draft_id: str, metadata: dict[str, Any]) -> None:
        metadata["updated_at"] = int(time.time())
        atomic_write_json(self.metadata_path(draft_id), metadata)

    def reset(self, draft_id: str) -> None:
        path = self.draft_dir(draft_id)
        if path.exists() and is_within(path, self.drafts_root):
            shutil.rmtree(path)

    def _inspect_upload(self, data: bytes) -> tuple[int, int]:
        if not data.startswith(PNG_SIGNATURE):
            raise AppError("The uploaded file is not a PNG.")
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise AppError("The uploaded PNG is invalid or too large.") from exc
        if width < 1 or height < 1 or width * height > 80_000_000:
            raise AppError("The uploaded PNG has unsafe dimensions.")
        return width, height

    def process_upload(self, draft_id: str, profile_key: str, group_id: str, data: bytes) -> dict[str, Any]:
        group = self.catalog.group(profile_key, group_id)
        source_width, source_height = self._inspect_upload(data)
        with self._lock:
            metadata = self.load(draft_id, profile_key)
            draft_dir = self.draft_dir(draft_id)
            raw_dir = draft_dir / "raw"
            output_dir = draft_dir / "processed"
            raw_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)
            raw_path = raw_dir / f"{group_id}.png"
            atomic_write_bytes(raw_path, data)

            old_group = metadata.get("groups", {}).get(group_id, {})
            for old_output in old_group.get("outputs", []):
                candidate = output_dir / str(old_output.get("stored_name", ""))
                if candidate.is_file() and is_within(candidate, output_dir):
                    candidate.unlink()

            output_records: list[dict[str, Any]] = []
            warnings: list[str] = []
            temporary_strip = output_dir / f".{group_id}.{uuid.uuid4().hex}.strip.png"
            try:
                if group["mode"] == "passthrough":
                    with Image.open(raw_path) as source:
                        image = source.convert("RGBA")
                    for index, spec in enumerate(group["outputs"]):
                        stored_name = f"{group_id}__{index}.png"
                        output_path = output_dir / stored_name
                        image.save(output_path, optimize=True)
                        warnings.extend(validate_finished_png(output_path, spec, group))
                        output_records.append(self._record_output(stored_name, output_path, spec))
                else:
                    normalize_uploaded_sheet(
                        raw_path,
                        temporary_strip,
                        frames=int(group["source_frames"]),
                        cell=32,
                        config_overrides=self.normalizer_defaults,
                    )
                    with Image.open(temporary_strip) as source:
                        strip = source.convert("RGBA")
                    expected_size = (int(group["source_frames"]) * 32, 32)
                    if strip.size != expected_size:
                        raise AppError(
                            f"Normalizer produced {strip.width}x{strip.height}; expected {expected_size[0]}x{expected_size[1]}."
                        )
                    for index, spec in enumerate(group["outputs"]):
                        start = int(spec["start"])
                        frames = int(spec["frames"])
                        output = strip.crop((start * 32, 0, (start + frames) * 32, 32))
                        if bool(spec.get("reverse", False)):
                            output = reverse_strip_frames(output, frames)
                        stored_name = f"{group_id}__{index}.png"
                        output_path = output_dir / stored_name
                        output.save(output_path, optimize=True)
                        warnings.extend(validate_finished_png(output_path, spec, group))
                        output_records.append(self._record_output(stored_name, output_path, spec))
            except NormalizationError as exc:
                metadata.setdefault("groups", {})[group_id] = {
                    "ready": False,
                    "source_width": source_width,
                    "source_height": source_height,
                    "error": str(exc),
                }
                self.save(draft_id, metadata)
                raise AppError(f"Could not process {group['label']}: {exc}") from exc
            except Exception as exc:
                if isinstance(exc, AppError):
                    message = str(exc)
                else:
                    message = f"Unexpected processing error: {exc}"
                metadata.setdefault("groups", {})[group_id] = {
                    "ready": False,
                    "source_width": source_width,
                    "source_height": source_height,
                    "error": message,
                }
                self.save(draft_id, metadata)
                if isinstance(exc, AppError):
                    raise
                raise AppError(message, 500) from exc
            finally:
                temporary_strip.unlink(missing_ok=True)

            metadata.setdefault("groups", {})[group_id] = {
                "ready": True,
                "source_width": source_width,
                "source_height": source_height,
                "raw_name": raw_path.name,
                "outputs": output_records,
                "warnings": warnings,
            }
            self.save(draft_id, metadata)
            return self.status(draft_id, profile_key)

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
        groups = metadata.get("groups", {})
        missing = [group["id"] for group in profile["groups"] if not groups.get(group["id"], {}).get("ready")]
        return {
            "draft_id": draft_id,
            "profile": profile_key,
            "groups": groups,
            "missing_groups": missing,
            "ready": not missing,
        }

    def processed_file(self, draft_id: str, stored_name: str) -> Path:
        validate_draft_id(draft_id)
        if not re.fullmatch(r"[a-z][a-z0-9_-]*__\d+\.png", stored_name):
            raise AppError("Invalid draft image name.")
        root = self.draft_dir(draft_id) / "processed"
        path = (root / stored_name).resolve()
        if not is_within(path, root) or not path.is_file():
            raise AppError("Draft image not found.", 404)
        return path

    def validate_for_export(self, draft_id: str, profile_key: str) -> DraftValidation:
        metadata = self.load(draft_id, profile_key)
        profile = self.catalog.profile(profile_key)
        files: list[tuple[Path, str]] = []
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
            for spec, record in zip(group["outputs"], records):
                path = self.processed_file(draft_id, str(record["stored_name"]))
                if record.get("suffix") != spec["suffix"]:
                    raise AppError(f"Draft metadata mismatch for {group['label']}.")
                warnings.extend(validate_finished_png(path, spec, group))
                files.append((path, str(spec["suffix"])))
        if missing:
            raise AppError("Missing required images: " + ", ".join(missing))
        expected_count = sum(len(group["outputs"]) for group in profile["groups"])
        if len(files) != expected_count:
            raise AppError(f"Draft contains {len(files)} final images; expected {expected_count}.")
        return DraftValidation(files=files, warnings=sorted(set(warnings)))

    def export(self, draft_id: str, profile_key: str, slug: str, sprites_root: Path) -> dict[str, Any]:
        slug = validate_slug(slug)
        validation = self.validate_for_export(draft_id, profile_key)
        profile = self.catalog.profile(profile_key)
        parent = (sprites_root / str(profile["folder"])).resolve()
        if not parent.is_dir() or not is_within(parent, sprites_root):
            raise AppError(f"Export folder does not exist: {parent}")
        for child in parent.iterdir():
            if child.name.casefold() == slug.casefold():
                raise AppError(f"The folder '{child.name}' already exists. Nothing was exported.", 409)

        target = parent / slug
        staging = parent / f".spritesheet-assistant-{slug}-{uuid.uuid4().hex}"
        try:
            staging.mkdir()
            for source, suffix in validation.files:
                destination = staging / f"{slug}{suffix}"
                shutil.copy2(source, destination)
            # Re-open every staged image before making the new folder visible.
            for staged in staging.glob("*.png"):
                with Image.open(staged) as image:
                    image.verify()
            if target.exists():
                raise AppError(f"The folder '{slug}' appeared during export. Nothing was overwritten.", 409)
            staging.rename(target)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise

        try:
            relative = target.relative_to(sprites_root).as_posix()
        except ValueError:
            relative = str(target)
        return {
            "ok": True,
            "path": str(target),
            "relative_path": relative,
            "file_count": len(validation.files),
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


def scan_sprite_library(root: Path, catalog: ProfileCatalog) -> dict[str, Any]:
    if not root.is_dir():
        return {"root": str(root), "available": False, "entities": [], "errors": ["Sprites root was not found."]}
    entities: list[dict[str, Any]] = []
    errors: list[str] = []
    for profile_key, profile in catalog.profiles.items():
        profile_root = root / str(profile["folder"])
        if not profile_root.is_dir():
            errors.append(f"Missing {profile['folder']} folder: {profile_root}")
            continue
        specs = catalog.all_output_specs(profile_key)
        for entity_dir in sorted((p for p in profile_root.iterdir() if p.is_dir() and not p.name.startswith(".")), key=lambda p: p.name.casefold()):
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
                    if animated and (frame_width != 32 or height != 32):
                        warnings.append(f"Frames are {frame_width}x{height}, not 32x32.")
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
        self.drafts = DraftManager(workspace, self.catalog)

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
                })
            elif path == "/api/library":
                self._send_json({"ok": True, **scan_sprite_library(state.sprites_root, state.catalog)})
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
                    self._one("draft"), self._one("profile"), self._one("group"), self._read_body()
                )
                self._send_json({"ok": True, **result})
            elif path == "/api/export":
                body = self._read_json_body()
                result = state.drafts.export(
                    str(body.get("draft", "")), str(body.get("profile", "")), str(body.get("slug", "")), state.sprites_root
                )
                self._send_json(result)
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
