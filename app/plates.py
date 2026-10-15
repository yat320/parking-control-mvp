"""Lectura de patentes para la cámara frontal (fast-alpr: detector YOLO + OCR, en ONNX, sin GPU).

Cómo se usa en el procesamiento (app/video_processor.py):
  1. cada `config.PLATES_EVERY` frames, si hay autos en cuadro, `PlateReader.read(frame)`
     encuentra las patentes y las lee;
  2. cada lectura se le asigna al auto (track) cuya caja la contiene;
  3. el auto acumula votos (`PlateVotes`): con sus últimas lecturas se queda con la patente
     que más se leyó, pesada por confianza, y solo si tiene formato argentino (ABC123 o AB123CD);
  4. al cruzar la línea, el evento se registra con esa patente y la foto recortada.

Si fast-alpr no está instalado, `create_reader()` devuelve None y todo sigue como antes.
"""
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field

import numpy as np

from . import config

# Formatos argentinos: el viejo (1995-2016) y el Mercosur (desde 2016).
FORMATS = (re.compile(r"^[A-Z]{3}[0-9]{3}$"), re.compile(r"^[A-Z]{2}[0-9]{3}[A-Z]{2}$"))


def normalize(text: str) -> str:
    """Mayúsculas, sin espacios ni guiones: 'ab 123 cd' -> 'AB123CD'."""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def is_valid(plate: str) -> bool:
    return any(f.match(plate) for f in FORMATS)


def pretty(plate: str) -> str:
    """Como se escribe en la chapa: 'ABC 123' o 'AB 123 CD'."""
    if FORMATS[0].match(plate):
        return f"{plate[:3]} {plate[3:]}"
    if FORMATS[1].match(plate):
        return f"{plate[:2]} {plate[2:5]} {plate[5:]}"
    return plate


@dataclass
class PlateRead:
    text: str              # normalizado
    confidence: float      # detección x peor carácter del OCR (0..1)
    box: tuple             # x, y, w, h en el frame que se le pasó a read()
    crop: np.ndarray       # recorte de la patente (BGR)


@dataclass
class PlateVotes:
    """Las últimas lecturas de un auto (config.PLATES_WINDOW); gana la patente que más suma.

    Solo cuentan las últimas porque el tracker a veces le pasa el número de un auto que
    se fue a otro que aparece en el mismo lugar: con las lecturas viejas, el auto nuevo
    heredaría la patente del anterior.
    """
    recent: deque = field(default_factory=lambda: deque(maxlen=config.PLATES_WINDOW))
    reads: int = 0

    def add(self, r: PlateRead) -> bool:
        """Suma una lectura. Devuelve True si cambió la patente que ya estaba firme (es otro auto)."""
        if not is_valid(r.text):
            return False  # 'AB123C' o basura: no cuenta
        before = self.best()
        self.recent.append(r)
        self.reads += 1
        after = self.best()
        return bool(before and after and after != before)

    def _scores(self) -> dict:
        score = defaultdict(float)
        for r in self.recent:
            score[r.text] += r.confidence
        return score

    def best(self) -> str | None:
        score = self._scores()
        if not score:
            return None
        plate, total = max(score.items(), key=lambda kv: kv[1])
        return plate if total >= config.PLATES_MIN_SCORE else None

    def crop(self, plate: str):
        """El recorte más confiable de esa patente entre las últimas lecturas."""
        reads = [r for r in self.recent if r.text == plate]
        return max(reads, key=lambda r: r.confidence).crop if reads else None


class PlateReader:
    def __init__(self, detector_model: str = None, ocr_model: str = None):
        from fast_alpr import ALPR  # import acá: es opcional y tarda en cargar
        self.alpr = ALPR(detector_model=detector_model or config.PLATES_DETECTOR,
                         ocr_model=ocr_model or config.PLATES_OCR)

    def read(self, frame: np.ndarray) -> list[PlateRead]:
        out = []
        for res in self.alpr.predict(frame):
            if not res.ocr or not res.ocr.text:
                continue
            text = normalize(res.ocr.text)
            chars = res.ocr.confidence
            if isinstance(chars, (list, tuple)):
                chars = min(chars[:len(text)] or [0.0])
            conf = float(res.detection.confidence) * float(chars or 0.0)
            b = res.detection.bounding_box
            x1, y1 = max(0, int(b.x1)), max(0, int(b.y1))
            x2, y2 = min(frame.shape[1], int(b.x2)), min(frame.shape[0], int(b.y2))
            if x2 <= x1 or y2 <= y1:
                continue
            out.append(PlateRead(text, conf, (x1, y1, x2 - x1, y2 - y1), frame[y1:y2, x1:x2].copy()))
        return out


_reader = None
_failed = False


def create_reader() -> PlateReader | None:
    """El lector compartido (los modelos se cargan una sola vez). None si no se puede."""
    global _reader, _failed
    if _reader is None and not _failed:
        try:
            _reader = PlateReader()
        except Exception as e:  # sin fast-alpr, o sin los modelos
            _failed = True
            print(f"[PATENTES] no se pueden leer patentes: {e}", flush=True)
    return _reader


def assign(reads: list[PlateRead], tracks, scale: float = 1.0) -> None:
    """Le da cada lectura al auto cuya caja contiene el centro de la patente.

    `scale` pasa de las coordenadas de las lecturas a las de los tracks (si la
    patente se leyó en el frame original y el tracking corre en uno achicado).
    """
    active = [t for t in tracks.values() if not t.missed]
    for r in reads:
        cx = (r.box[0] + r.box[2] / 2) * scale
        cy = (r.box[1] + r.box[3] / 2) * scale
        best, best_d = None, None
        for t in active:
            d = t.detection
            mx, my = d.w * 0.1, d.h * 0.1  # un poco de margen: la patente va en el borde del auto
            if d.x - mx <= cx <= d.x + d.w + mx and d.y - my <= cy <= d.y + d.h + my:
                dist = (t.centroid[0] - cx) ** 2 + (t.centroid[1] - cy) ** 2
                if best is None or dist < best_d:
                    best, best_d = t, dist
        if best is not None and best.plates.add(r):
            best.pending_plate.clear()  # los eventos sin patente eran del auto anterior
