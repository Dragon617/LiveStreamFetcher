# -*- coding: utf-8 -*-
"""v8.4.13: 新 LOGO 白底 → 透明底处理。

算法：边缘 flood fill（BFS）标记连通背景白区 → alpha=0。
内部被金色包围的白色区域（播放键三角形）不与边缘连通，自动保留。
"""
import os
from collections import deque

import numpy as np
from PIL import Image, ImageFilter

SRC = r"E:\WorkBuddy\workspace\直播流获取工具\_logo_new_src.jpg"
DST_PNG = r"E:\WorkBuddy\workspace\直播流获取工具\icons\logo_main.png"
DST_ICO = r"E:\WorkBuddy\workspace\直播流获取工具\app_icon.ico"

WHITE_THRESHOLD = 240   # 亮度下限：max(r,g,b) > 240
SAT_THRESHOLD = 18      # 饱和度上限：max-min < 18（金色高光 R>G>B 差值大，不会被误判）


def main():
    img = Image.open(SRC).convert("RGB")
    arr = np.asarray(img)
    h, w, _ = arr.shape
    print(f"src: {w}x{h}")

    # 1. 白度掩码：高亮度 + 低饱和度（纯白的特征）
    #    金色 logo 亮部虽然亮，但 R>G>B 饱和度差明显，不会误判
    rgb_max = arr.max(axis=2).astype(np.int16)
    rgb_min = arr.min(axis=2).astype(np.int16)
    white_mask = (rgb_max > WHITE_THRESHOLD) & ((rgb_max - rgb_min) < SAT_THRESHOLD)

    # 2. 从边界 BFS，标记与边缘连通的白色区域 = 背景
    bg = np.zeros((h, w), dtype=bool)
    dq = deque()
    # 四边种子
    for x in range(w):
        if white_mask[0, x] and not bg[0, x]:
            bg[0, x] = True
            dq.append((0, x))
        if white_mask[h - 1, x] and not bg[h - 1, x]:
            bg[h - 1, x] = True
            dq.append((h - 1, x))
    for y in range(h):
        if white_mask[y, 0] and not bg[y, 0]:
            bg[y, 0] = True
            dq.append((y, 0))
        if white_mask[y, w - 1] and not bg[y, w - 1]:
            bg[y, w - 1] = True
            dq.append((y, w - 1))

    while dq:
        y, x = dq.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < h and 0 <= nx < w and white_mask[ny, nx] and not bg[ny, nx]:
                bg[ny, nx] = True
                dq.append((ny, nx))

    bg_ratio = bg.mean()
    print(f"background ratio: {bg_ratio:.1%}")

    # 3. 合成 alpha：背景 0，内容 255
    alpha = np.where(bg, 0, 255).astype(np.uint8)

    # 4. 边缘羽化（1px 高斯，消除锯齿）
    alpha_img = Image.fromarray(alpha, mode="L").filter(ImageFilter.GaussianBlur(1.2))
    # 阈值回拉：>200 → 255，<55 → 0，中间保留半透明过渡
    a = np.asarray(alpha_img).astype(np.int32)  # int32 防 200*255 溢出 int16
    a = np.clip((a - 55) * 255 // 145, 0, 255).astype(np.uint8)

    rgba = np.dstack([arr, a])
    out = Image.fromarray(rgba, mode="RGBA")

    # 5. 裁剪到内容 bbox + 4% padding（必须按 alpha 通道算 bbox——
    #    getbbox() 默认看 RGB，白底 RGB=255 非零会导致 bbox=全图）
    bbox = Image.fromarray(a, mode="L").getbbox()
    if bbox:
        x0, y0, x1, y1 = bbox
        bw, bh = x1 - x0, y1 - y0
        side = max(bw, bh)
        pad = int(side * 0.04)
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        half = side // 2 + pad
        x0 = max(0, cx - half)
        y0 = max(0, cy - half)
        x1 = min(w, cx + half)
        y1 = min(h, cy + half)
        out = out.crop((x0, y0, x1, y1))
    print(f"cropped: {out.size}")

    # 6. 保存 logo_main.png（512x512）
    logo512 = out.resize((512, 512), Image.LANCZOS)
    logo512.save(DST_PNG, "PNG")
    print(f"saved: {DST_PNG}")

    # 7. 生成 app_icon.ico（多尺寸）
    ico_src = out.resize((256, 256), Image.LANCZOS)
    ico_src.save(
        DST_ICO, "ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)],
    )
    print(f"saved: {DST_ICO}")


if __name__ == "__main__":
    main()
