"""Build the editor's small, offline UI asset set from an existing FGO dump.

This maintenance tool never downloads files and never modifies the source dump.
NarrowFigure is a three-frame atlas, not one portrait: its 150x376 pixel frames
start at x=0,155,310. Cached Atlas extraAssets URLs identify the correct atlas
and frame for each servant. The output manifest records every source hash and
crop, so the mapping can be audited and rebuilt without guessing identities.
Formation cards crop one complete, native 512x724 CharaGraph frame, then resize
it to 358x507 RGB before WebP Q90 encoding. The source atlas' spurious alpha
is discarded before resizing, retaining RGB details beneath it. Other assets
keep their existing dimensions, alpha and PNG recipes.
CE previews reuse the install's curated recognition templates, with no dump fill.
Skills, class icons, shared UI and existing recognition resources are preserved.

Usage:
    python tools/build_battle_editor_ui_assets.py --source-root F:/.../FGO整包
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
from importlib.metadata import version as package_version
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, features


CLASS_IDS = {
    "saber": 1, "archer": 2, "lancer": 3, "rider": 4, "caster": 5,
    "assassin": 6, "berserker": 7, "shielder": 8, "ruler": 9,
    "alterEgo": 10, "avenger": 11, "loreGrandCaster": 17,
    "moonCancer": 23, "foreigner": 25, "pretender": 28, "beast": 33,
    "unBeast": 40,
}
NARROW_RECT = (150, 376)
NARROW_STRIDE = 155
FORMATION_RECT = (512, 724)
FORMATION_SCALE = 0.7
PALETTE_FILENAMES = re.compile(r"(?:servant_[0-9]+|f_[0-9]+)\.png")
FORMATION_FILENAMES = re.compile(r"formation_([0-9]+)_[a-f0-9]{16}\.webp")
_PALETTE_TOOLS = None


def formation_resize_recipe() -> dict:
    """Record the complete-card transform independently of its native crop."""
    width, height = (int(dimension * FORMATION_SCALE + 0.5) for dimension in FORMATION_RECT)
    return {"width": width, "height": height, "scale": FORMATION_SCALE,
            "resample": "LANCZOS", "mode": "RGB"}


def palette_tools():
    """Load maintenance-only helpers beside this script, also for spec imports."""
    global _PALETTE_TOOLS
    if _PALETTE_TOOLS is None:
        spec = importlib.util.spec_from_file_location(
            "editor_command_card_palette", Path(__file__).with_name("command_card_palette.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _PALETTE_TOOLS = module
    return _PALETTE_TOOLS


def read_json(path: Path, fallback):
    if not path.is_file():
        return fallback
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atlas_narrow_source(url: str, source: Path) -> tuple[Path, tuple[int, int, int, int]] | None:
    """Only accept the explicit numeric NarrowFigure asset/frame URL shape."""
    match = re.fullmatch(r"/[^/]+/NarrowFigure/(\d+)/(\d+(?:_2)?)@([012])\.png", urlsplit(url).path)
    if not match:
        return None
    folder, stem, frame = match.groups()
    path = source / "从者立绘" / "NarrowFigure" / folder / (stem + ".png")
    left = int(frame) * NARROW_STRIDE
    return path, (left, 0, left + NARROW_RECT[0], NARROW_RECT[1])


def atlas_formation_source(url: str, source: Path, servant_id: int) -> tuple[Path, tuple[int, int, int, int]] | None:
    """Resolve an exact servant's declared CharaGraph half, excluding name rows.

    The a/b textures are 1024px-wide two-card atlases. Atlas @1/@2 identify
    left/right native 512x724 cards; transparent/name areas below y=724 are not
    part of the illustration. Never infer an asset from an adjacent servant ID.
    """
    if not isinstance(url, str) or type(servant_id) is not int or servant_id <= 0:
        return None
    parsed = urlsplit(url)
    match = re.fullmatch(r"/(JP|CN)/CharaGraph/([1-9][0-9]*)/([1-9][0-9]*)([ab])@([12])\.png", parsed.path)
    if (parsed.scheme != "https" or parsed.netloc != "static.atlasacademy.io"
            or parsed.query or parsed.fragment or not match
            or match[2] != str(servant_id) or match[3] != str(servant_id)):
        return None
    _, folder, stem, suffix, frame = match.groups()
    path = source / "从者立绘/CharaGraph" / folder / (stem + suffix + ".png")
    left = (int(frame) - 1) * FORMATION_RECT[0]
    return path, (left, 0, left + FORMATION_RECT[0], FORMATION_RECT[1])


class AssetBuilder:
    def __init__(self, project: Path, source: Path, cache: Path, output: Path):
        self.project, self.source, self.cache, self.output = project, source, cache, output
        self.media = output / "media"
        self.media.mkdir(parents=True, exist_ok=True)
        previous = read_json(output / "manifest.json", {})
        self._previous_files = (previous.get("files", {}) if isinstance(previous, dict) and previous.get("schema") == 1 else {})
        if not isinstance(self._previous_files, dict):
            self._previous_files = {}
        self.manifest = {
            "schema": 1,
            "generator": "tools/build_battle_editor_ui_assets.py",
            "sourceLabel": "用户本地 FGO 整包；仅供 MaaFGO 本地界面使用，版权归原权利方",
            "sources": {}, "files": {}, "servants": {}, "equips": {},
            "classes": {}, "classVariants": {}, "rarities": {}, "common": {},
            "missing": {"portraits": [], "formationPortraits": [], "equipImages": [], "skillIcons": [], "classes": [], "classVariants": []},
        }
        self._hashes = {}
        self._palette_recipe = None
        self._webp_recipe = None

    def webp_recipe(self) -> dict:
        """Fingerprint the actual encoder and opaque-source interpretation."""
        if self._webp_recipe is None:
            if not features.check("webp") or not features.version("webp"):
                raise RuntimeError("Maintenance dependency missing: Pillow with libwebp is required; no WebP fallback is allowed")
            self._webp_recipe = {
                "format": "WEBP", "lossless": False, "quality": 90, "method": 6,
                "libwebp_version": features.version("webp"), "input_mode": "RGB",
                "alpha": "discardBeforeResizePreserveRGB",
            }
        return copy.deepcopy(self._webp_recipe)

    def quantization_recipe(self) -> dict:
        if self._palette_recipe is None:
            try:
                import imagequant  # Verify availability even before reusing a cached P image.
                actual_version = package_version("imagequant")
                metadata = palette_tools().transform_metadata()
            except ImportError as exc:
                raise RuntimeError("Maintenance dependency missing: install tools/requirements-command-card-assets.txt; no palette fallback is allowed") from exc
            self._palette_recipe = {**metadata, "imagequant_version": actual_version}
        return copy.deepcopy(self._palette_recipe)

    def _hash(self, path: Path) -> str:
        if path not in self._hashes:
            self._hashes[path] = sha256(path)
        return self._hashes[path]

    def _relative_source(self, path: Path) -> tuple[str, str]:
        path = path.resolve()
        if path.is_relative_to(self.source):
            return "fgo-dump", path.relative_to(self.source).as_posix()
        if path.is_relative_to(self.project):
            return "project", path.relative_to(self.project).as_posix()
        raise ValueError(f"Source must stay in the selected dump/project: {path}")

    def add(self, path: Path, filename: str, *, crop=None, resize=None, transparent_edge_black=False) -> str | None:
        if not path.is_file():
            return None
        formation = FORMATION_FILENAMES.fullmatch(filename)
        if (Path(filename).name != filename or
                (not filename.endswith(".png") and not formation)):
            raise ValueError("Output media names must be flat PNG or fingerprinted formation WebP basenames")
        if filename.startswith("formation_") and not formation:
            raise ValueError("Complete formation cards must use fingerprinted WebP output names")
        if filename in self.manifest["files"]:
            return filename
        quantization = self.quantization_recipe() if PALETTE_FILENAMES.fullmatch(filename) else None
        encoding = self.webp_recipe() if formation else None
        if (quantization or encoding) and path.resolve().is_relative_to(self.media.resolve()):
            raise ValueError("Image inputs must be original sources, not generated editor media")
        source_kind, source_path = self._relative_source(path)
        if formation:
            sid = formation[1]
            if (source_kind != "fgo-dump" or not re.fullmatch(
                    rf"从者立绘/CharaGraph/{sid}/{sid}[ab]\.png", source_path)
                    or crop not in ((0, 0, 512, 724), (512, 0, 1024, 724))
                    or not resize or resize.get("mode") != "RGB" or transparent_edge_black):
                raise ValueError("Formation WebP inputs must be original same-servant CharaGraph crops resized from opaque RGB")
        previous = self._previous_files.get(filename)
        previous_source = previous.get("source", {}) if isinstance(previous, dict) else {}
        if isinstance(previous_source, dict):
            expected_alpha = {"method": "edgeConnectedBlack", "maxChannel": 8} if transparent_edge_black else None
            if (previous_source.get("kind") == source_kind and previous_source.get("path") == source_path
                    and previous_source.get("sha256") == self._hash(path)
                    and previous_source.get("crop") == (list(crop) if crop else None)
                    and previous_source.get("resize") == resize
                    and previous_source.get("alphaRepair") == expected_alpha
                    and previous.get("quantization") == quantization
                    and previous.get("encoding") == encoding
                    and (not quantization or previous.get("mode") == "P")
                    and (not encoding or previous.get("mode") == "RGB")
                    and (self.media / filename).is_file()
                    and sha256(self.media / filename) == previous.get("sha256")):
                self.manifest["files"][filename] = copy.deepcopy(previous)
                return filename
        with Image.open(path) as original:
            original.load()
            original_size = original.size
            if crop and not (0 <= crop[0] < crop[2] <= original.width and 0 <= crop[1] < crop[3] <= original.height):
                raise ValueError(f"Crop outside {path}: {crop}")
            image = original.crop(crop) if crop else original.copy()
            if transparent_edge_black:
                image = image.convert("RGBA")
                pixels = image.load()
                # Some independently extracted UI sprites lost their alpha.
                # Only remove black connected to the outer edge, keeping dark
                # details enclosed inside the flag/stars untouched.
                pending = [(x, y) for x in range(image.width) for y in (0, image.height - 1)]
                pending += [(x, y) for y in range(image.height) for x in (0, image.width - 1)]
                seen = set()
                while pending:
                    x, y = pending.pop()
                    if (x, y) in seen:
                        continue
                    seen.add((x, y))
                    if max(pixels[x, y][:3]) > 8:
                        continue
                    pixels[x, y] = (*pixels[x, y][:3], 0)
                    pending.extend((nx, ny) for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
                                   if 0 <= nx < image.width and 0 <= ny < image.height)
            # Complete CharaGraph illustrations are opaque artwork. Their dump
            # alpha can zero near-black hair/line art while retaining valid RGB.
            # Drop it BEFORE resizing: RGBA resampling would erase that RGB.
            if resize:
                image = image.convert(resize["mode"]).resize(
                    (resize["width"], resize["height"]), Image.Resampling[resize["resample"]])
            # Quantize once, comparing alpha against the resampled RGBA input.
            metrics = None
            original_alpha = None
            if quantization:
                image = image.convert("RGBA")
                original_alpha = image.getchannel("A")
                image, metrics = palette_tools().quantize_image(image)
            with tempfile.NamedTemporaryFile(prefix=f".{filename}.", suffix=".tmp", dir=self.media, delete=False) as temporary:
                temporary_path = Path(temporary.name)
            try:
                if encoding:
                    image.save(temporary_path, format="WEBP", lossless=encoding["lossless"],
                               quality=encoding["quality"], method=encoding["method"])
                    with Image.open(temporary_path) as saved:
                        if saved.format != "WEBP" or saved.mode != "RGB" or saved.size != image.size:
                            raise ValueError("Formation WebP changed format, opaque mode or dimensions")
                else:
                    options = {"compress_level": 9} if quantization else {}
                    image.save(temporary_path, format="PNG", optimize=True, **options)
                if quantization:
                    with Image.open(temporary_path) as saved:
                        if saved.mode != "P" or saved.size != image.size:
                            raise ValueError("Palette output changed mode or dimensions")
                        saved_alpha = saved.convert("RGBA").getchannel("A")
                        for mask in ([255 if a == 0 else 0 for a in range(256)],
                                     [255 if a >= 128 else 0 for a in range(256)]):
                            if original_alpha.point(mask).tobytes() != saved_alpha.point(mask).tobytes():
                                raise ValueError("Saved palette PNG changed alpha classes")
                temporary_path.replace(self.media / filename)
            finally:
                temporary_path.unlink(missing_ok=True)
            width, height = image.size
        origin = {"kind": source_kind, "path": source_path, "sha256": self._hash(path),
                  "width": original_size[0], "height": original_size[1]}
        if crop:
            origin["crop"] = list(crop)
        if resize:
            origin["resize"] = copy.deepcopy(resize)
        if transparent_edge_black:
            origin["alphaRepair"] = {"method": "edgeConnectedBlack", "maxChannel": 8}
        self.manifest["files"][filename] = {
            "width": width, "height": height, "sha256": sha256(self.media / filename),
            "source": origin,
        }
        if quantization:
            self.manifest["files"][filename].update({
                "mode": "P", "quantization": quantization, "quantizationMetrics": metrics,
            })
        if encoding:
            self.manifest["files"][filename].update({"mode": "RGB", "encoding": encoding})
        return filename

    def skill_icon(self, filename: str | None) -> str | None:
        if not filename or not re.fullmatch(r"skill_\d{5}\.png", filename):
            return None
        local = self.icon_source("SkillIcons/" + filename)
        if not local.is_file():
            local = self.project / "agent" / "battle_editor" / "data" / "media" / "skill" / filename
        result = self.add(local, filename)
        if result is None and filename not in self.manifest["missing"]["skillIcons"]:
            self.manifest["missing"]["skillIcons"].append(filename)
        return result

    def formation_portrait(self, sid: int, data: dict, record: dict) -> None:
        ascensions = data.get("extraAssets", {}).get("charaGraph", {}).get("ascension", {})
        ascensions = ascensions if isinstance(ascensions, dict) else {}
        preferred = str(record.get("portraitAscension", 3))
        for ascension in dict.fromkeys((preferred, "3", "1", "2", "4")):
            url = ascensions.get(ascension, "")
            mapped = atlas_formation_source(url, self.source, sid)
            if mapped is None:
                continue
            path, rect = mapped
            if not path.is_file():
                continue
            # Fingerprinted URLs keep a browser's older narrow-strip cache from
            # supplying an image with the wrong aspect ratio after an update.
            kind, relative = self._relative_source(path)
            resize = formation_resize_recipe()
            recipe = {"kind": kind, "path": relative, "sha256": self._hash(path), "crop": list(rect),
                      "resize": resize, "encoding": self.webp_recipe()}
            fingerprint = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
            filename = f"formation_{sid}_{fingerprint}.webp"
            result = self.add(path, filename, crop=rect, resize=resize)
            if result:
                record["formationPortrait"] = result
                record["formationPortraitAscension"] = int(ascension)
                self.manifest["files"][result]["source"].update({"atlasURL": url, "ascension": int(ascension)})
                return
        self.manifest["missing"]["formationPortraits"].append(sid)

    def icon_source(self, relative: str) -> Path:
        for directory in ("图标_游戏内显示", "图标拆分"):
            candidate = self.source / directory / relative
            if candidate.is_file():
                return candidate
        return self.source / "图标拆分" / relative

    def class_source(self, class_id: int, style: int) -> Path:
        transparent = self.source / "图标_游戏内显示/ClassIcons_纯图标透明" / f"class{style}_{class_id}.png"
        return transparent if transparent.is_file() else self.icon_source(f"ClassIcons/class{style}_{class_id}.png")

    def _dataset(self, filename: str, fallback):
        path = self.cache / filename
        result = read_json(path, fallback)
        if path.is_file():
            self.manifest["sources"][filename] = {"sha256": self._hash(path)}
        return result

    def build(self) -> dict:
        catalog_path = self.project / "agent" / "battle_editor" / "data" / "catalog.json"
        catalog = read_json(catalog_path, {})
        if not isinstance(catalog, dict) or not isinstance(catalog.get("servants"), list):
            raise ValueError("An existing editor catalog.json is required")
        self.manifest["sources"]["catalog.json"] = {"sha256": self._hash(catalog_path)}
        jp = {entry["id"]: entry for entry in self._dataset("JP_nice_servant.json", [])}
        cn = {entry["id"]: entry for entry in self._dataset("CN_nice_servant.json", [])}
        cn_skills = {}
        for item in cn.values():
            for entry in item.get("appendPassive", []):
                skill = entry.get("skill", {})
                if skill.get("id"):
                    cn_skills[skill["id"]] = skill

        for servant in catalog["servants"]:
            sid = servant["id"]
            record = {"activeSkills": [], "appendSkills": []}
            data = jp.get(sid, cn.get(sid, {}))
            ascensions = data.get("extraAssets", {}).get("narrowFigure", {}).get("ascension", {})
            portrait_source = None
            for ascension in ("3", "1", "2", "4"):
                mapped = atlas_narrow_source(ascensions.get(ascension, ""), self.source)
                if mapped and mapped[0].is_file():
                    path, rect = mapped
                    portrait_source = (path, rect)
                    record["portraitAscension"] = int(ascension)
                    break
            # If Atlas has no entry, an exact numeric canonical ID is safe; never
            # guess by name, filename prefix, nearby ID, or another servant's art.
            if not portrait_source and not ascensions:
                direct = self.source / "从者立绘" / "NarrowFigure" / str(sid) / f"{sid}.png"
                if direct.is_file():
                    portrait_source = (direct, (0, 0, 150, 376))
                    record["portraitAscension"] = 1
            if not portrait_source:
                self.manifest["missing"]["portraits"].append(sid)
            self.formation_portrait(sid, data, record)
            # Use NarrowFigure metadata to preserve the same ascension, but
            # only materialize its narrow fallback when no complete card exists.
            if "formationPortrait" not in record and portrait_source:
                path, rect = portrait_source
                portrait = self.add(path, f"servant_{sid}.png", crop=rect)
                if portrait:
                    record["portrait"] = portrait
            for skill in servant.get("skillVariants", servant.get("skills", [])):
                record["activeSkills"].append({
                    "id": skill["id"], "num": skill.get("num"), "name": skill.get("name", ""),
                    "icon": self.skill_icon(skill.get("icon")),
                })
            by_num = {}
            for entry in data.get("appendPassive", []):
                num = entry.get("num")
                if type(num) is not int or not 100 <= num <= 104:
                    continue
                if num not in by_num or entry.get("priority", 0) > by_num[num].get("priority", 0):
                    by_num[num] = entry
            for num, entry in sorted(by_num.items()):
                skill = entry["skill"]
                translated = cn_skills.get(skill["id"], {})
                icon_name = Path(urlsplit(skill.get("icon", "")).path).name
                record["appendSkills"].append({
                    "id": skill["id"], "num": num - 99,
                    "name": translated.get("name") or skill.get("name", ""),
                    "nameJp": skill.get("name", ""), "icon": self.skill_icon(icon_name),
                })
            self.manifest["servants"][str(sid)] = record

        # CE previews reuse only this install's curated EquipFaces/team assets
        # at runtime. No dump-only CE may become an editor-specific PNG.

        for key, cid in CLASS_IDS.items():
            # The filter and the 4/5-star cards share the exact native gold icon.
            # Individual cards select the native style using the runtime catalog's
            # rarity, which can differ from JP metadata (for example Mash).
            icon = self.add(self.class_source(cid, 3), f"class_{key}.png")
            if icon:
                self.manifest["classes"][key] = icon
            else:
                self.manifest["missing"]["classes"].append(key)
            variants = {}
            for rarity in range(6):
                style = 0 if rarity == 0 else 1 if rarity <= 2 else 2 if rarity == 3 else 3
                filename = f"class_{key}.png" if style == 3 else f"class_{key}_{style}.png"
                variant = self.add(self.class_source(cid, style), filename)
                if variant is None:
                    variant = icon
                    self.manifest["missing"]["classVariants"].append({
                        "classKey": key, "rarity": rarity, "requestedStyle": style,
                        "fallback": "sameClassGold" if icon else None,
                    })
                if variant:
                    variants[str(rarity)] = variant
            self.manifest["classVariants"][key] = variants
        for rarity in range(6):
            filename = f"rarity{rarity}_{1 if rarity == 0 else 0}.png"
            icon = self.add(self.icon_source("PartyOrganization/" + filename), f"rarity_{rarity}.png", transparent_edge_black=True)
            if icon:
                self.manifest["rarities"][str(rarity)] = icon
        for key, path in {
            "support": "PartyOrganization/icon_support_01.png",
            "background": "UISkin/img_common_bg01_skin01.png",
        }.items():
            image = self.add(self.icon_source(path), f"ui_{key}.png", transparent_edge_black=key == "support")
            if image:
                self.manifest["common"][key] = image
        self.manifest["common"]["npFrames"] = {}
        for color in ("arts", "buster", "quick"):
            image = self.add(self.icon_source(f"Battle/card_bg_np{color}.png"), f"battle_np_{color}.png", transparent_edge_black=True)
            if image:
                self.manifest["common"]["npFrames"][color] = image
        for key, source_name in {
            "masterSkill": "btn_master_skill", "masterSkillDisabled": "btn_master_skill_disable",
            "masterMenu": "btn_master_menu", "masterFrame": "frame_master_bg", "masterEquipFrame": "img_frame_master",
        }.items():
            image = self.add(self.icon_source(f"Battle/{source_name}.png"), f"battle_{key}.png", transparent_edge_black=True)
            if image:
                self.manifest["common"][key] = image

        self.manifest["stats"] = {
            "portraits": sum("portrait" in item for item in self.manifest["servants"].values()),
            "formationPortraits": sum("formationPortrait" in item for item in self.manifest["servants"].values()),
            "servants": len(self.manifest["servants"]),
            "addedEquipImages": len(self.manifest["equips"]),
            "activeSkillMappings": sum(len(x["activeSkills"]) for x in self.manifest["servants"].values()),
            "appendSkillMappings": sum(len(x["appendSkills"]) for x in self.manifest["servants"].values()),
            "classes": len(self.manifest["classes"]),
            "classVariantMappings": sum(len(x) for x in self.manifest["classVariants"].values()),
            "mediaFiles": len(self.manifest["files"]),
            "mediaBytes": sum((self.media / name).stat().st_size for name in self.manifest["files"]),
        }
        manifest_path = self.output / "manifest.json"
        manifest_bytes = (json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if manifest_path.is_file() and manifest_path.read_bytes() == manifest_bytes:
            return self.manifest
        with tempfile.NamedTemporaryFile(prefix=".manifest.", suffix=".tmp", dir=self.output, delete=False) as temporary:
            temporary_path = Path(temporary.name)
        try:
            temporary_path.write_bytes(manifest_bytes)
            temporary_path.replace(manifest_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return self.manifest

    def contact_sheet(self, destination: Path):
        """A small proof sheet with IDs and native sizes; excluded from releases."""
        preferred = [800100, 100100, 202200, 303800, 2500100, 1100100]
        names = [(str(sid), (self.manifest["servants"].get(str(sid), {}).get("formationPortrait")
                            or self.manifest["servants"].get(str(sid), {}).get("portrait"))) for sid in preferred]
        names += [(key + " gold", value) for key, value in self.manifest["classes"].items()][:6]
        names += [(f"saber rarity {rarity}", value) for rarity, value in self.manifest["classVariants"].get("saber", {}).items()]
        names += [("avenger rarity 0", self.manifest["classVariants"].get("avenger", {}).get("0"))]
        names += [("CE " + key, value["image"]) for key, value in self.manifest["equips"].items()][:4]
        names += [(key, value) for key, value in self.manifest["common"].items() if isinstance(value, str)]
        names += [("NP " + key, value) for key, value in self.manifest["common"].get("npFrames", {}).items()]
        names = [(label, name) for label, name in names if name]
        cell_w, cell_h, columns = 180, 420, 6
        result = Image.new("RGB", (columns * cell_w, ((len(names) + columns - 1) // columns) * cell_h), "#e9f4fb")
        draw = ImageDraw.Draw(result)
        for index, (label, name) in enumerate(names):
            x, y = (index % columns) * cell_w, (index // columns) * cell_h
            with Image.open(self.media / name) as source:
                image = source.convert("RGBA")
                image.thumbnail((160, 376), Image.Resampling.LANCZOS)
                position = (x + (cell_w - image.width) // 2, y + 25)
                result.paste(image.convert("RGB"), position, image.getchannel("A") if image.mode == "RGBA" else None)
                draw.text((x + 8, y + 5), f"{label} {source.width}x{source.height}", fill="#172c44")
        destination.parent.mkdir(parents=True, exist_ok=True)
        result.save(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--contact-sheet", type=Path)
    args = parser.parse_args()
    project = args.project_root.resolve()
    source = args.source_root.resolve()
    if not source.is_dir():
        parser.error("--source-root must point to the existing local FGO dump")
    output = (args.output or project / "agent/battle_editor/data/ui").resolve()
    if output == source or output.is_relative_to(source):
        parser.error("Output must not modify the source dump")
    builder = AssetBuilder(project, source, (args.cache_dir or project / "config/BattleEditor/catalog-build").resolve(), output)
    manifest = builder.build()
    if args.contact_sheet:
        builder.contact_sheet(args.contact_sheet)
    print(json.dumps(manifest["stats"], ensure_ascii=False, indent=2))
    print("Missing:", json.dumps(manifest["missing"], ensure_ascii=False))


if __name__ == "__main__":
    main()
