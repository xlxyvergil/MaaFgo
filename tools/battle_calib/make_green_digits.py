"""把 digits 目录的彩色数字模板重制为绿底模板(green_mask 用)。

输入: 0-9.png(彩色数字, 带原按钮底色) + mask{d}.png(白色=数字本体)
输出: 0-9.png 直接覆盖为绿底版本: 数字本体保留原色, 其余区域填纯绿 (0,255,0)。
      MaaFW TemplateMatch 的 green_mask=true 会把模板中的纯绿像素从
      匹配中排除, 只对数字本体打分, 消除底色带来的滑动窗口误匹配。

用法: python tools/battle_calib/make_green_digits.py [--dry-run]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

DIGITS_DIR = Path("assets/resource/base/image/battle/digits")
GREEN = (0, 255, 0)


def make_green(digit: int, dry_run: bool = False) -> bool:
    src = DIGITS_DIR / f"{digit}.png"
    mask = DIGITS_DIR / f"mask{digit}.png"
    if not src.exists() or not mask.exists():
        print(f"[skip] {digit}: 缺少 {src.name} 或 {mask.name}")
        return False

    src_img = Image.open(src).convert("RGBA")
    mask_img = Image.open(mask).convert("RGBA")

    if src_img.size != mask_img.size:
        print(f"[warn] {digit}: 尺寸不一致 src={src_img.size} mask={mask_img.size}, 跳过")
        return False

    # mask 白色(数字本体)保留原色, 其余填纯绿
    src_px = src_img.load()
    mask_px = mask_img.load()
    w, h = src_img.size
    changed = 0
    for y in range(h):
        for x in range(w):
            r, g, b, _ = mask_px[x, y]
            # 白色区域=数字本体; 非白(暗/透明)区域视为背景
            if r > 200 and g > 200 and b > 200:
                continue
            if src_px[x, y][:3] != GREEN:
                src_px[x, y] = (*GREEN, 255)
                changed += 1

    if dry_run:
        print(f"[dry-run] {digit}: 将替换 {changed} 像素为绿底")
    else:
        src_img.save(src)
        print(f"[ok] {digit}: 绿底模板已写入 ({changed} 像素替换)")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="生成绿底数字模板")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    args = parser.parse_args()

    ok = 0
    for d in range(10):
        if make_green(d, dry_run=args.dry_run):
            ok += 1
    print(f"完成: {ok}/10")


if __name__ == "__main__":
    main()
