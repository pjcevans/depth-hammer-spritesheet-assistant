from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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


def finished_strip(frames: int) -> Image.Image:
    image = Image.new("RGBA", (frames * 32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for index in range(frames):
        x = index * 32
        colour = ((index * 53 + 80) % 255, (index * 31 + 60) % 255, 140, 255)
        draw.rectangle((x + 5, 3, x + 26, 31), fill=colour)
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

    def _make_complete_draft(self, draft_id: str, profile_key: str) -> None:
        metadata = self.manager.load(draft_id, profile_key)
        processed = self.manager.draft_dir(draft_id) / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        metadata["groups"] = {}
        profile = self.catalog.profile(profile_key)
        for group in profile["groups"]:
            records = []
            for index, spec in enumerate(group["outputs"]):
                stored_name = f"{group['id']}__{index}.png"
                path = processed / stored_name
                if group["mode"] == "passthrough":
                    image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
                    ImageDraw.Draw(image).rectangle((24, 8, 103, 127), fill=(120, 80, 50, 255))
                else:
                    image = finished_strip(int(spec["frames"]))
                image.save(path)
                records.append(self.manager._record_output(stored_name, path, spec))
            metadata["groups"][group["id"]] = {
                "ready": True,
                "source_width": 512,
                "source_height": 512,
                "outputs": records,
                "warnings": [],
            }
        self.manager.save(draft_id, metadata)

    def test_export_is_complete_atomic_and_no_overwrite(self) -> None:
        draft_id = "draft-export-001"
        self._make_complete_draft(draft_id, "mob")
        result = self.manager.export(draft_id, "mob", "bandit", self.root)
        target = self.root / "monsters" / "bandit"
        self.assertTrue(target.is_dir())
        self.assertEqual(result["file_count"], 13)
        self.assertEqual(len(list(target.glob("*.png"))), 13)
        self.assertFalse(any(target.glob("*.import")))
        with self.assertRaises(app.AppError) as context:
            self.manager.export(draft_id, "mob", "BANDIT".lower(), self.root)
        self.assertEqual(context.exception.status, 409)

    def test_library_infers_unknown_horizontal_strip(self) -> None:
        folder = self.root / "monsters" / "custom"
        folder.mkdir()
        finished_strip(6).save(folder / "custom_dance.png")
        result = app.scan_sprite_library(self.root, self.catalog)
        entity = next(item for item in result["entities"] if item["name"] == "custom")
        asset = next(item for item in entity["assets"] if item["name"] == "custom_dance.png")
        self.assertEqual(asset["frames"], 6)
        self.assertTrue(asset["animated"])

    def test_slug_rejects_paths_and_windows_names(self) -> None:
        for value in ("../bandit", "Bandit", "con", "a/b"):
            with self.assertRaises(app.AppError):
                app.validate_slug(value)
        self.assertEqual(app.validate_slug("bandit_elite-2"), "bandit_elite-2")


if __name__ == "__main__":
    unittest.main()
