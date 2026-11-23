"""Export a two-image pair for AI loading-art: original style + track silhouette.

One-track pipeline (Hometown by default):

    python export_loading_ai_pair.py
    python export_loading_ai_pair.py Gt1 --style coastal

Writes into <work>/preview/ai-<track>/ :

    01_style_original.png   NFS5 loading map, contained on a black square
    02_silhouette.png       vroad centre line, same square, white on black
    03_silhouette_game.png  native-size yellow line for later FSH packing
    prompt.txt              what to paste into the image model
"""

import argparse
import math
import os
import sys

from PIL import Image, ImageDraw

import nfs4_frd as frd
import nfs5cfg

GAME = nfs5cfg.GAME
HS_TRACKS = nfs5cfg.HS_TRACKS
PREVIEW = os.path.join(nfs5cfg.WORK, 'preview')
STYLE_SRC = os.path.join(PREVIEW, '1_original_nfs5_loading_coastal.png')

AI_SIDE = 1024
GAME_LONG = 280
MARGIN_FRAC = 0.08


def project_points(track):
    pts = [track.vroad(i)['ref'] for i in range(track.n_vroad)]
    xs = [p[0] for p in pts]
    zs = [p[2] for p in pts]
    return pts, min(xs), max(xs), min(zs), max(zs)


def canvas_for_aspect(span_x, span_z, long_side):
    if span_x >= span_z:
        w = long_side
        h = max(1, round(long_side * span_z / span_x))
    else:
        h = long_side
        w = max(1, round(long_side * span_x / span_z))
    return w, h


def to_screen(pts, min_x, min_z, span_x, span_z, w, h, margin):
    scale = min((w - 2 * margin) / span_x, (h - 2 * margin) / span_z)
    ox = (w - span_x * scale) / 2 - min_x * scale
    oy = (h - span_z * scale) / 2 - min_z * scale
    screen = []
    for p in pts:
        x = p[0] * scale + ox
        y = h - (p[2] * scale + oy)
        screen.append((x, y))
    return screen


def draw_line(w, h, screen, colour, width, closed=True):
    """Draw an anti-aliased polyline on a transparent image, then composite."""
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    xy = list(screen)
    if closed and len(xy) > 2:
        xy = xy + [xy[0]]
    draw.line(xy, fill=colour, width=width, joint='curve')
    r = max(1, width // 2)
    x0, y0 = screen[0]
    draw.ellipse((x0 - r, y0 - r, x0 + r, y0 + r), fill=colour)
    return img


def paste_centered(dst, src):
    x = (dst.size[0] - src.size[0]) // 2
    y = (dst.size[1] - src.size[1]) // 2
    dst.paste(src, (x, y), src if src.mode == 'RGBA' else None)


def contain(src, box_w, box_h):
    src = src.convert('RGBA')
    scale = min(box_w / src.size[0], box_h / src.size[1])
    nw = max(1, round(src.size[0] * scale))
    nh = max(1, round(src.size[1] * scale))
    return src.resize((nw, nh), Image.Resampling.LANCZOS)


def prompt_text(track_name):
    return (
        'Paste BOTH images. Image 1 = style original. Image 2 = silhouette (shape only).\n'
        '\n'
        'PROMPT (English, copy as-is):\n'
        '\n'
        'Use image 1 only for visual style. Use image 2 only for the track path.\n'
        'Redraw the EXACT route from image 2 as a single continuous racing-line, '
        'in the style of image 1.\n'
        '\n'
        'Must match image 1:\n'
        '- pure black background, no map, no roads, no terrain, no car, no UI\n'
        '- one thick golden-yellow centre line (about the same thickness as image 1)\n'
        '- soft anti-aliased edge, slight darker-gold outline, no glow bloom\n'
        '- no text, no flags, no start marker, no compass, no grid\n'
        '\n'
        'Must match image 2:\n'
        '- keep the silhouette\'s proportions and orientation\n'
        '- do not simplify, smooth out, or invent extra loops\n'
        '- do not copy the shape from image 1\n'
        '\n'
        'Output: square PNG, black background, only the yellow line. Same framing as image 2.\n'
        '\n'
        '---\n'
        '\n'
        'If the model ignores the silhouette, add:\n'
        'The path in image 2 is authoritative. Image 1 is a paint-over reference '
        'for colour and line treatment only.\n'
    )


def export(track_name, style_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    track = frd.read(os.path.join(HS_TRACKS, track_name, 'tr.frd'))
    pts, min_x, max_x, min_z, max_z = project_points(track)
    span_x = (max_x - min_x) or 1.0
    span_z = (max_z - min_z) or 1.0
    closed = math.hypot(pts[0][0] - pts[-1][0], pts[0][2] - pts[-1][2]) < 20.0

    # Native packing size: long side like stock circuit maps (~260-280).
    gw, gh = canvas_for_aspect(span_x, span_z, GAME_LONG)
    g_margin = max(8, round(min(gw, gh) * MARGIN_FRAC))
    g_screen = to_screen(pts, min_x, min_z, span_x, span_z, gw, gh, g_margin)
    game = Image.new('RGBA', (gw, gh), (0, 0, 0, 255))
    # ~5 px at coastal 257 matches the original yellow stroke.
    game.alpha_composite(draw_line(gw, gh, g_screen, (232, 176, 32, 255), 5, closed))
    game_path = os.path.join(out_dir, '03_silhouette_game.png')
    game.save(game_path)

    # AI square: same framing, white silhouette (reads clearer than yellow).
    square = Image.new('RGBA', (AI_SIDE, AI_SIDE), (0, 0, 0, 255))
    inner = AI_SIDE - 2 * round(AI_SIDE * MARGIN_FRAC)
    aw, ah = canvas_for_aspect(span_x, span_z, inner)
    a_margin = max(12, round(min(aw, ah) * 0.04))
    a_screen = to_screen(pts, min_x, min_z, span_x, span_z, aw, ah, a_margin)
    sil = Image.new('RGBA', (aw, ah), (0, 0, 0, 255))
    sil.alpha_composite(draw_line(aw, ah, a_screen, (255, 255, 255, 255), 14, closed))
    paste_centered(square, sil)
    sil_path = os.path.join(out_dir, '02_silhouette.png')
    square.save(sil_path)

    style_src = Image.open(style_path)
    style = Image.new('RGBA', (AI_SIDE, AI_SIDE), (0, 0, 0, 255))
    fitted = contain(style_src, inner, inner)
    paste_centered(style, fitted)
    style_out = os.path.join(out_dir, '01_style_original.png')
    style.save(style_out)

    prompt_path = os.path.join(out_dir, 'prompt.txt')
    with open(prompt_path, 'w', encoding='utf-8') as f:
        f.write(prompt_text(track_name))

    print('track %s  vroad %d  bbox %.0fx%.0f m  game %dx%d  ai %dx%d' % (
        track_name, track.n_vroad, span_x, span_z, gw, gh, AI_SIDE, AI_SIDE))
    print(style_out)
    print(sil_path)
    print(game_path)
    print(prompt_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('track', nargs='?', default='Hometown')
    ap.add_argument('--style', default=STYLE_SRC)
    args = ap.parse_args()
    out = os.path.join(PREVIEW, 'ai-%s' % args.track.lower())
    export(args.track, args.style, out)


if __name__ == '__main__':
    sys.exit(main())
