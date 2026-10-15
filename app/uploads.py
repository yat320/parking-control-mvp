"""Videos que sube la gente desde la web ("Probar con tu video").

Cada video se guarda en videos/ como subido-AAAAMMDD-HHMMSS.<ext>, con un .json al
lado que pone la línea virtual según cómo cruzan los autos (ver config.video_settings).
Se guardan solo los últimos config.UPLOAD_KEEP: es una demo compartida.
"""
import json
import time
from pathlib import Path

import cv2

from . import config

ALLOWED = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
PREFIX = "subido-"

# Línea y sentido según cómo cruzan los autos en el video.
#   horizontal: van de arriba hacia abajo (o al revés); bajar es entrada.
#   vertical:   van de costado; ir hacia la derecha es entrada.
LINES = {
    "horizontal": ([0.05, 0.55, 0.95, 0.55], "down", "autos de arriba hacia abajo"),
    "vertical": ([0.5, 0.95, 0.5, 0.05], "down", "autos de costado"),
}


class UploadError(ValueError):
    """Un video que no se puede usar; el mensaje se muestra tal cual en la web."""


def save(fileobj, filename: str | None, orientacion: str, invertir: bool, folder: Path | None = None,
         patentes: bool = False) -> str:
    """Guarda el video subido y su .json. Devuelve el nombre del archivo en videos/."""
    folder = Path(folder or config.VIDEO_PATH.parent)
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise UploadError(f"Ese archivo no es un video que se pueda leer. Formatos: {', '.join(sorted(ALLOWED))}.")
    if orientacion not in LINES:
        raise UploadError("Elegí cómo cruzan los autos: de arriba hacia abajo o de costado.")
    folder.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest, n = folder / f"{PREFIX}{stamp}{ext}", 1
    while dest.exists():
        n += 1
        dest = folder / f"{PREFIX}{stamp}-{n}{ext}"

    limit = config.UPLOAD_MAX_MB * 1024 * 1024
    size = 0
    try:
        with open(dest, "wb") as out:
            while chunk := fileobj.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise UploadError(f"El video pesa más de {config.UPLOAD_MAX_MB} MB. Grabá uno más corto.")
                out.write(chunk)
        seconds = check_video(dest)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise

    line, direction, how = LINES[orientacion]
    if invertir:
        direction = "up" if direction == "down" else "down"
    length = f", {seconds:.0f} s" if seconds else ""
    frontal = ", lee patentes" if patentes else ""
    dest.with_suffix(".json").write_text(json.dumps({
        "descripcion": f"Video subido ({how}{length}{frontal})",
        "line": line,
        "entry_direction": direction,
        "min_area": config.UPLOAD_MIN_AREA,
        "patentes": bool(patentes),
    }, ensure_ascii=False), encoding="utf-8")
    prune(folder)
    return dest.name


def check_video(path: Path) -> float:
    """Que OpenCV lo pueda leer y que no sea más largo que el máximo. Devuelve los segundos (0 si no se sabe)."""
    cap = cv2.VideoCapture(str(path))
    try:
        ok = cap.isOpened() and cap.read()[0]
        if not ok:
            raise UploadError("No se pudo leer el video. Si es de iPhone, probá en Ajustes → Cámara → Formatos → "
                              "Más compatible, o grabalo de nuevo.")
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    finally:
        cap.release()
    seconds = frames / fps if fps > 0 and frames > 0 else 0.0
    if seconds > config.UPLOAD_MAX_SECONDS:
        raise UploadError(f"El video dura {seconds:.0f} s y el máximo es {config.UPLOAD_MAX_SECONDS} s. "
                          "Recortalo o grabá uno más corto.")
    return seconds


def prune(folder: Path) -> None:
    """Deja solo los últimos UPLOAD_KEEP videos subidos (con sus .json)."""
    subidos = sorted((p for p in folder.glob(PREFIX + "*") if p.suffix.lower() in ALLOWED),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    for old in subidos[config.UPLOAD_KEEP:]:
        old.unlink(missing_ok=True)
        old.with_suffix(".json").unlink(missing_ok=True)
