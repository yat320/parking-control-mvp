"""Simulador jugable: la partida del juego (/juego) se convierte en un video de prueba.

El jugador hace entrar autos y elige cuál sale y cuándo. El navegador manda la
lista de autos con sus tiempos; acá se arma el video cenital con el mismo dibujo
que demo_video (así el detector ve lo mismo que en la demo) y se guarda en videos/
como juego-AAAAMMDD-HHMMSS.mp4, listo para procesar como cualquier otro.

Tiempos en segundos de video: `entra` es cuando el auto aparece arriba del cuadro,
`sale` cuando aparece abajo para irse (None si se queda adentro).

Modo cabina (/cabina): con `espera`, el auto frena antes de la línea y se queda quieto
hasta `abre_entrada` (o `abre_salida`), el segundo en que el jugador le abrió la barrera;
sin apertura, espera hasta el final del video.
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
CABINA_SECONDS = 90   # largo de un turno en el modo cabina
LINE_Y = (config.LINE[1] + config.LINE[3]) / 2  # altura de la línea, en fracción del alto
STOP_GAP = 12         # px entre la trompa del auto que espera y la línea (la barrera)
NEVER = float("inf")


PLATE_RE = re.compile(r"^([A-Z]{2}\d{3}[A-Z]{2}|[A-Z]{3}\d{3})$")  # AB123CD (Mercosur) o ABC123


class JuegoError(ValueError):
    """Una partida que no se puede armar; el mensaje se muestra en la web."""


def constants() -> dict:
    """Lo que el juego necesita para mover los autos igual que el video."""
    return {"ancho": W, "alto": H, "auto_ancho": CAR_W, "auto_alto": CAR_H, "velocidad": SPEED,
            "carril_entrada": ENTRY_X, "carril_salida": EXIT_X, "linea_y": LINE_Y,
            "max_seg": MAX_SECONDS, "max_autos": MAX_CARS, "cola_seg": TAIL_SECONDS, "frena_px": STOP_GAP,
            # la cámara frontal (demo_plates), para mostrarla en vivo en el juego
            "frontal": {"ancho": demo_plates.W, "alto": demo_plates.H, "auto_ancho": demo_plates.CAR_W,
                        "auto_alto": demo_plates.CAR_H, "velocidad": demo_plates.SPEED,
                        "carril": demo_plates.LANE_DX}}


def crossing_offset(direction: str) -> float:
    """Segundos desde que el auto aparece hasta que su centro cruza la línea."""
    if direction == "down":
        return (LINE_Y * H + CAR_H / 2) / SPEED
    return (H + CAR_H / 2 - LINE_Y * H) / SPEED


def stop_offset(direction: str) -> float:
    """Segundos desde que el auto aparece hasta que frena frente a la barrera."""
    if direction == "down":
        return (LINE_Y * H - STOP_GAP) / SPEED
    return (H - LINE_Y * H - STOP_GAP) / SPEED


def _pause(start: float, opens, direction: str, kind: str):
    """(llega, espera) del auto que frena; `opens` es cuándo le abrieron (None: nunca)."""
    if opens is None:
        return (stop_offset(direction), NEVER)
    opens = float(opens)
    if opens < start:
        raise JuegoError(f"Una barrera de {kind} se abrió antes de que llegara el auto.")
    return (stop_offset(direction), max(0.0, opens - start - stop_offset(direction)))


def _bgr(hex_color: str) -> tuple:
    h = str(hex_color).lstrip("#")
    if len(h) != 6:
        raise JuegoError(f"Color inválido: {hex_color!r}")
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise JuegoError(f"Color inválido: {hex_color!r}")
    return (b, g, r)


def build_schedule(autos: list[dict], patentes: bool = False, duracion: float | None = None) -> tuple[list, float]:
    """Lista de autos del juego -> (movimientos, segundos de video).

    Cada movimiento es (segundo, sentido, color BGR, patente, pausa); "down" entra, "up" sale.
    `pausa` es None o (llega, espera) para los autos que frenan en la barrera (modo cabina).
    Con `patentes`, cada auto tiene que traer su patente argentina, distinta de las otras.
    `duracion`: largo mínimo del video (en modo cabina, cuando terminó la partida; así se ven
    los autos que quedaron esperando).
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
        espera = bool(a.get("espera"))
        pin = _pause(entra, a.get("abre_entrada"), "down", "entrada") if espera else None
        schedule.append((entra, "down", color, patente, pin))
        if a.get("sale") is not None:
            sale = float(a["sale"])
            if pin and pin[1] == NEVER:
                raise JuegoError("Un auto sale sin que le hayan abierto para entrar.")
            if sale < entra + crossing_offset("down") + (pin[1] if pin else 0):
                raise JuegoError("Un auto sale antes de haber entrado.")
            pout = _pause(sale, a.get("abre_salida"), "up", "salida") if espera else None
            schedule.append((sale, "up", color, patente, pout))
    schedule.sort(key=lambda s: s[0])
    # El video sigue hasta que el último auto que se movió termine de cruzar.
    ends = [s[0] + (s[4][1] if s[4] else 0) for s in schedule if not (s[4] and s[4][1] == NEVER)]
    seconds = max((max(ends) + TAIL_SECONDS) if ends else 0, float(duracion or 0), schedule[-1][0] + TAIL_SECONDS)
    if seconds > MAX_SECONDS:
        raise JuegoError(f"La partida dura más de {MAX_SECONDS} s.")
    return schedule, seconds


def save(autos: list[dict], folder: Path | None = None, patentes: bool = False,
         duracion: float | None = None) -> str:
    """Arma el video de la partida y su .json. Devuelve el nombre del archivo en videos/.

    Con `patentes`, arma además la cámara frontal (juego-...-frontal.mp4), que ve la chapa
    de cada auto a la misma hora en que pasa por la barrera.
    Los videos se escriben con un nombre oculto y se renombran al terminar: así nunca
    aparece en la lista un video a medio escribir (no se podría abrir).
    """
    schedule, seconds = build_schedule(autos, patentes, duracion)
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
        generate(tmp, seconds, schedule=[(t, way, color, pause) for t, way, color, _, pause in schedule])
        if frontal:
            ftmp = frontal.with_name("." + frontal.name)
            temps.append(ftmp)
            demo_plates.generate(ftmp, seconds, lanes=True,
                                 schedule=[(t, way, p, color, pause) for t, way, color, p, pause in schedule])
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
        opts.update(frontal=frontal.name, desfase=0.0, frontal_carriles="entrada-izquierda",
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
