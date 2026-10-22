"""Simulador jugable: la partida del juego (/juego) se convierte en un video de prueba.

El jugador hace entrar autos y elige cuál sale y cuándo. El navegador manda la
lista de autos con sus tiempos; acá se arma el video cenital con el mismo dibujo
que demo_video (así el detector ve lo mismo que en la demo) y se guarda en videos/
como juego-AAAAMMDD-HHMMSS.mp4, listo para procesar como cualquier otro.

Tiempos en segundos de video: `entra` es cuando el auto aparece arriba del cuadro,
`sale` cuando aparece abajo para irse (None si se queda adentro).
"""
import json
import os
import re
import time
from pathlib import Path

from . import config, demo_plates
from .demo_video import CAR_H, CAR_W, ENTRY_X, EXIT_X, H, SPEED, W, generate

PREFIX = "juego-"
KEEP = 3              # partidas guardadas (es una demo compartida)
MAX_SECONDS = 120     # largo máximo del video de una partida
MAX_CARS = 40
TAIL_SECONDS = 5.5    # después del último movimiento, para que el auto termine de cruzar
LINE_Y = (config.LINE[1] + config.LINE[3]) / 2  # altura de la línea, en fracción del alto


PLATE_RE = re.compile(r"^([A-Z]{2}\d{3}[A-Z]{2}|[A-Z]{3}\d{3})$")  # AB123CD (Mercosur) o ABC123


class JuegoError(ValueError):
    """Una partida que no se puede armar; el mensaje se muestra en la web."""


def constants() -> dict:
    """Lo que el juego necesita para mover los autos igual que el video."""
    return {"ancho": W, "alto": H, "auto_ancho": CAR_W, "auto_alto": CAR_H, "velocidad": SPEED,
            "carril_entrada": ENTRY_X, "carril_salida": EXIT_X, "linea_y": LINE_Y,
            "max_seg": MAX_SECONDS, "max_autos": MAX_CARS, "cola_seg": TAIL_SECONDS,
            # la cámara frontal (demo_plates), para mostrarla en vivo en el juego
            "frontal": {"ancho": demo_plates.W, "alto": demo_plates.H, "auto_ancho": demo_plates.CAR_W,
                        "auto_alto": demo_plates.CAR_H, "velocidad": demo_plates.SPEED,
                        "carril": demo_plates.LANE_DX}}


def crossing_offset(direction: str) -> float:
    """Segundos desde que el auto aparece hasta que su centro cruza la línea."""
    if direction == "down":
        return (LINE_Y * H + CAR_H / 2) / SPEED
    return (H + CAR_H / 2 - LINE_Y * H) / SPEED


def _bgr(hex_color: str) -> tuple:
    h = str(hex_color).lstrip("#")
    if len(h) != 6:
        raise JuegoError(f"Color inválido: {hex_color!r}")
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise JuegoError(f"Color inválido: {hex_color!r}")
    return (b, g, r)


def build_schedule(autos: list[dict], patentes: bool = False) -> tuple[list, float]:
    """Lista de autos del juego -> (movimientos, segundos de video).

    Cada movimiento es (segundo, sentido, color BGR, patente); "down" entra, "up" sale.
    Con `patentes`, cada auto tiene que traer su patente argentina, distinta de las otras.
    """
    if not autos:
        raise JuegoError("La partida no tiene autos.")
    if len(autos) > MAX_CARS:
        raise JuegoError(f"Máximo {MAX_CARS} autos por partida.")
    schedule, vistas = [], set()
    for a in autos:
        color = _bgr(a.get("color", "#c83232"))
        patente = None
        if patentes:
            patente = str(a.get("patente") or "").replace(" ", "").upper()
            if not PLATE_RE.match(patente):
                raise JuegoError(f"Patente inválida: {a.get('patente')!r} (va AB123CD o ABC123).")
            if patente in vistas:
                raise JuegoError(f"Hay dos autos con la patente {patente}.")
            vistas.add(patente)
        entra = float(a["entra"])
        if entra < 0:
            raise JuegoError("Un auto entra antes de empezar la partida.")
        schedule.append((entra, "down", color, patente))
        if a.get("sale") is not None:
            sale = float(a["sale"])
            if sale < entra + crossing_offset("down"):
                raise JuegoError("Un auto sale antes de haber entrado.")
            schedule.append((sale, "up", color, patente))
    schedule.sort(key=lambda s: s[0])
    seconds = schedule[-1][0] + TAIL_SECONDS
    if seconds > MAX_SECONDS:
        raise JuegoError(f"La partida dura más de {MAX_SECONDS} s.")
    return schedule, seconds


def save(autos: list[dict], folder: Path | None = None, patentes: bool = False) -> str:
    """Arma el video de la partida y su .json. Devuelve el nombre del archivo en videos/.

    Con `patentes`, arma además la cámara frontal (juego-...-frontal.mp4), que ve la chapa
    de cada auto a la misma hora en que pasa por la barrera.
    Los videos se escriben con un nombre oculto y se renombran al terminar: así nunca
    aparece en la lista un video a medio escribir (no se podría abrir).
    """
    schedule, seconds = build_schedule(autos, patentes)
    folder = Path(folder or config.VIDEO_PATH.parent)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest, n = folder / f"{PREFIX}{stamp}.mp4", 1
    while dest.exists():
        n += 1
        dest = folder / f"{PREFIX}{stamp}-{n}.mp4"
    frontal = dest.with_name(dest.stem + "-frontal.mp4") if patentes else None
    temps = []
    try:
        tmp = dest.with_name("." + dest.name)
        temps.append(tmp)
        generate(tmp, seconds, schedule=[(t, way, color) for t, way, color, _ in schedule])
        if frontal:
            ftmp = frontal.with_name("." + frontal.name)
            temps.append(ftmp)
            demo_plates.generate(ftmp, seconds, schedule=[(t, way, p, color) for t, way, color, p in schedule],
                                 lanes=True)
            os.replace(ftmp, frontal)
        os.replace(tmp, dest)
    finally:
        for t in temps:
            t.unlink(missing_ok=True)
    entradas = sum(1 for s in schedule if s[1] == "down")
    salidas = len(schedule) - entradas
    opts = {
        "descripcion": f"Partida del simulador ({entradas} entradas, {salidas} salidas, {seconds:.0f} s"
                       f"{', con cámara frontal' if frontal else ''})",
        "line": list(config.LINE), "entry_direction": config.ENTRY_DIRECTION,
    }
    if frontal:  # las chapas dibujadas las leen mejor estos modelos (ver demo_plates)
        opts.update(frontal=frontal.name, desfase=0.0,
                    ocr=demo_plates.DRAWN_OCR, detector_patentes=demo_plates.DRAWN_DETECTOR)
    dest.with_suffix(".json").write_text(json.dumps(opts, ensure_ascii=False), encoding="utf-8")
    prune(folder)
    return dest.name


def prune(folder: Path) -> None:
    partidas = sorted((p for p in folder.glob(PREFIX + "*.mp4") if not p.stem.endswith("-frontal")),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    for old in partidas[KEEP:]:
        old.unlink(missing_ok=True)
        old.with_suffix(".json").unlink(missing_ok=True)
        old.with_name(old.stem + "-frontal.mp4").unlink(missing_ok=True)
