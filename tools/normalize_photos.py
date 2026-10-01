#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把 src/ 里 1..11 的原始照片抠成人像贴图（给「合成大贾许然飞」换皮用）。

和原作者 normalize_assets.py 的区别只有「抠底」这一步：
  · normalize_assets.py 靠「从四边向内区域生长 + 饱和度闸门」求背景，
    只适合单色背景的产品图；拿生活照（咖啡馆、草地、夜景）去跑，
    整张照片都会被当成主体，抠不出人。
  · 这里改用 rembg 的 u2net_human_seg 人像分割模型，单张推理约 0.4 秒，
    头发边缘也能保住。

抠底之后的收尾（去噪 / 羽化 / 裁剪 / 统一画布 / 烤暗边）完全复用
normalize_assets.py 里的函数，保证产出规格和原作者一致：
  · 512x512、RGBA 透明底
  · 主体按长边 92% 等比缩放居中（= game.js 里的 ASSET_FILL）
  · 主体底下烤一圈柔和暗边（浅色衣服在奶油色棋盘上才分得清）

依赖：
  pip install rembg onnxruntime pillow numpy
  首次运行会自动下载 u2net_human_seg.onnx（约 168 MB）到 ~/.u2net/。
  国内直连 github.com 会超时，可以先手动下好再跑：
    curl -L -o %USERPROFILE%\\.u2net\\u2net_human_seg.onnx ^
      https://ghfast.top/https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net_human_seg.onnx

跑法：
  python tools/normalize_photos.py                  # 输出 assets/fruits/NN-jxrf.png
  python tools/normalize_photos.py --slug mingming  # 换主题就换 slug
"""
import argparse
import os
import re
import sys

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 复用原作者脚本里的收尾逻辑，避免两套实现漂移
from normalize_assets import bake_rim, subject_mask  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
OUT = os.path.join(ROOT, "assets", "fruits")
SIZE = 512            # 输出画布边长
FILL = 0.92           # 主体最长边占画布比例（必须和 game.js 的 ASSET_FILL 一致）
MODEL = "u2net_human_seg"


def find_src(i):
    for ext in ("png", "jpg", "jpeg", "webp", "bmp", "gif"):
        p = os.path.join(SRC, "%d.%s" % (i, ext))
        if os.path.exists(p):
            return p
    return None


def pack(i, cut, slug):
    """把抠好的人像收进 512x512 画布（长边 92%、居中、烤暗边）"""
    arr = np.asarray(cut.convert("RGBA")).astype(np.float32)
    a = arr[:, :, 3] / 255.0

    m = subject_mask(a)                       # 丢掉零碎噪点、填掉主体内的小孔
    a = m.astype(np.float32)
    a = np.asarray(Image.fromarray((a * 255).astype(np.uint8))
                   .filter(ImageFilter.GaussianBlur(0.8))).astype(np.float32) / 255.0
    a[a < 0.06] = 0.0
    if a.max() <= 0:
        raise RuntimeError("抠图失败：整幅图都透明了")

    out = arr.copy()
    out[:, :, 3] = np.clip(a * 255.0, 0, 255)
    im = Image.fromarray(out.astype(np.uint8), "RGBA")

    bbox = im.split()[3].point(lambda v: 255 if v > 12 else 0).getbbox()
    if bbox is None:
        raise RuntimeError("抠图失败：找不到主体")
    subject = im.crop(bbox)
    sw, sh = subject.size

    target = int(round(SIZE * FILL))
    k = target / max(sw, sh)
    nw, nh = max(1, int(round(sw * k))), max(1, int(round(sh * k)))
    subject = subject.resize((nw, nh), Image.LANCZOS)

    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(subject, ((SIZE - nw) // 2, (SIZE - nh) // 2), subject)
    canvas = bake_rim(canvas)

    name = "%02d-%s.png" % (i, slug)
    dst = os.path.join(OUT, name)
    canvas.save(dst, "PNG", optimize=True)

    px = np.asarray(canvas).astype(np.float32)
    solid = px[:, :, 3] > 200
    mean = px[:, :, :3][solid].mean(axis=0) if solid.any() else np.array([200., 200., 200.])
    hexc = "#%02x%02x%02x" % tuple(int(v) for v in mean)
    print("%-14s -> %-22s 主体 %3dx%-4d 平均色 %s  %5.1f%%  %6.1f KB"
          % (os.path.basename(find_src(i)), name, nw, nh, hexc,
             solid.mean() * 100, os.path.getsize(dst) / 1024))
    return hexc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="jxrf", help="输出文件名里的主题后缀，默认 jxrf")
    args = ap.parse_args()

    try:
        from rembg import new_session, remove
    except ImportError:
        print("缺 rembg：pip install rembg onnxruntime")
        return 1

    missing = [i for i in range(1, 12) if not find_src(i)]
    if missing:
        print("src/ 里缺这些编号：%s（命名成 1.* ~ 11.* 就行）" % missing)
        return 1

    os.makedirs(OUT, exist_ok=True)
    print("画布 %dx%d  主体占长边 %.0f%%  模型 %s\n" % (SIZE, SIZE, FILL * 100, MODEL))
    session = new_session(MODEL)
    colors = []
    for i in range(1, 12):
        colors.append(pack(i, remove(Image.open(find_src(i)).convert("RGBA"), session=session), args.slug))

    print("\n各级平均色（可写进 game.js 的 FRUITS：c1/c2 是贴图缺失时的兜底配色）:")
    for i, c in enumerate(colors, 1):
        print("  tier %2d  %s" % (i - 1, c))
    print("\n接着跑：python tools/optimize_sprites.py && python tools/build_parts.py && python tools/make_blur.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
