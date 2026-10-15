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


FRONTAL = "-frontal"  # subido-...-frontal.mp4: la cámara frontal de subido-....mp4


def save(fileobj, filename: str | None, orientacion: str, invertir: bool, folder: Path | None = None,
         patentes: bool = False, frontal=None, frontal_name: str | None = None, desfase: float = 0.0) -> str:
    """Guarda el video subido y su .json. Devuelve el nombre del archivo en videos/.

    Con `frontal` (otro archivo abierto), ese es la cámara frontal de este: se guarda al
    lado y las patentes se leen ahí; `desfase` = segundos que la frontal empezó antes.
    """
    folder = Path(folder or config.VIDEO_PATH.parent)
    ext = Path(filename or "").suffix.lower()
    fext = Path(frontal_name or "").suffix.lower()
    if ext not in ALLOWED or (frontal is not None and fext not in ALLOWED):
        raise UploadError(f"Ese archivo no es un video que se pueda leer. Formatos: {', '.join(sorted(ALLOWED))}.")
    if orientacion not in LINES:
        raise UploadError("Elegí cómo cruzan los autos: de arriba hacia abajo o de costado.")
    if abs(desfase) > config.UPLOAD_MAX_SECONDS:
        raise UploadError("El desfase entre las cámaras no puede ser más largo que el video.")
    folder.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest, n = folder / f"{PREFIX}{stamp}{ext}", 1
    while dest.exists():
        n += 1
        dest = folder / f"{PREFIX}{stamp}-{n}{ext}"
    fdest = dest.with_name(dest.stem + FRONTAL + fext) if frontal is not None else None

    written = []
    try:
        seconds = _write(fileobj, dest, written)
        if fdest:
            try:
                _write(frontal, fdest, written)
            except UploadError as e:
                raise UploadError(f"Cámara frontal: {e}")
    except BaseException:
        for p in written:
            p.unlink(missing_ok=True)
        raise

    line, direction, how = LINES[orientacion]
    if invertir:
        direction = "up" if direction == "down" else "down"
    length = f", {seconds:.0f} s" if seconds else ""
    extra = ", con cámara frontal" if fdest else ", lee patentes" if patentes else ""
    opts = {
        "descripcion": f"Video subido ({how}{length}{extra})",
        "line": line,
        "entry_direction": direction,
        "min_area": config.UPLOAD_MIN_AREA,
        "patentes": bool(patentes) and not fdest,
    }
    if fdest:
        opts.update(frontal=fdest.name, desfase=float(desfase))
    dest.with_suffix(".json").write_text(json.dumps(opts, ensure_ascii=False), encoding="utf-8")
    prune(folder)
    return dest.name


def _write(fileobj, dest: Path, written: list) -> float:
    """Copia el archivo subido con tope de tamaño y lo valida. Devuelve los segundos."""
    limit = config.UPLOAD_MAX_MB * 1024 * 1024
    size = 0
    written.append(dest)
    with open(dest, "wb") as out:
        while chunk := fileobj.read(1 << 20):
            size += len(chunk)
            if size > limit:
                raise UploadError(f"El video pesa más de {config.UPLOAD_MAX_MB} MB. Grabá uno más corto.")
            out.write(chunk)
    return check_video(dest)


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
    """Deja solo los últimos UPLOAD_KEEP videos subidos (con sus .json y su cámara frontal)."""
    subidos = sorted((p for p in folder.glob(PREFIX + "*")
                      if p.suffix.lower() in ALLOWED and not p.stem.endswith(FRONTAL)),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    for old in subidos[config.UPLOAD_KEEP:]:
        for f in folder.glob(old.stem + FRONTAL + ".*"):
            f.unlink(missing_ok=True)
        old.unlink(missing_ok=True)
        old.with_suffix(".json").unlink(missing_ok=True)
