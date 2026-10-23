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
import os
import re
import threading
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


def one_off(a: str, b: str) -> bool:
    """Misma patente salvo una letra o número (AA310WF / AA310VF): un error típico del OCR."""
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1


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
                         ocr_model=ocr_model or config.PLATES_OCR,
                         detector_sess_options=_session_options(), ocr_sess_options=_session_options())

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


def _session_options():
    """Hilos de los modelos: en un servidor de menos de un procesador (Render), varios hilos
    se pisan entre sí y frenan la detección; con PARKING_PLATES_THREADS se suben."""
    import onnxruntime as ort
    o = ort.SessionOptions()
    o.intra_op_num_threads = config.PLATES_THREADS
    o.inter_op_num_threads = 1
    return o


_readers: dict = {}
_failed: set = set()


class AsyncPlates:
    """Lee patentes en otro hilo, para procesar en vivo sin esperar cada lectura.

    Leer una patente es lo más caro del sistema (un modelo de IA); en un servidor chico
    tarda más que un cuadro de video. Así la detección, la barrera y los eventos siguen al
    ritmo de la cámara y las patentes llegan apenas están: con poco procesador se leen menos
    veces, pero nada se frena. Hay una sola lectura en curso: si está ocupado, `submit`
    no hace nada y ese cuadro no se lee.
    """

    def __init__(self, reader: "PlateReader"):
        self.reader = reader
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._job = None
        self._busy = False
        self._done = []
        self._closed = False
        threading.Thread(target=self._run, daemon=True, name="patentes").start()

    def idle(self) -> bool:
        return not self._busy

    def submit(self, frame: np.ndarray, tag) -> bool:
        """Manda un cuadro a leer (con `tag`, por ejemplo el segundo del video). False si está ocupado."""
        with self._lock:
            if self._busy or self._closed:
                return False
            self._busy, self._job = True, (frame, tag)
        self._wake.set()
        return True

    def results(self) -> list:
        """Lecturas terminadas desde la última llamada: [(lecturas, tag), ...]."""
        with self._lock:
            out, self._done = self._done, []
        return out

    def close(self) -> None:
        self._closed = True
        self._wake.set()

    def _run(self) -> None:
        # Menos prioridad que la detección: si no alcanza el procesador, que espere la
        # patente y no la barrera.
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), config.PLATES_NICE)
        except (AttributeError, OSError):
            pass
        while True:
            self._wake.wait()
            self._wake.clear()
            if self._closed:
                return
            frame, tag = self._job
            try:
                reads = self.reader.read(frame)
            except Exception as e:  # una lectura fallida no corta el video
                print(f"[PATENTES] error al leer: {e}", flush=True)
                reads = []
            with self._lock:
                self._done.append((reads, tag))
                self._busy = False


def create_reader(ocr_model: str | None = None, detector_model: str | None = None) -> PlateReader | None:
    """El lector con ese OCR y ese detector (los modelos se cargan una sola vez). None si no se puede.

    Por defecto, config.PLATES_OCR y config.PLATES_DETECTOR. Un video puede pedir otros en su
    .json ("ocr", "detector_patentes"): los de prueba con chapas dibujadas usan los que mejor
    leen esos dibujos (ver demo_plates.py).
    """
    key = (ocr_model or config.PLATES_OCR, detector_model or config.PLATES_DETECTOR)
    if key not in _readers and key not in _failed:
        try:
            _readers[key] = PlateReader(detector_model=key[1], ocr_model=key[0])
        except Exception as e:  # sin fast-alpr, o sin los modelos
            _failed.add(key)
            print(f"[PATENTES] no se pueden leer patentes con {key}: {e}", flush=True)
    return _readers.get(key)


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
