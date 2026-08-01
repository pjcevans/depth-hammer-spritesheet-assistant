from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import app
from normalize_anim import normalize_uploaded_sheet


def png_bytes(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def separated_sheet(frame_count: int, columns: int = 4) -> Image.Image:
    size = 40
    gap = 12
    rows = (frame_count + columns - 1) // columns
    image = Image.new("RGBA", (columns * (size + gap), rows * (size + gap)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for index in range(frame_count):
        col = index % columns
        row = index // columns
        x = col * (size + gap)
        y = row * (size + gap)
        colour = ((index * 41 + 30) % 220 + 20, (index * 67 + 45) % 210 + 20, (index * 23 + 70) % 180 + 30, 255)
        draw.rectangle((x, y, x + size - 1, y + size - 1), fill=colour)
        # Preserve transparent pixels inside each connected 1,584-pixel blob.
        draw.rectangle((x, y, x + 3, y + 3), fill=(0, 0, 0, 0))
    return image


def finished_strip(frames: int, cell: int = 32) -> Image.Image:
    image = Image.new("RGBA", (frames * cell, cell), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for index in range(frames):
        x = index * cell
        colour = ((index * 53 + 80) % 255, (index * 31 + 60) % 255, 140, 255)
        inset = max(3, cell // 7)
        draw.rectangle((x + inset, inset, x + cell - inset - 1, cell - 1), fill=colour)
    return image


def detached_creature_sheet(frame_count: int = 16, columns: int = 4) -> Image.Image:
    """Cells whose tornado body and two fists are separate blobs."""
    cell = 190
    rows = (frame_count + columns - 1) // columns
    image = Image.new("RGBA", (cell * columns, cell * rows), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for index in range(frame_count):
        col = index % columns
        row = index // columns
        x = col * cell
        y = row * cell
        colour = ((index * 31 + 60) % 220 + 20, (index * 47 + 70) % 210 + 20, 170, 255)
        draw.rectangle((x + 68, y + 45, x + 121, y + 145), fill=colour)
        draw.rectangle((x + 8, y + 68, x + 49, y + 111), fill=colour)
        draw.rectangle((x + 140, y + 68, x + 181, y + 111), fill=colour)
    return image


def variable_height_wake_sheet() -> Image.Image:
    """A 4x2 wake sheet whose changing heights defeat top-edge blob sorting."""
    columns = 4
    rows = 2
    cell_width = 160
    cell_height = 220
    heights = [44, 88, 132, 176, 180, 184, 188, 192]
    image = Image.new("RGBA", (columns * cell_width, rows * cell_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for index, height in enumerate(heights):
        column = index % columns
        row = index // columns
        left = column * cell_width + 45
        bottom = (row + 1) * cell_height - 18
        colour = (30 + index * 24, 70 + index * 17, 190 - index * 13, 255)
        draw.rectangle((left, bottom - height, left + 69, bottom), fill=colour)
    return image


class SpritesheetAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "entity_sprites" / "default"
        (self.root / "monsters").mkdir(parents=True)
        (self.root / "player").mkdir(parents=True)
        self.catalog = app.ProfileCatalog(app.PROFILE_PATH)
        self.manager = app.DraftManager(self.base / "workspace", self.catalog)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_profiles_have_expected_final_counts(self) -> None:
        mob = self.catalog.all_output_specs("mob")
        player = self.catalog.all_output_specs("player")
        self.assertEqual(len(mob), 13)
        self.assertEqual(len(player), 25)
        self.assertEqual(sum(1 for item in mob if item["frames"] > 1), 11)
        self.assertEqual(sum(1 for item in player if item["frames"] > 1), 23)
        self.assertEqual(len(self.catalog.profile("mob")["groups"]), 6)
        self.assertEqual(len(self.catalog.profile("player")["groups"]), 9)

    def test_auto_detects_sibling_depth_hammer_repository(self) -> None:
        code_root = self.base / "code"
        assistant_root = code_root / "depth-hammer-spritesheet-assistant"
        sprites_root = code_root / "depth-hammer" / "assets" / "entity_sprites" / "default"
        (sprites_root / "monsters").mkdir(parents=True)
        (sprites_root / "player").mkdir()

        with patch.object(app, "APP_DIR", assistant_root):
            resolved, available, source = app.discover_sprites_root()

        self.assertEqual(resolved, sprites_root.resolve())
        self.assertTrue(available)
        self.assertEqual(source, "auto-detected")

    def test_normalizer_api_accepts_arbitrary_frame_count(self) -> None:
        source = self.base / "three.png"
        output = self.base / "three_out.png"
        separated_sheet(3, columns=3).save(source)
        normalize_uploaded_sheet(
            source,
            output,
            frames=3,
            cell=32,
            config_overrides={"min_blob_area": 1564, "fit": True, "allow_missing": False},
        )
        with Image.open(output) as image:
            self.assertEqual(image.size, (96, 32))

    def test_no_argument_normalizer_accepts_partial_folder(self) -> None:
        folder = self.base / "partial-batch"
        folder.mkdir()
        separated_sheet(4).save(folder / "only_one_custom_sheet.png")
        result = subprocess.run(
            [sys.executable, str(app.APP_DIR / "normalize_anim.py")],
            cwd=folder,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((folder / "output" / "only_one_custom_sheet.png").is_file())

    def test_combined_walk_upload_becomes_four_strips(self) -> None:
        result = self.manager.process_upload(
            "draft-walk-0001",
            "mob",
            "walk",
            png_bytes(separated_sheet(16)),
        )
        self.assertTrue(result["groups"]["walk"]["ready"])
        outputs = result["groups"]["walk"]["outputs"]
        self.assertEqual(len(outputs), 4)
        self.assertEqual({(item["width"], item["height"]) for item in outputs}, {(128, 32)})

    def test_new_upload_replaces_pixels_and_preview_version(self) -> None:
        draft_id = "draft-replace-upload"
        first_source = Image.new("RGBA", (320, 240), (0, 0, 0, 0))
        ImageDraw.Draw(first_source).ellipse((55, 20, 265, 230), fill=(210, 55, 70, 255))
        first = self.manager.process_upload(
            draft_id,
            "mob",
            "base",
            png_bytes(first_source),
        )["groups"]["base"]
        first_output = next(item for item in first["outputs"] if item["width"] == 32)
        first_bytes = self.manager.processed_file(draft_id, first_output["stored_name"]).read_bytes()

        second_source = Image.new("RGBA", (320, 240), (0, 0, 0, 0))
        ImageDraw.Draw(second_source).rectangle((40, 35, 280, 220), fill=(40, 125, 220, 255))
        second = self.manager.process_upload(
            draft_id,
            "mob",
            "base",
            png_bytes(second_source),
        )["groups"]["base"]
        second_output = next(item for item in second["outputs"] if item["width"] == 32)

        self.assertNotEqual(first["raw_version"], second["raw_version"])
        self.assertNotEqual(first["processed_version"], second["processed_version"])
        self.assertNotEqual(
            first_bytes,
            self.manager.processed_file(draft_id, second_output["stored_name"]).read_bytes(),
        )

    def test_two_by_two_footprint_outputs_64_pixel_frames(self) -> None:
        draft_id = "draft-footprint-two"
        status = self.manager.set_footprint(draft_id, "mob", "2x2")
        self.assertEqual(status["footprint"], "2x2")
        self.assertEqual(status["cell_size"], 64)

        source = Image.new("RGBA", (500, 400), (0, 0, 0, 0))
        ImageDraw.Draw(source).ellipse((70, 20, 430, 390), fill=(95, 145, 205, 255))
        base = self.manager.process_upload(
            draft_id,
            "mob",
            "base",
            png_bytes(source),
        )["groups"]["base"]
        self.assertEqual(
            {(item["width"], item["height"]) for item in base["outputs"]},
            {(64, 64), (256, 256)},
        )

        walk = self.manager.process_upload(
            draft_id,
            "mob",
            "walk",
            png_bytes(separated_sheet(16)),
        )["groups"]["walk"]
        self.assertEqual(
            {(item["width"], item["height"]) for item in walk["outputs"]},
            {(256, 64)},
        )
        self.assertTrue(all(item["cell_size"] == 64 for item in (base, walk)))

    def test_changing_footprint_reprocesses_existing_uploads(self) -> None:
        draft_id = "draft-footprint-change"
        first = self.manager.process_upload(
            draft_id,
            "mob",
            "walk",
            png_bytes(separated_sheet(16)),
        )["groups"]["walk"]
        old_version = first["processed_version"]
        self.assertEqual(first["outputs"][0]["height"], 32)

        changed = self.manager.set_footprint(draft_id, "mob", "2×2")
        updated = changed["groups"]["walk"]
        self.assertTrue(updated["ready"])
        self.assertEqual(updated["cell_size"], 64)
        self.assertEqual(updated["outputs"][0]["height"], 64)
        self.assertEqual(updated["outputs"][0]["width"], 256)
        self.assertNotEqual(old_version, updated["processed_version"])

    def test_animation_previews_keep_detected_original_frame_size(self) -> None:
        draft_id = "draft-original-previews"
        saved = self.manager.process_upload(
            draft_id,
            "mob",
            "walk",
            png_bytes(separated_sheet(16)),
        )["groups"]["walk"]

        previews = saved["original_previews"]
        self.assertEqual(len(previews), 4)
        self.assertEqual({(item["width"], item["height"]) for item in previews}, {(160, 40)})
        self.assertTrue(all(item["width"] // item["frames"] == 40 for item in previews))
        self.assertTrue(all(item["width"] // item["frames"] > 32 for item in previews))
        for record in previews:
            self.assertTrue(self.manager.processed_file(draft_id, record["stored_name"]).is_file())

    def test_disconnected_creature_automatically_uses_grid(self) -> None:
        result = self.manager.process_upload(
            "draft-fists-0001",
            "mob",
            "walk",
            png_bytes(detached_creature_sheet()),
        )
        saved = result["groups"]["walk"]
        self.assertTrue(saved["ready"])
        self.assertEqual(saved["detection_method"], "grid_auto")
        self.assertEqual(saved["processing_method"], "Automatic fallback: 4×4 grid")
        self.assertEqual(len(saved["outputs"]), 4)

    def test_automatic_grid_infers_horizontal_eight_frame_sheet(self) -> None:
        horizontal = self.manager.process_upload(
            "draft-horizontal-eight",
            "mob",
            "death",
            png_bytes(detached_creature_sheet(8, columns=8)),
        )["groups"]["death"]
        self.assertTrue(horizontal["ready"])
        self.assertEqual(horizontal["processing_method"], "Automatic fallback: 8×1 grid")
        self.assertEqual((horizontal["outputs"][0]["width"], horizontal["outputs"][0]["height"]), (256, 32))

        standard = self.manager.process_upload(
            "draft-standard-eight",
            "mob",
            "death",
            png_bytes(detached_creature_sheet(8, columns=4)),
        )["groups"]["death"]
        self.assertTrue(standard["ready"])
        self.assertEqual(standard["processing_method"], "Automatic fallback: 4×2 grid")

    def test_wake_uses_row_major_grid_order_when_pose_heights_change(self) -> None:
        source = self.base / "wake-source.png"
        expected = self.base / "wake-expected.png"
        variable_height_wake_sheet().save(source)
        normalize_uploaded_sheet(
            source,
            expected,
            frames=8,
            cell=32,
            config_overrides={
                **self.manager.normalizer_defaults,
                "source_cols": 4,
                "source_rows": 2,
            },
        )

        saved = self.manager.process_upload(
            "draft-wake-row-major",
            "mob",
            "wake",
            source.read_bytes(),
        )["groups"]["wake"]
        actual_path = self.manager.processed_file(
            "draft-wake-row-major",
            saved["outputs"][0]["stored_name"],
        )
        with Image.open(actual_path) as actual, Image.open(expected) as wanted:
            self.assertEqual(actual.convert("RGBA").tobytes(), wanted.convert("RGBA").tobytes())
        self.assertEqual(saved["detection_method"], "grid_auto")
        self.assertEqual(
            saved["processing_method"],
            "Automatic grid: 4×2, reading left-to-right then top-to-bottom",
        )

    def test_crop_pair_accepts_small_or_large_source(self) -> None:
        for draft_id, size in (("draft-crop-small", (32, 32)), ("draft-crop-large", (900, 500))):
            source = Image.new("RGBA", size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(source)
            draw.ellipse(
                (size[0] * 0.2, size[1] * 0.1, size[0] * 0.8, size[1] * 0.9),
                fill=(110, 75, 55, 255),
            )
            result = self.manager.process_upload(draft_id, "mob", "base", png_bytes(source))
            saved = result["groups"]["base"]
            self.assertTrue(saved["ready"])
            self.assertEqual({(item["width"], item["height"]) for item in saved["outputs"]}, {(32, 32), (256, 256)})

    def test_crop_scale_changes_subject_footprint(self) -> None:
        source = Image.new("RGBA", (320, 240), (0, 0, 0, 0))
        ImageDraw.Draw(source).rectangle((80, 20, 239, 219), fill=(80, 130, 170, 255))
        draft_id = "draft-crop-scale"
        self.manager.process_upload(draft_id, "mob", "base", png_bytes(source))
        result = self.manager.reprocess(
            draft_id,
            "mob",
            "base",
            {"zoom": 0.4, "pan_x": 0, "pan_y": 0},
        )
        record = next(item for item in result["groups"]["base"]["outputs"] if item["width"] == 256)
        path = self.manager.processed_file(draft_id, record["stored_name"])
        with Image.open(path) as image:
            small_box = image.convert("RGBA").getchannel("A").getbbox()
        self.assertIsNotNone(small_box)
        self.assertLess(small_box[2] - small_box[0], 120)

    def test_quality_controls_change_only_32_pixel_output_and_persist(self) -> None:
        source = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        draw = ImageDraw.Draw(source)
        draw.ellipse((40, 18, 470, 500), fill=(74, 116, 174, 255))
        for offset in range(48, 470, 17):
            draw.line((offset, 35, 510 - offset // 3, 485), fill=(225, 184, 82, 255), width=5)

        draft_id = "draft-quality-still"
        first = self.manager.process_upload(
            draft_id,
            "mob",
            "base",
            png_bytes(source),
            {
                "downscale_filter": "nearest",
                "preblur": 0,
                "sharpen": 1,
                "quantize": False,
                "colour_count": 48,
                "alpha_aware": False,
                "gamma_correct": False,
                "alpha_cleanup": False,
                "hard_alpha": False,
                "despeckle": False,
            },
        )["groups"]["base"]
        first_small = next(item for item in first["outputs"] if item["width"] == 32)
        first_large = next(item for item in first["outputs"] if item["width"] == 256)
        first_small_bytes = self.manager.processed_file(draft_id, first_small["stored_name"]).read_bytes()
        first_large_bytes = self.manager.processed_file(draft_id, first_large["stored_name"]).read_bytes()

        second = self.manager.reprocess(
            draft_id,
            "mob",
            "base",
            {
                "downscale_filter": "lanczos",
                "preblur": 0.35,
                "sharpen": 2.5,
                "quantize": True,
                "colour_count": 24,
                "alpha_aware": True,
                "gamma_correct": True,
                "alpha_cleanup": True,
                "hard_alpha": True,
                "despeckle": True,
            },
        )["groups"]["base"]
        second_small = next(item for item in second["outputs"] if item["width"] == 32)
        second_large = next(item for item in second["outputs"] if item["width"] == 256)
        self.assertNotEqual(
            first_small_bytes,
            self.manager.processed_file(draft_id, second_small["stored_name"]).read_bytes(),
        )
        self.assertEqual(
            first_large_bytes,
            self.manager.processed_file(draft_id, second_large["stored_name"]).read_bytes(),
        )
        self.assertEqual(second["quality"]["downscale_filter"], "lanczos")
        self.assertEqual(second["quality"]["sharpen"], 2.5)
        self.assertEqual(second["quality"]["colour_count"], 24)
        self.assertTrue(second["quality"]["hard_alpha"])

    def test_animation_quality_reprocess_preserves_detection_settings(self) -> None:
        draft_id = "draft-quality-animation"
        first = self.manager.process_upload(
            draft_id,
            "mob",
            "walk",
            png_bytes(separated_sheet(16)),
        )["groups"]["walk"]
        original_detection = dict(first["settings"])

        updated = self.manager.reprocess(
            draft_id,
            "mob",
            "walk",
            {
                "downscale_filter": "box",
                "preblur": 0,
                "sharpen": 2.2,
                "quantize": True,
                "colour_count": 32,
                "hard_alpha": True,
            },
        )["groups"]["walk"]
        self.assertEqual(updated["settings"], original_detection)
        self.assertEqual(updated["quality"]["downscale_filter"], "box")
        self.assertEqual(updated["quality"]["sharpen"], 2.2)
        self.assertEqual(updated["quality"]["colour_count"], 32)
        with self.assertRaises(app.AppError):
            self.manager.reprocess(draft_id, "mob", "walk", {"sharpen": 4.1})

    def _make_complete_draft(
        self,
        draft_id: str,
        profile_key: str,
        footprint: str = "1x1",
    ) -> None:
        metadata = self.manager.load(draft_id, profile_key)
        metadata["footprint"] = footprint
        cell_size = app.footprint_cell_size(footprint)
        processed = self.manager.draft_dir(draft_id) / "processed"
        raw = self.manager.draft_dir(draft_id) / "raw"
        processed.mkdir(parents=True, exist_ok=True)
        raw.mkdir(parents=True, exist_ok=True)
        metadata["groups"] = {}
        profile = self.catalog.profile(profile_key)
        for group in profile["groups"]:
            records = []
            for index, spec in enumerate(group["outputs"]):
                stored_name = f"{group['id']}__{index}.png"
                path = processed / stored_name
                if group["mode"] == "crop_pair":
                    size = app.resolved_crop_output_size(spec, cell_size)
                    image = Image.new("RGBA", size, (0, 0, 0, 0))
                    ImageDraw.Draw(image).rectangle(
                        (max(1, size[0] // 5), max(1, size[1] // 10), size[0] - 2, size[1] - 1),
                        fill=(120, 80, 50, 255),
                    )
                elif group["mode"] == "passthrough":
                    image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
                    ImageDraw.Draw(image).rectangle((24, 8, 103, 127), fill=(120, 80, 50, 255))
                else:
                    image = finished_strip(int(spec["frames"]), cell_size)
                image.save(path)
                records.append(self.manager._record_output(stored_name, path, spec))
            raw_name = f"{group['id']}.png"
            source = Image.new("RGBA", (640, 480), (0, 0, 0, 0))
            ImageDraw.Draw(source).rectangle((40, 30, 599, 449), fill=(90, 120, 170, 255))
            source.save(raw / raw_name)
            metadata["groups"][group["id"]] = {
                "ready": True,
                "source_width": 512,
                "source_height": 512,
                "raw_name": raw_name,
                "cell_size": cell_size,
                "outputs": records,
                "warnings": [],
            }
        self.manager.save(draft_id, metadata)

    def test_two_by_two_export_validates_64_pixel_assets(self) -> None:
        draft_id = "draft-export-2x2"
        self._make_complete_draft(draft_id, "mob", "2x2")
        result = self.manager.export(draft_id, "mob", "ogre", self.root)
        self.assertEqual(result["footprint"], "2x2")
        self.assertEqual(result["cell_size"], 64)
        with Image.open(self.root / "monsters" / "ogre" / "ogre.png") as image:
            self.assertEqual(image.size, (64, 64))
        with Image.open(self.root / "monsters" / "ogre" / "ogre_walk_down.png") as image:
            self.assertEqual(image.size, (256, 64))

    def test_export_is_complete_atomic_and_no_overwrite(self) -> None:
        draft_id = "draft-export-001"
        self._make_complete_draft(draft_id, "mob")
        result = self.manager.export(draft_id, "mob", "bandit", self.root)
        target = self.root / "monsters" / "bandit"
        self.assertTrue(target.is_dir())
        self.assertEqual(result["file_count"], 13)
        self.assertEqual(len(list(target.glob("*.png"))), 13)
        self.assertFalse(any(target.glob("*.import")))
        source_target = self.base / "source_assets" / "bandit"
        self.assertTrue(source_target.is_dir())
        self.assertEqual(result["source_file_count"], 6)
        self.assertEqual(
            {path.name for path in source_target.glob("*.png")},
            {
                "bandit.png",
                "bandit_walk.png",
                "bandit_melee.png",
                "bandit_death.png",
                "bandit_deathfire.png",
                "bandit_wake.png",
            },
        )
        with Image.open(source_target / "bandit_walk.png") as source:
            self.assertEqual(source.size, (640, 480))
        with self.assertRaises(app.AppError) as context:
            self.manager.export(draft_id, "mob", "BANDIT".lower(), self.root)
        self.assertEqual(context.exception.status, 409)

    def test_existing_source_folder_blocks_export_without_creating_game_folder(self) -> None:
        draft_id = "draft-source-collision"
        self._make_complete_draft(draft_id, "mob")
        (self.base / "source_assets" / "rat").mkdir(parents=True)
        with self.assertRaises(app.AppError) as context:
            self.manager.export(draft_id, "mob", "rat", self.root)
        self.assertEqual(context.exception.status, 409)
        self.assertFalse((self.root / "monsters" / "rat").exists())

    def test_library_infers_unknown_horizontal_strip(self) -> None:
        folder = self.root / "monsters" / "custom"
        folder.mkdir()
        finished_strip(6).save(folder / "custom_dance.png")
        result = app.scan_sprite_library(self.root, self.catalog)
        entity = next(item for item in result["entities"] if item["name"] == "custom")
        asset = next(item for item in entity["assets"] if item["name"] == "custom_dance.png")
        self.assertEqual(asset["frames"], 6)
        self.assertTrue(asset["animated"])

    def test_identity_and_library_descriptions_persist(self) -> None:
        draft_id = "draft-details-001"
        self.manager.save_identity(draft_id, "mob", "Bandit", "bandit", "Patched leather and a chipped sword.")
        reloaded = app.DraftManager(self.base / "workspace", self.catalog).status(draft_id, "mob")
        self.assertEqual(reloaded["identity"]["description"], "Patched leather and a chipped sword.")

        folder = self.root / "monsters" / "bandit"
        folder.mkdir()
        state = app.AppState(self.base / "state-workspace", str(self.root))
        state.save_library_metadata("mob", "bandit", "Bandit", "Patched leather and a chipped sword.")
        result = state.library_scan()
        entity = next(item for item in result["entities"] if item["name"] == "bandit")
        self.assertEqual(entity["display_name"], "Bandit")
        self.assertEqual(entity["description"], "Patched leather and a chipped sword.")
        self.assertTrue((self.root / app.LIBRARY_METADATA_FILENAME).is_file())

    def test_existing_library_footprint_persists_and_sets_expected_cell_size(self) -> None:
        folder = self.root / "monsters" / "ogre"
        folder.mkdir()
        finished_strip(4, 64).save(folder / "ogre_walk_down.png")
        state = app.AppState(self.base / "footprint-state", str(self.root))
        state.save_library_metadata(
            "mob",
            "ogre",
            "Ogre",
            "A huge two-tile brute.",
            "2x2",
        )
        entity = next(item for item in state.library_scan()["entities"] if item["name"] == "ogre")
        self.assertEqual(entity["footprint"], "2x2")
        self.assertEqual(entity["cell_size"], 64)
        asset = next(item for item in entity["assets"] if item["name"] == "ogre_walk_down.png")
        self.assertFalse(any("not 64x64" in warning for warning in asset["warnings"]))

    def test_library_replacement_changes_only_selected_animation_and_saves_original(self) -> None:
        folder = self.root / "monsters" / "bandit"
        folder.mkdir()
        left = folder / "bandit_walk_left.png"
        right = folder / "bandit_walk_right.png"
        finished_strip(4).save(left)
        original_left = left.read_bytes()
        right_image = finished_strip(4)
        ImageDraw.Draw(right_image).rectangle((2, 2, 8, 8), fill=(240, 20, 20, 255))
        right_image.save(right)
        original_right = right.read_bytes()

        state = app.AppState(self.base / "replace-state", str(self.root))
        state.drafts.source_assets_root = self.base / "source_assets"
        state.save_library_metadata("mob", "bandit", "Bandit", "A wary cave ambusher.")
        source = finished_strip(4, 96)
        result = state.process_library_replacement(
            "library-edit-bandit-left",
            "mob",
            "bandit",
            "bandit_walk_left.png",
            "1x1",
            png_bytes(source),
            {"mode": "auto", "sharpen": 2.0},
        )
        self.assertTrue(result["group"]["ready"])
        committed = state.commit_library_replacement("library-edit-bandit-left")

        self.assertEqual(committed["relative_path"], "monsters/bandit/bandit_walk_left.png")
        self.assertNotEqual(left.read_bytes(), original_left)
        self.assertEqual(right.read_bytes(), original_right)
        with Image.open(left) as image:
            self.assertEqual(image.size, (128, 32))
        source_copy = self.base / "source_assets" / "bandit" / "bandit_walk_left.png"
        with Image.open(source_copy) as image:
            self.assertEqual(image.size, (384, 96))
        entity = next(item for item in state.library_scan()["entities"] if item["name"] == "bandit")
        self.assertEqual(entity["description"], "A wary cave ambusher.")
        replaced = next(item for item in entity["assets"] if item["name"] == "bandit_walk_left.png")
        self.assertEqual(replaced["processing_preset"]["quality"]["sharpen"], 2.0)

    def test_library_replacement_respects_two_by_two_footprint(self) -> None:
        folder = self.root / "monsters" / "ogre"
        folder.mkdir()
        target = folder / "ogre_death.png"
        finished_strip(8).save(target)
        state = app.AppState(self.base / "replace-2x2-state", str(self.root))
        state.drafts.source_assets_root = self.base / "source_assets"
        result = state.process_library_replacement(
            "library-edit-ogre-death",
            "mob",
            "ogre",
            "ogre_death.png",
            "2x2",
            png_bytes(separated_sheet(8)),
            {"mode": "auto", "hard_alpha": True},
        )
        self.assertEqual(result["cell_size"], 64)
        output = result["group"]["outputs"][0]
        self.assertEqual((output["width"], output["height"]), (512, 64))
        state.commit_library_replacement("library-edit-ogre-death")
        with Image.open(target) as image:
            self.assertEqual(image.size, (512, 64))

    def test_library_replacement_supports_unknown_custom_animation_strip(self) -> None:
        folder = self.root / "monsters" / "custom"
        folder.mkdir()
        target = folder / "custom_dance.png"
        finished_strip(6).save(target)
        state = app.AppState(self.base / "replace-custom-state", str(self.root))
        state.drafts.source_assets_root = self.base / "source_assets"
        result = state.process_library_replacement(
            "library-edit-custom-dance",
            "mob",
            "custom",
            "custom_dance.png",
            "1x1",
            png_bytes(separated_sheet(6, columns=3)),
            {"mode": "auto"},
        )
        self.assertEqual(result["group_id"], "replace_anim_6")
        self.assertEqual(result["expected_frames"], 6)
        self.assertEqual(result["group"]["outputs"][0]["width"], 192)

    def test_library_directional_replacement_updates_all_four_sheets_as_one_block(self) -> None:
        folder = self.root / "monsters" / "bandit"
        folder.mkdir()
        directions = ("up", "right", "left", "down")
        originals: dict[str, bytes] = {}
        for index, direction in enumerate(directions):
            target = folder / f"bandit_walk_{direction}.png"
            image = finished_strip(4)
            ImageDraw.Draw(image).rectangle((index + 1, 1, index + 4, 4), fill=(250, 20, 20, 255))
            image.save(target)
            originals[direction] = target.read_bytes()

        state = app.AppState(self.base / "replace-walk-block-state", str(self.root))
        state.drafts.source_assets_root = self.base / "source_assets"
        scan = next(item for item in state.library_scan()["entities"] if item["name"] == "bandit")
        walk_group = next(group for group in scan["replacement_groups"] if group["id"] == "walk")
        self.assertEqual(walk_group["asset_names"], [
            "bandit_walk_up.png",
            "bandit_walk_right.png",
            "bandit_walk_left.png",
            "bandit_walk_down.png",
        ])

        result = state.process_library_replacement(
            "library-edit-bandit-walk-block",
            "mob",
            "bandit",
            "bandit_walk_up.png",
            "1x1",
            png_bytes(separated_sheet(16)),
            {"mode": "auto", "sharpen": 1.75},
            "walk",
        )
        self.assertEqual(result["group_id"], "replace_block_walk")
        self.assertEqual(result["expected_frames"], 16)
        self.assertEqual(len(result["group"]["outputs"]), 4)
        committed = state.commit_library_replacement("library-edit-bandit-walk-block")
        self.assertEqual(committed["asset_count"], 4)
        self.assertEqual(committed["action_group"], "walk")
        for direction in directions:
            target = folder / f"bandit_walk_{direction}.png"
            self.assertNotEqual(target.read_bytes(), originals[direction])
            with Image.open(target) as image:
                self.assertEqual(image.size, (128, 32))
        with Image.open(self.base / "source_assets" / "bandit" / "bandit_walk.png") as image:
            self.assertEqual(image.size, separated_sheet(16).size)

        refreshed = next(item for item in state.library_scan()["entities"] if item["name"] == "bandit")
        for asset in refreshed["assets"]:
            if asset["name"] in walk_group["asset_names"]:
                self.assertEqual(asset["processing_preset"]["quality"]["sharpen"], 1.75)

    def test_incomplete_directional_set_is_not_offered_as_a_block(self) -> None:
        folder = self.root / "monsters" / "partial"
        folder.mkdir()
        for direction in ("up", "right", "left"):
            finished_strip(4).save(folder / f"partial_walk_{direction}.png")
        state = app.AppState(self.base / "partial-block-state", str(self.root))
        entity = next(item for item in state.library_scan()["entities"] if item["name"] == "partial")
        self.assertFalse(any(group["id"] == "walk" for group in entity["replacement_groups"]))
        with self.assertRaises(app.AppError):
            state.process_library_replacement(
                "library-edit-partial-walk",
                "mob",
                "partial",
                "partial_walk_up.png",
                "1x1",
                png_bytes(separated_sheet(16)),
                {"mode": "auto"},
                "walk",
            )

    def test_sprite_library_ui_exposes_grouped_and_standalone_editor_controls(self) -> None:
        script = (app.STATIC_DIR / "app.js").read_text(encoding="utf-8")
        styles = (app.STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        self.assertIn("Replace this asset", script)
        self.assertIn("Replace only this direction", script)
        self.assertIn("Or use “Replace all ${asset.directional_block} directions” above for a complete set.", script)
        self.assertIn("Replace directional animation blocks", script)
        self.assertIn("Replace all ${directionalGroup.id} directions", script)
        self.assertIn("All ${view.targetNames.length} directional PNGs will be replaced together.", script)
        self.assertIn("/api/library/replacement/upload", script)
        self.assertIn("/api/library/replacement/reprocess", script)
        self.assertIn("/api/library/replacement/commit", script)
        self.assertIn("library-replacement-card", styles)

    def test_directional_assets_keep_individual_replacement_path(self) -> None:
        folder = self.root / "monsters" / "bandit"
        folder.mkdir()
        for direction in ("up", "right", "left", "down"):
            finished_strip(4).save(folder / f"bandit_walk_{direction}.png")
        state = app.AppState(self.base / "individual-direction-state", str(self.root))
        before_right = (folder / "bandit_walk_right.png").read_bytes()
        result = state.process_library_replacement(
            "library-edit-bandit-walk-left-only",
            "mob",
            "bandit",
            "bandit_walk_left.png",
            "1x1",
            png_bytes(separated_sheet(4)),
            {"mode": "auto"},
        )
        self.assertEqual(result["expected_frames"], 4)
        committed = state.commit_library_replacement("library-edit-bandit-walk-left-only")
        self.assertEqual(committed["asset_count"], 1)
        self.assertEqual([record["asset"] for record in committed["assets"]], ["bandit_walk_left.png"])
        self.assertEqual((folder / "bandit_walk_right.png").read_bytes(), before_right)

    def test_slug_rejects_paths_and_windows_names(self) -> None:
        for value in ("../bandit", "Bandit", "con", "a/b"):
            with self.assertRaises(app.AppError):
                app.validate_slug(value)
        self.assertEqual(app.validate_slug("bandit_elite-2"), "bandit_elite-2")

    def test_creation_original_preview_has_large_stage(self) -> None:
        styles = (app.STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".preview-stage.original canvas { width: min(512px, 100%); }", styles)
        self.assertIn(".preview-stage.original img { width: min(512px, 100%); max-height: 512px; }", styles)
        self.assertIn("minmax(min(680px, 100%), 1fr)", styles)
        self.assertIn(".preview-stage.actual canvas { width: 32px; height: 32px; }", styles)


if __name__ == "__main__":
    unittest.main()
