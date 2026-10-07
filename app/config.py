"""Configuración central del MVP.

Todo se puede cambiar con variables de entorno, así no hace falta tocar código
para probar con otro video u otra línea virtual.
"""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DB_PATH = Path(os.getenv("PARKING_DB", BASE_DIR / "data" / "parking.db"))
VIDEO_PATH = Path(os.getenv("PARKING_VIDEO", BASE_DIR / "videos" / "video_test.mp4"))
OUTPUT_PATH = Path(os.getenv("PARKING_OUTPUT", BASE_DIR / "output" / "processed_video.mp4"))

# Detector a usar: "motion" (sustracción de fondo, sin dependencias extra) o "yolo".
DETECTOR = os.getenv("PARKING_DETECTOR", "motion")

# Línea virtual, en fracciones del ancho/alto del frame (0..1), así sirve para
# cualquier resolución. Por defecto: horizontal, a la altura de la barrera.
# Formato: "x1,y1,x2,y2".
LINE = tuple(float(v) for v in os.getenv("PARKING_LINE", "0.05,0.55,0.95,0.55").split(","))

# Sentido de la línea: cruzarla hacia abajo es ENTRADA y hacia arriba SALIDA.
# Con "up" se invierte (si la cámara mira al revés).
ENTRY_DIRECTION = os.getenv("PARKING_ENTRY_DIRECTION", "down")

# Segundos que la barrera queda abierta después de un evento.
BARRIER_OPEN_SECONDS = float(os.getenv("PARKING_BARRIER_SECONDS", "3"))

# Área mínima (en píxeles) de un blob para considerarlo vehículo.
MIN_AREA = int(os.getenv("PARKING_MIN_AREA", "2500"))

# Escala de tiempo de la demo: cuántos segundos "reales" de estadía representa
# cada segundo de video. Con 60, un auto que entra y sale 20 s después en el
# video cuenta como 20 minutos de estadía. Con 1, el tiempo es literal
# (lo correcto para una cámara real).
TIME_SCALE = float(os.getenv("PARKING_TIME_SCALE", "60"))

# Tarifa por defecto (se puede cambiar desde la web; queda guardada en SQLite).
DEFAULT_RATE_PER_HOUR = float(os.getenv("PARKING_RATE", "1000"))
DEFAULT_FRACTION_MINUTES = int(os.getenv("PARKING_FRACTION", "15"))
# Minutos sin cargo y tope por cada 24 h. 0 = desactivado.
DEFAULT_TOLERANCE_MINUTES = int(os.getenv("PARKING_TOLERANCE", "0"))
DEFAULT_DAILY_CAP = float(os.getenv("PARKING_DAILY_CAP", "0"))


def video_settings(video_path: Path, defaults: dict) -> dict:
    """Ajustes propios de un video, leídos de un .json con el mismo nombre.

    Ejemplo de videos/calle_real.json:
        {"line": [0.5, 0.95, 0.5, 0.05], "entry_direction": "down", "min_area": 300}
    Lo que no esté en el archivo queda con el valor de `defaults`
    (claves: line, entry_sign, min_area).
    """
    opts = dict(defaults)
    sidecar = Path(video_path).with_suffix(".json")
    if sidecar.exists():
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        if "line" in data:
            opts["line"] = tuple(float(v) for v in data["line"])
        if "entry_direction" in data:
            opts["entry_sign"] = 1 if data["entry_direction"] == "down" else -1
        if "min_area" in data:
            opts["min_area"] = int(data["min_area"])
    return opts
