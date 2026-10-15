"""Dos cámaras: la de la barrera cuenta los cruces y la frontal lee las patentes.

La cámara de la barrera (cenital, la de siempre) decide cuándo hay un evento y si es
entrada o salida. La frontal no ve la línea: solo lee patentes y las anota con la hora
en que las vio (`PlateTimeline`). Cuando la de la barrera registra un evento a los
t segundos, se le da la patente que la frontal vio cerca de t (`claim`).

Con archivos, las dos se leen juntas al ritmo del video de la barrera; `desfase` corrige
que una haya empezado a grabar antes que la otra (tiempo frontal = tiempo barrera + desfase).
Con cámaras en vivo, la hora es la del reloj y no hace falta desfase.
"""
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import config, plates


@dataclass
class Sighting:
    """Una patente vista por la frontal, en una o varias lecturas seguidas."""
    plate: str
    first: float           # segundos, en el reloj de la cámara de la barrera
    last: float
    score: float = 0.0     # suma de confianzas
    best_conf: float = 0.0
    crop: np.ndarray | None = field(default=None, repr=False)
    used: bool = False

    def distance(self, t: float) -> float:
        """0 si t cae mientras se la veía; si no, cuánto antes o después."""
        return 0.0 if self.first <= t <= self.last else min(abs(t - self.first), abs(t - self.last))


class PlateTimeline:
    def __init__(self):
        self.sightings: list[Sighting] = []

    def add(self, read: plates.PlateRead, t: float) -> None:
        if not plates.is_valid(read.text):
            return
        for s in reversed(self.sightings):
            if s.plate == read.text and t - s.last <= config.PAIR_GAP_SECONDS:
                s.last = t
                break
        else:
            s = Sighting(read.text, t, t)
            self.sightings.append(s)
        s.score += read.confidence
        if read.confidence > s.best_conf:
            s.best_conf, s.crop = read.confidence, read.crop
        # No guardar para siempre: lo viejo ya no se puede emparejar con nada.
        if len(self.sightings) > 200:
            self.sightings = [x for x in self.sightings if t - x.last < 60]

    def claim(self, t: float) -> Sighting | None:
        """La patente que mejor corresponde a un evento a los t segundos (y la marca como usada).

        Cuentan las vistas entre PAIR_BEFORE segundos antes y PAIR_AFTER después del evento,
        con puntaje suficiente; gana la más cercana en el tiempo y, a igual distancia, la
        que más se leyó. Una patente se usa una sola vez por pasada.
        """
        cands = [s for s in self.sightings
                 if not s.used and s.score >= config.PLATES_MIN_SCORE
                 and s.last >= t - config.PAIR_BEFORE and s.first <= t + config.PAIR_AFTER]
        if not cands:
            return None
        best = min(cands, key=lambda s: (s.distance(t), -s.score))
        best.used = True
        return best


class FrontalFile:
    """La cámara frontal grabada: se lee a la par del video de la barrera."""

    def __init__(self, path: Path, offset: float, reader: plates.PlateReader):
        self.cap = cv2.VideoCapture(str(path))
        if not self.cap.isOpened():
            raise FileNotFoundError(f"No se pudo abrir el video de la cámara frontal: {Path(path).name}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 25.0
        self.offset = offset
        self.reader = reader
        self.timeline = PlateTimeline()
        self.idx = 0
        self.done = False
        self.last_frame = None
        self.last_boxes = []   # (x, y, w, h, texto) de la última lectura, en el cuadro frontal

    def advance(self, t: float) -> None:
        """Lee la frontal hasta el momento que corresponde a t (segundos de la barrera)."""
        target = t + self.offset
        while not self.done and self.idx / self.fps <= target:
            ok, frame = self.cap.read()
            if not ok:
                self.done = True
                break
            self.idx += 1
            self.last_frame = frame
            if self.idx % config.PLATES_EVERY == 0:
                reads = self.reader.read(frame)
                self.last_boxes = [(*r.box, r.text) for r in reads]
                when = self.idx / self.fps - self.offset
                for r in reads:
                    self.timeline.add(r, when)

    def inset(self, width: int) -> np.ndarray | None:
        """El último cuadro frontal achicado a `width`, con las patentes marcadas, para el recuadro."""
        if self.last_frame is None:
            return None
        f = self.last_frame.copy()
        for x, y, w, h, text in self.last_boxes:
            cv2.rectangle(f, (x, y), (x + w, y + h), (230, 200, 40), max(2, f.shape[1] // 300))
            cv2.putText(f, text, (x, y + h + max(18, h // 2)), cv2.FONT_HERSHEY_SIMPLEX,
                        max(0.6, f.shape[1] / 1200), (230, 200, 40), max(2, f.shape[1] // 400))
        h = max(1, round(f.shape[0] * width / f.shape[1]))
        return cv2.resize(f, (width, h), interpolation=cv2.INTER_AREA)

    def release(self) -> None:
        self.cap.release()
