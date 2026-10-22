"""Genera un video de prueba para la cámara frontal: autos de frente, con patente.

Cámara fija a la altura de la barrera, mirando hacia afuera: un auto que se acerca
baja en la imagen (entra) y uno que se va sube (sale). Las patentes son dibujadas:
sirven para probar el circuito entero (leer, votar, emparejar la salida por patente),
no para medir cuánto acierta el lector con chapas reales.

Entran AB123CD, ABC123 y AE905KT, y salen en otro orden (ABC123, AB123CD, AE905KT):
por orden de llegada se cobraría al auto equivocado; por patente, al correcto.

    python -m app.demo_plates --out videos/patentes_prueba.mp4
    python -m app.demo_plates --par videos/            # dos cámaras: barrera + frontal
"""
import json
import argparse
from pathlib import Path

import cv2
import numpy as np

W, H, FPS = 960, 540, 20
CAR_W, CAR_H = 330, 230
SPEED = 170  # px/s
# Las chapas dibujadas no son como las reales: el OCR argentino (entrenado con chapas de verdad)
# las lee mal y el global bien, y el detector de 640 no las encuentra y el de 384 sí. Los videos
# de prueba piden esos dos; con chapas reales rinden más los de config.py.
DRAWN_OCR = "cct-xs-v1-global-model"
DRAWN_DETECTOR = "yolo-v9-t-384-license-plate-end2end"
LANE_DX = 215  # con carriles: centro de cada auto respecto del medio
PLATE_W, PLATE_H = 100, 32  # ~10% del ancho: el detector, entrenado con fotos reales, no ve chapas mucho más grandes

# (segundo, sentido, patente, color del auto BGR). "down" = entra, "up" = sale.
SCHEDULE = [
    (1.0, "down", "AB123CD", (40, 40, 190)),
    (6.0, "down", "ABC123", (180, 110, 40)),
    (11.0, "down", "AE905KT", (200, 200, 200)),
    (16.0, "up", "ABC123", (180, 110, 40)),
    (21.0, "up", "AB123CD", (40, 40, 190)),
    (26.0, "up", "AE905KT", (200, 200, 200)),
]
SECONDS = 31


def plate_image(text: str) -> np.ndarray:
    """Chapa argentina: Mercosur (blanca con franja azul) o vieja (negra con letras blancas)."""
    pw, ph = 200, 64
    mercosur = len(text) == 7
    img = np.full((ph, pw, 3), 255 if mercosur else 25, np.uint8)
    shown = f"{text[:2]} {text[2:5]} {text[5:]}" if mercosur else f"{text[:3]} {text[3:]}"
    if mercosur:
        cv2.rectangle(img, (0, 0), (pw, 14), (150, 60, 0), -1)
        cv2.putText(img, "REPUBLICA ARGENTINA", (52, 11), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (255, 255, 255), 1)
    color = (10, 10, 10) if mercosur else (240, 240, 240)
    (tw, _), _ = cv2.getTextSize(shown, cv2.FONT_HERSHEY_DUPLEX, 1.25, 3)
    cv2.putText(img, shown, ((pw - tw) // 2, 54), cv2.FONT_HERSHEY_DUPLEX, 1.25, color, 3)
    cv2.rectangle(img, (0, 0), (pw - 1, ph - 1), (90, 90, 90), 2)
    return cv2.resize(img, (PLATE_W, PLATE_H), interpolation=cv2.INTER_AREA)


def draw_car(frame: np.ndarray, cx: int, cy: int, color, plate: np.ndarray, scale: float = 1.0) -> None:
    """`scale`: tamaño aparente (perspectiva: más cerca de la cámara, más grande)."""
    car = np.zeros((CAR_H, CAR_W, 3), np.uint8)
    car[:] = color
    cv2.rectangle(car, (35, 18), (CAR_W - 35, 95), (55, 60, 65), -1)          # parabrisas
    cv2.rectangle(car, (18, 120), (75, 150), (210, 230, 240), -1)             # faros
    cv2.rectangle(car, (CAR_W - 75, 120), (CAR_W - 18, 150), (210, 230, 240), -1)
    cv2.rectangle(car, (60, 205), (CAR_W - 60, 225), (30, 30, 30), -1)        # paragolpes
    py, px = 165, (CAR_W - plate.shape[1]) // 2
    car[py:py + plate.shape[0], px:px + plate.shape[1]] = plate
    if scale != 1.0:
        car = cv2.resize(car, (round(CAR_W * scale), round(CAR_H * scale)), interpolation=cv2.INTER_AREA)
    ch, cw = car.shape[:2]
    x0, y0 = cx - cw // 2, cy - ch // 2
    # pegar recortando a los bordes del cuadro
    fx0, fy0 = max(0, x0), max(0, y0)
    fx1, fy1 = min(W, x0 + cw), min(H, y0 + ch)
    if fx1 > fx0 and fy1 > fy0:
        frame[fy0:fy1, fx0:fx1] = car[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0]


def generate(path: Path, seconds: float = SECONDS, lead: float = 0.0, schedule=None, lanes: bool = False) -> Path:
    """`lead`: segundos de más al principio (la cámara empezó a grabar antes).
    `schedule`: lista de (segundo, sentido, patente, color BGR); por defecto, SCHEDULE.
    `lanes`: el que entra va por la izquierda y el que sale por la derecha (si no, por el medio),
    así un auto que entra y otro que sale a la vez no se tapan."""
    schedule = schedule or SCHEDULE
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    from .demo_video import SensorNoise
    rng = np.random.default_rng(7)
    noise = SensorNoise(rng, (H, W, 3), 3)
    base = np.full((H, W, 3), (95, 100, 105), np.uint8)
    cv2.rectangle(base, (0, 0), (W, 110), (150, 140, 120), -1)  # pared al fondo
    plates = {p: plate_image(p) for _, _, p, _ in schedule}
    travel = (H + CAR_H) / SPEED
    for i in range(int((seconds + lead) * FPS)):
        t = i / FPS - lead
        frame = base.copy()
        for start, way, plate, color in schedule:
            k = t - start
            if 0 <= k <= travel:
                cy = -CAR_H // 2 + int(k * SPEED) if way == "down" else H + CAR_H // 2 - int(k * SPEED)
                cx = W // 2 + ((-LANE_DX if way == "down" else LANE_DX) if lanes else 0)
                # Con carriles, perspectiva: el que se acerca (baja) crece y el que se va se achica,
                # como en una cámara real (el emparejamiento usa eso para separar entrada y salida).
                scale = 0.7 + 0.5 * min(1.0, max(0.0, cy / H)) if lanes else 1.0
                draw_car(frame, cx, cy, color, plates[plate], scale)
        out.write(noise.apply(frame, i))
    out.release()
    return path


def generate_pair(folder: Path, lead: float = 0.0) -> Path:
    """Dos cámaras de los mismos autos: par_barrera.mp4 (cenital, cuenta) y par_frontal.mp4
    (lee las patentes), más el .json que las une. Devuelve el video de la barrera.

    `lead`: la frontal empezó a grabar esos segundos antes (queda como "desfase" en el .json).
    """
    from . import demo_video
    folder = Path(folder)
    barrera = folder / "par_barrera.mp4"
    # El mismo auto (mismo color) a la misma hora en las dos cámaras.
    sched = [(start, way, color) for start, way, _plate, color in SCHEDULE]
    demo_video.generate(barrera, SECONDS, schedule=sched)
    generate(folder / "par_frontal.mp4", lead=lead)
    barrera.with_suffix(".json").write_text(json.dumps({
        "descripcion": "Dos cámaras: barrera (cuenta) + frontal (patentes)",
        "frontal": "par_frontal.mp4", "desfase": lead,
        "ocr": DRAWN_OCR, "detector_patentes": DRAWN_DETECTOR}, ensure_ascii=False), encoding="utf-8")
    return barrera


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("videos/patentes_prueba.mp4"))
    ap.add_argument("--par", type=Path, help="carpeta: arma el par de cámaras barrera + frontal")
    ap.add_argument("--desfase", type=float, default=0.0, help="con --par: la frontal empieza N s antes")
    args = ap.parse_args()
    print(generate_pair(args.par, args.desfase) if args.par else generate(args.out))


if __name__ == "__main__":
    main()
