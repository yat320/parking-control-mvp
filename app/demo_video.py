"""Genera videos/video_test.mp4: una cámara cenital fija sobre la entrada de una cochera.

Sirve para probar el MVP sin tener un video real. Los autos que bajan por el
carril izquierdo entran; los que suben por el derecho salen. Se agrega ruido
de sensor y un leve parpadeo de luz para que la detección no sea trivial.

    python tools/generate_test_video.py            # 40 s, 640x360, 20 fps
    python tools/generate_test_video.py --out otro.mp4 --seconds 60

La web y la consola lo generan solas si no existe videos/video_test.mp4.
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

from . import config

W, H, FPS = 640, 360, 20
CAR_W, CAR_H = 86, 150
SPEED = 110  # px/s
ENTRY_X, EXIT_X = 250, 390  # centro de cada carril

# (segundo en que aparece, sentido, color BGR). "down" = entra, "up" = sale.
SCHEDULE = [
    (2.0, "down", (40, 40, 200)),     # auto rojo entra
    (8.0, "down", (190, 110, 30)),    # auto azul entra
    (15.0, "up", (40, 40, 200)),      # rojo sale
    (21.0, "down", (225, 225, 225)),  # blanco entra
    (28.0, "up", (190, 110, 30)),     # azul sale
    (34.0, "up", (225, 225, 225)),    # blanco sale
]


def make_background(rng: np.random.Generator) -> np.ndarray:
    bg = np.full((H, W, 3), (70, 110, 60), np.uint8)  # pasto
    cv2.rectangle(bg, (170, 0), (470, H), (85, 85, 85), -1)  # asfalto
    noise = rng.normal(0, 6, (H, 300, 1)).astype(np.int16)
    bg[:, 170:470] = np.clip(bg[:, 170:470].astype(np.int16) + noise, 0, 255).astype(np.uint8)
    cv2.line(bg, (320, 0), (320, H), (210, 210, 210), 2)  # división de carriles
    for y in range(0, H, 40):
        cv2.line(bg, (320, y), (320, y + 20), (85, 85, 85), 2)
    cv2.rectangle(bg, (490, 150), (600, 240), (150, 150, 160), -1)  # garita
    cv2.rectangle(bg, (500, 160), (590, 190), (190, 160, 120), -1)
    cv2.putText(bg, "CABINA", (505, 225), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (40, 40, 40), 1)
    cv2.putText(bg, "ENTRADA", (205, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1)
    cv2.putText(bg, "SALIDA", (355, H - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1)
    return bg


def draw_car(frame, cx, cy, color, facing_down):
    x1, y1 = int(cx - CAR_W / 2), int(cy - CAR_H / 2)
    x2, y2 = x1 + CAR_W, y1 + CAR_H
    for wy in (y1 + 22, y2 - 38):  # ruedas
        cv2.rectangle(frame, (x1 - 5, wy), (x1 + 6, wy + 22), (20, 20, 20), -1)
        cv2.rectangle(frame, (x2 - 6, wy), (x2 + 5, wy + 22), (20, 20, 20), -1)
    cv2.rectangle(frame, (x1, y1 + 8), (x2, y2 - 8), color, -1)
    cv2.rectangle(frame, (x1 + 8, y1), (x2 - 8, y2), color, -1)
    shade = tuple(int(c * 0.55) for c in color)
    front = y2 - 45 if facing_down else y1 + 25
    back = y1 + 20 if facing_down else y2 - 40
    cv2.rectangle(frame, (x1 + 10, front), (x2 - 10, front + 20), (60, 50, 40), -1)  # parabrisas
    cv2.rectangle(frame, (x1 + 12, back), (x2 - 12, back + 18), (60, 50, 40), -1)    # luneta
    cv2.rectangle(frame, (x1 + 12, min(front, back) + 22), (x2 - 12, max(front, back) - 4), shade, -1)  # techo


def generate(out: Path, seconds: float, seed: int = 7) -> None:
    rng = np.random.default_rng(seed)
    bg = make_background(rng)
    out.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for i in range(int(seconds * FPS)):
        t = i / FPS
        frame = bg.copy()
        for start, direction, color in SCHEDULE:
            dt = t - start
            if dt < 0:
                continue
            travel = dt * SPEED
            if direction == "down":
                cx, cy = ENTRY_X, -CAR_H / 2 + travel
            else:
                cx, cy = EXIT_X, H + CAR_H / 2 - travel
            if -CAR_H < cy < H + CAR_H:
                draw_car(frame, cx, cy, color, direction == "down")
        # Ruido de sensor + leve cambio de brillo, como una cámara barata.
        flicker = 3 * np.sin(t * 1.3)
        noise = rng.normal(flicker, 3, frame.shape).astype(np.int16)
        frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        writer.write(frame)
    writer.release()
    print(f"Video generado: {out} ({seconds:.0f} s, {W}x{H} @ {FPS} fps, "
          f"{sum(1 for s in SCHEDULE if s[1] == 'down')} entradas y {sum(1 for s in SCHEDULE if s[1] == 'up')} salidas)")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=config.VIDEO_PATH)
    p.add_argument("--seconds", type=float, default=40)
    args = p.parse_args()
    generate(args.out, args.seconds)


if __name__ == "__main__":
    main()
