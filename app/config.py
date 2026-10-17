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

# Para contar un cruce, el auto tiene que venir avanzando hacia ese lado al menos esta fracción
# del ancho del cuadro en sus últimos cuadros (descarta saltos del centro de la mancha).
CROSS_MIN_TRAVEL = float(os.getenv("PARKING_CROSS_MIN_TRAVEL", "0.02"))

# Segundos que la barrera queda abierta después de un evento.
BARRIER_OPEN_SECONDS = float(os.getenv("PARKING_BARRIER_SECONDS", "3"))

# Área mínima (en píxeles) de un blob para considerarlo vehículo.
MIN_AREA = int(os.getenv("PARKING_MIN_AREA", "2500"))
# Memoria del fondo del detector de movimiento, en segundos de video. Un vehículo quieto
# pasa a ser fondo después de ~1/10 de esto: con 150, a los ~15 s (el que espera la
# barrera o saca el ticket no desaparece).
BG_SECONDS = float(os.getenv("PARKING_FONDO_SEGUNDOS", "150"))

# Escala de tiempo de la demo: cuántos segundos "reales" de estadía representa
# cada segundo de video. Con 60, un auto que entra y sale 20 s después en el
# video cuenta como 20 minutos de estadía. Con 1, el tiempo es literal
# (lo correcto para una cámara real).
TIME_SCALE = float(os.getenv("PARKING_TIME_SCALE", "60"))

# Los videos más anchos que esto se achican antes de detectar (los de celular vienen en
# 1080p o 4K y en un servidor chico irían lentísimo). 0 = no achicar.
PROCESS_MAX_WIDTH = int(os.getenv("PARKING_MAX_WIDTH", "640"))

# "Probar con tu video": límites de lo que se puede subir desde la web.
UPLOAD_MAX_MB = int(os.getenv("PARKING_UPLOAD_MB", "80"))
UPLOAD_MAX_SECONDS = int(os.getenv("PARKING_UPLOAD_SECONDS", "90"))
UPLOAD_KEEP = int(os.getenv("PARKING_UPLOAD_KEEP", "5"))       # se guardan solo los últimos
UPLOAD_MIN_AREA = int(os.getenv("PARKING_UPLOAD_MIN_AREA", "1500"))  # en el video ya achicado

# Patentes (cámara frontal). Se leen en los videos cuyo .json dice "patentes": true, o en
# todos con PARKING_PLATES=1. Hace falta fast-alpr (requirements.txt); sin eso, no se leen.
PLATES = os.getenv("PARKING_PLATES", "0") == "1"
PLATES_EVERY = int(os.getenv("PARKING_PLATES_EVERY", "3"))          # leer cada N frames
PLATES_MIN_SCORE = float(os.getenv("PARKING_PLATES_MIN_SCORE", "0.5"))  # suma de confianzas para aceptar una patente
PLATES_WINDOW = int(os.getenv("PARKING_PLATES_WINDOW", "6"))       # lecturas recientes que votan (~1 s)
PLATES_LATE_SECONDS = float(os.getenv("PARKING_PLATES_LATE", "3"))  # si se lee después de cruzar, hasta cuándo vale
# Detector de 640: encuentra chapas más chicas (cámara más lejos) que el de 384, a ~76 ms por
# cuadro de 1280x720 en vez de ~36 (y se lee uno de cada PLATES_EVERY cuadros).
PLATES_DETECTOR = os.getenv("PARKING_PLATES_DETECTOR", "yolo-v9-t-640-license-plate-end2end")
# OCR entrenado con patentes argentinas: 96,6% de aciertos leyendo una sola vez 735 chapas reales
# (contra 92,8% del global "cct-xs-v1-global-model"), y ~2 ms por chapa.
PLATES_OCR = os.getenv("PARKING_PLATES_OCR", "argentinian-plates-cnn-synth-model")
# Dos cámaras (la de la barrera cuenta, la frontal lee patentes): cuándo una patente vista
# por la frontal corresponde a un evento de la barrera.
# Con la frontal más afuera, el auto se ve hasta ~10 s antes de cruzar (frena, espera la barrera
# y arranca); el que sale se ve recién después de cruzar, alejándose.
PAIR_BEFORE = float(os.getenv("PARKING_PAIR_BEFORE", "15"))  # vista hasta 15 s antes del evento
PAIR_AFTER = float(os.getenv("PARKING_PAIR_AFTER", "4"))     # o hasta 4 s después
PAIR_GAP_SECONDS = float(os.getenv("PARKING_PAIR_GAP", "2"))  # lecturas a menos de esto son la misma pasada
# Si la chapa crece más que esto (o se achica), el auto se acerca (o se aleja) de la frontal.
# Una entrada no se lleva una chapa que se aleja ni una salida una que se acerca. 0 = no mirar.
PAIR_TREND = float(os.getenv("PARKING_PAIR_TREND", "1.15"))
# La frontal mira hacia afuera (ve llegar a los que entran). Si mira hacia adentro, PARKING_FRONTAL_ADENTRO=1.
FRONTAL_LOOKS_IN = os.getenv("PARKING_FRONTAL_ADENTRO", "0") == "1"
PLATES_DIR = Path(os.getenv("PARKING_PLATES_DIR", BASE_DIR / "data" / "patentes"))  # fotos recortadas

# Tarifa por defecto (se puede cambiar desde la web; queda guardada en SQLite).
DEFAULT_RATE_PER_HOUR = float(os.getenv("PARKING_RATE", "1000"))
DEFAULT_FRACTION_MINUTES = int(os.getenv("PARKING_FRACTION", "15"))


def video_settings(video_path: Path, defaults: dict) -> dict:
    """Ajustes propios de un video, leídos de un .json con el mismo nombre.

    Ejemplo de videos/calle_real.json:
        {"line": [0.5, 0.95, 0.5, 0.05], "entry_direction": "down", "min_area": 300}
    Con "patentes": true, es una cámara frontal y se leen las patentes; "ocr" y
    "detector_patentes" eligen otros modelos para ese video (los de las chapas dibujadas).
    Con "frontal": "otro.mp4", ese video es la cámara frontal de este (las patentes se
    leen ahí) y "desfase": segundos que la frontal empezó a grabar antes (negativo: después).
    Lo que no esté en el archivo queda con el valor de `defaults`
    (claves: line, entry_sign, min_area, plates, frontal, offset).
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
        if "patentes" in data:
            opts["plates"] = bool(data["patentes"])
        if data.get("ocr"):
            opts["ocr"] = str(data["ocr"])
        if data.get("detector_patentes"):
            opts["plate_detector"] = str(data["detector_patentes"])
        if data.get("frontal"):
            opts["frontal"] = sidecar.parent / Path(str(data["frontal"])).name  # siempre en la misma carpeta
            opts["offset"] = float(data.get("desfase", 0))
    return opts
