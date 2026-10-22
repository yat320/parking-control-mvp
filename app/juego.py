"""Simulador jugable: la partida del juego (/juego) se convierte en un video de prueba.

El jugador hace entrar autos y elige cuál sale y cuándo. El navegador manda la
lista de autos con sus tiempos; acá se arma el video cenital con el mismo dibujo
que demo_video (así el detector ve lo mismo que en la demo) y se guarda en videos/
como juego-AAAAMMDD-HHMMSS.mp4, listo para procesar como cualquier otro.

Tiempos en segundos de video: `entra` es cuando el auto aparece arriba del cuadro,
`sale` cuando aparece abajo para irse (None si se queda adentro).
"""
import json
import time
from pathlib import Path

from . import config
from .demo_video import CAR_H, CAR_W, ENTRY_X, EXIT_X, H, SPEED, W, generate

PREFIX = "juego-"
KEEP = 3              # partidas guardadas (es una demo compartida)
MAX_SECONDS = 120     # largo máximo del video de una partida
MAX_CARS = 40
TAIL_SECONDS = 5.5    # después del último movimiento, para que el auto termine de cruzar
LINE_Y = (config.LINE[1] + config.LINE[3]) / 2  # altura de la línea, en fracción del alto


class JuegoError(ValueError):
    """Una partida que no se puede armar; el mensaje se muestra en la web."""


def constants() -> dict:
    """Lo que el juego necesita para mover los autos igual que el video."""
    return {"ancho": W, "alto": H, "auto_ancho": CAR_W, "auto_alto": CAR_H, "velocidad": SPEED,
            "carril_entrada": ENTRY_X, "carril_salida": EXIT_X, "linea_y": LINE_Y,
            "max_seg": MAX_SECONDS, "max_autos": MAX_CARS, "cola_seg": TAIL_SECONDS}


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


def build_schedule(autos: list[dict]) -> tuple[list, float]:
    """Lista de autos del juego -> (schedule de demo_video, segundos de video)."""
    if not autos:
        raise JuegoError("La partida no tiene autos.")
    if len(autos) > MAX_CARS:
        raise JuegoError(f"Máximo {MAX_CARS} autos por partida.")
    schedule = []
    for a in autos:
        color = _bgr(a.get("color", "#c83232"))
        entra = float(a["entra"])
        if entra < 0:
            raise JuegoError("Un auto entra antes de empezar la partida.")
        schedule.append((entra, "down", color))
        if a.get("sale") is not None:
            sale = float(a["sale"])
            if sale < entra + crossing_offset("down"):
                raise JuegoError("Un auto sale antes de haber entrado.")
            schedule.append((sale, "up", color))
    schedule.sort(key=lambda s: s[0])
    seconds = schedule[-1][0] + TAIL_SECONDS
    if seconds > MAX_SECONDS:
        raise JuegoError(f"La partida dura más de {MAX_SECONDS} s.")
    return schedule, seconds


def save(autos: list[dict], folder: Path | None = None) -> str:
    """Arma el video de la partida y su .json. Devuelve el nombre del archivo en videos/."""
    schedule, seconds = build_schedule(autos)
    folder = Path(folder or config.VIDEO_PATH.parent)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest, n = folder / f"{PREFIX}{stamp}.mp4", 1
    while dest.exists():
        n += 1
        dest = folder / f"{PREFIX}{stamp}-{n}.mp4"
    generate(dest, seconds, schedule=schedule)
    entradas = sum(1 for s in schedule if s[1] == "down")
    salidas = len(schedule) - entradas
    dest.with_suffix(".json").write_text(json.dumps({
        "descripcion": f"Partida del simulador ({entradas} entradas, {salidas} salidas, {seconds:.0f} s)",
        "line": list(config.LINE), "entry_direction": config.ENTRY_DIRECTION,
    }, ensure_ascii=False), encoding="utf-8")
    prune(folder)
    return dest.name


def prune(folder: Path) -> None:
    partidas = sorted(folder.glob(PREFIX + "*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in partidas[KEEP:]:
        old.unlink(missing_ok=True)
        old.with_suffix(".json").unlink(missing_ok=True)
