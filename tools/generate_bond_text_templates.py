"""Rebuild JP/CN bond-page text templates from the user's extracted FGO assets.

Example:
    python tools/generate_bond_text_templates.py --source-root "F:/path/to/FGO整包"

The complete 01/02 text is resized for 1280x720 recognition. Brown source
backgrounds are excluded with a pure-green mask for Maa TemplateMatch method 5.
No game screenshot or machine-specific absolute source path is required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import cv2
import numpy as np


DEFAULT_SCALE = 1.25
GREEN_RED_RATIO = 0.6
GREEN_RED_OFFSET = 2.0
MIN_RED = 140
MAX_BLUE = 100
BACKGROUND_BGR = (0, 255, 0)
PNG_COMPRESSION = 9
SERVERS = (("JP", "图标拆分", "base"), ("CN", "国服图标拆分", "cn"))
LEGACY_NAMES = {"羁绊结算页.png", "升级标题.png", "升级说明.png"}
TEXT_NAMES = ("img_txt_servantbonds01.png", "img_txt_servantbonds02.png")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render_template(source_bytes: bytes, scale: float) -> tuple[bytes, dict]:
    """Return deterministic RGB PNG bytes and the exact extraction parameters."""
    source = cv2.imdecode(np.frombuffer(source_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
    if source is None or source.ndim != 3 or source.shape[2] not in (3, 4):
        raise ValueError("Expected an RGB/RGBA extracted text image")
    bgr = source[:, :, :3]
    blue, green, red = cv2.split(bgr)
    foreground = ((green.astype(np.float32) - GREEN_RED_RATIO * red > GREEN_RED_OFFSET)
                  & (red > MIN_RED) & (blue < MAX_BLUE))
    if source.shape[2] == 4:
        foreground &= source[:, :, 3] > 0
    height, width = source.shape[:2]
    size = (math.floor(width * scale + 0.5), math.floor(height * scale + 0.5))
    if min(size) < 1:
        raise ValueError("Scale produces an empty template")
    resized = cv2.resize(bgr, size, interpolation=cv2.INTER_LINEAR)
    mask = cv2.resize(foreground.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST) != 0
    foreground_pixels = int(np.count_nonzero(mask))
    if foreground_pixels == 0 or foreground_pixels == mask.size:
        raise ValueError("Extraction must preserve both text and a masked background")
    resized[~mask] = BACKGROUND_BGR
    ok, encoded = cv2.imencode(".png", resized, [cv2.IMWRITE_PNG_COMPRESSION, PNG_COMPRESSION])
    if not ok:
        raise ValueError("Could not encode the extracted template")
    metadata = {
        "source_size": [width, height],
        "source_mode": "RGBA" if source.shape[2] == 4 else "RGB",
        "output_size": list(size),
        "output_mode": "RGB",
        "scale": scale,
        "size_rounding": "floor(source_dimension * scale + 0.5)",
        "image_interpolation": "cv2.INTER_LINEAR",
        "mask_interpolation": "cv2.INTER_NEAREST",
        "mask": {
            "foreground_expression": "(G - 0.6 * R > 2) & (R > 140) & (B < 100)",
            "green_red_ratio": GREEN_RED_RATIO,
            "green_red_offset": GREEN_RED_OFFSET,
            "min_red_exclusive": MIN_RED,
            "max_blue_exclusive": MAX_BLUE,
            "transparent_pixels_excluded": True,
            "background_rgb": [0, 255, 0],
            "foreground_pixels": foreground_pixels,
            "background_pixels": int(mask.size - foreground_pixels),
        },
        "padding": 0,
        "encoder": {"library": "opencv", "version": cv2.__version__,
                    "png_compression": PNG_COMPRESSION},
    }
    return encoded.tobytes(), metadata


def generate(source_root: Path, project_root: Path, scale: float = DEFAULT_SCALE) -> list[dict]:
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("Scale must be finite and greater than zero")
    manifest_path = project_root / "docs/verification/bond_stop_templates.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    old_templates = manifest.get("templates", [])
    old_by_output = {record["output"]: record for record in old_templates}
    replacements = []
    generated = []
    calibrated_scale = scale == DEFAULT_SCALE
    for server, source_directory, layer in SERVERS:
        for number, filename in enumerate(TEXT_NAMES, 1):
            relative_source = Path(source_directory) / "Battle" / filename
            source_path = source_root / relative_source
            source_bytes = source_path.read_bytes()
            png, metadata = render_template(source_bytes, scale)
            relative_output = Path("assets/resource") / layer / "image/battle/bond" / filename
            output_path = project_root / relative_output
            # Validate every input before writing any of the four final files.
            replacements.append((output_path, png))
            previous = old_by_output.get(relative_output.as_posix(), {})
            record = {
                "source_root": "FGO整包",
                "source": relative_source.as_posix(),
                "source_sha256": sha256(source_bytes),
                "server": server,
                "output": relative_output.as_posix(),
                "output_sha256": sha256(png),
                "operation": "resize complete extracted bond text; replace non-text background with pure green",
                **metadata,
                "runtime_match": {"method": 5, "green_mask": True},
                "status": ("extracted_game_text_jp_screenshot_calibrated_20261107" if server == "JP" and calibrated_scale
                           else "extracted_cn_game_text_pending_device_calibration"),
                "recognizes": ("unobstructed bond-results page" if number == 1
                               else "bond-level-up presentation only; never MAX/current cap or servant attribution"),
                "calibration_note": ("JP 1280x720 retained result/upgrade frames; dynamic heading and card animation are excluded"
                                     if server == "JP" and calibrated_scale else
                                     ("Chinese source text visually verified; common 1.25x scale agrees with prior 720p partial-template lettering; full CN screenshot calibration is pending"
                                      if server == "CN" and calibrated_scale else
                                      "Custom scale has not been calibrated against real game frames")),
            }
            if server == "JP" and not calibrated_scale:
                record["status"] = "extracted_game_text_pending_scale_calibration"
            if server == "JP" and calibrated_scale:
                record["calibration"] = {
                    "method": "real Maa TemplateMatch replay; no device input",
                    "samples": ([{
                        "file": "tests/fixtures/bond_stop/jp_bond_slot5_max.png",
                        "score": 0.909551, "box": [78, 164, 309, 43],
                    }] if number == 1 else [{
                        "file": "tests/fixtures/bond_stop/jp_bond_level_up_overlay.png",
                        "score": 0.887930, "box": [661, 321, 516, 43],
                    }, {
                        "file": "tests/fixtures/bond_stop/jp_bond_upgrade_panel.png",
                        "score": 0.886285, "box": [661, 321, 516, 43],
                    }]),
                }
            for field in ("calibration", "verification"):
                if field in previous and previous.get("scale") == scale and field not in record:
                    record[field] = previous[field]
            generated.append(record)

    kept = []
    retired = manifest.get("retired_templates", [])
    retired_hashes = {record.get("output_sha256") for record in retired}
    for record in old_templates:
        name = Path(record["output"]).name
        if name in LEGACY_NAMES:
            if record.get("output_sha256") not in retired_hashes:
                historical = dict(record)
                historical["retired_output"] = historical.pop("output")
                historical["previous_status"] = historical.pop("status", None)
                historical["status"] = "retired_replaced_by_complete_extracted_text_20261107"
                retired.append(historical)
        elif name not in TEXT_NAMES:
            kept.append(record)
    for output_path, png in replacements:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(png)
    manifest.update({
        "date": "2026-11-07",
        "status": "mixed_local_screenshots_and_temporary_bbc_fallbacks",
        "source_note": "JP/CN complete bond-page and upgrade text comes from the corresponding extracted FGO game assets. JP MAX/RESULT retain local screenshot sources. CN MAX/RESULT remain temporary BBC references pending device calibration. No BBC executable or module was run.",
        "text_template_generator": "tools/generate_bond_text_templates.py",
        "text_template_rebuild": "python tools/generate_bond_text_templates.py --source-root <FGO整包路径>",
        "templates": kept + generated,
        "retired_templates": retired,
        "upgrade_note": "A single fixed right-side complete img_txt_servantbonds02 template identifies the upgrade presentation. The left-side img_txt_servantbonds01 identifies the ordinary bond-results page. Both use method5 and a pure-green background mask; heading/card OCR branches are retired. An upgrade presentation does not establish MAX/current cap. CN complete text uses Chinese source assets and awaits full real-screen calibration.",
    })
    panel_fixture = "tests/fixtures/bond_stop/jp_bond_upgrade_panel.png"
    if (project_root / panel_fixture).is_file():
        fixture = {
            "file": panel_fixture,
            "sha256": sha256((project_root / panel_fixture).read_bytes()),
            "role": "JP late bond-level-up frame; complete right-side explanatory text visible after the heading/card animation",
            "capture_source": ".workbuddy/runtime/bond-asset-text-20261107/current.png",
            "retained_roi_xywh": [630, 285, 625, 110],
            "operation": "retain original-position text ROI in a 1280x720 frame; fill all other pixels black",
        }
        fixtures = [r for r in manifest.get("regression_fixtures", []) if r.get("file") != panel_fixture]
        manifest["regression_fixtures"] = fixtures + [fixture]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return generated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path,
                        help="Extracted FGO asset root containing 图标拆分 and 国服图标拆分")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="MaaFGO project root (defaults to this script's project)")
    parser.add_argument("--scale", type=float, default=DEFAULT_SCALE,
                        help="Text scale for 1280x720 recognition; default: 1.25")
    args = parser.parse_args()
    for record in generate(args.source_root, args.project_root, args.scale):
        print(f"{record['server']} {record['output']} {record['output_size']} "
              f"foreground={record['mask']['foreground_pixels']} sha256={record['output_sha256']}")


if __name__ == "__main__":
    main()
