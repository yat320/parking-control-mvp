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
    """Una patente vista por la frontal, en una o varias lecturas seguidas.

    Lecturas que difieren en una letra (AA310WF / AA310VF) mientras se ve al auto son del
    mismo auto: se juntan y la patente es la que más suma (`texts`).
    """
    plate: str
    first: float           # segundos, en el reloj de la cámara de la barrera
    last: float
    score: float = 0.0     # suma de confianzas
    best_conf: float = 0.0
    crop: np.ndarray | None = field(default=None, repr=False)
    used: bool = False
    widths: list = field(default_factory=list, repr=False)  # ancho de la chapa en cada lectura
    xs: list = field(default_factory=list, repr=False)      # centro de la chapa, en fracción del ancho
    texts: dict = field(default_factory=dict, repr=False)   # texto leído -> suma de confianzas

    def trend(self) -> str:
        """'acerca' si la chapa viene creciendo, 'aleja' si viene achicándose, '' si no se sabe.

        La frontal mira hacia afuera: el auto que entra se acerca (su chapa crece) y el que
        sale se aleja (se achica). Compara las primeras lecturas con las últimas.
        """
        if len(self.widths) < 3:
            return ""
        k = max(1, len(self.widths) // 3)
        a, b = np.mean(self.widths[:k]), np.mean(self.widths[-k:])
        if b > a * config.PAIR_TREND:
            return "acerca"
        if a > b * config.PAIR_TREND:
            return "aleja"
        return ""

    def side(self) -> str:
        """'izquierda' o 'derecha': de qué lado de la imagen se la vio (o '' si no se sabe)."""
        if not self.xs:
            return ""
        return "izquierda" if float(np.median(self.xs)) < 0.5 else "derecha"

    def distance(self, t: float) -> float:
        """0 si t cae mientras se la veía; si no, cuánto antes o después."""
        return 0.0 if self.first <= t <= self.last else min(abs(t - self.first), abs(t - self.last))


class PlateTimeline:
    def __init__(self, lanes: str | None = None):
        """`lanes`: "entrada-izquierda" o "entrada-derecha" si la frontal ve los dos carriles."""
        self.sightings: list[Sighting] = []
        self.lanes = lanes
        self.claimed: list[tuple[str, float, float]] = []  # (patente, primera vista, segundo del evento)

    def _taken(self, s: Sighting) -> bool:
        """Esa chapa ya se le dio a un evento en esta misma pasada (mismo texto o una letra distinta).

        Mientras un auto espera la barrera, la frontal lo ve en varios tramos, y una lectura con
        una letra mal (WM833DH por AM833DH) puede abrir un tramo aparte: si no se descartan,
        el auto siguiente se lleva la misma patente. La pasada va desde que se lo vio hasta
        PAIR_AFTER s después del cruce; lo que se ve más tarde es otra pasada (la vuelta).
        """
        return any((s.plate == p or plates.one_off(s.plate, p))
                   and first - config.PAIR_GAP_SECONDS <= s.first <= when + config.PAIR_AFTER
                   for p, first, when in self.claimed)

    def add(self, read: plates.PlateRead, t: float, width: int | None = None) -> None:
        if not plates.is_valid(read.text):
            return
        for s in reversed(self.sightings):
            if t - s.last <= config.PAIR_GAP_SECONDS and (
                    s.plate == read.text or any(plates.one_off(x, read.text) for x in s.texts)):
                s.last = t
                break
        else:
            s = Sighting(read.text, t, t)
            self.sightings.append(s)
        s.texts[read.text] = s.texts.get(read.text, 0.0) + read.confidence
        s.plate = max(s.texts.items(), key=lambda kv: kv[1])[0]
        s.score += read.confidence
        s.widths.append(read.box[2])
        if width:
            s.xs.append((read.box[0] + read.box[2] / 2) / width)
        if read.confidence > s.best_conf:
            s.best_conf, s.crop = read.confidence, read.crop
        # No guardar para siempre: lo viejo ya no se puede emparejar con nada.
        if len(self.sightings) > 200:
            self.sightings = [x for x in self.sightings if t - x.last < 60]

    def claim(self, t: float, kind: str | None = None) -> Sighting | None:
        """La patente que mejor corresponde a un evento a los t segundos (y la marca como usada).

        Cuentan las vistas entre PAIR_BEFORE segundos antes y PAIR_AFTER después del evento,
        con puntaje suficiente, y gana la que apareció primero: los autos pasan en fila, así
        que el primero que vio la frontal y todavía no se usó es el que está cruzando. Eso
        sirve con la frontal pegada a la barrera (ve al auto mientras cruza) y con la frontal
        más afuera (lo ve llegar varios segundos antes, mientras el siguiente ya se acerca).
        Una patente se usa una sola vez por pasada.

        Con `kind` ('entrada' o 'salida'), no se le da a una entrada una chapa que se ve
        alejarse ni a una salida una que se ve acercarse: con un carril de cada lado, la
        frontal ve a la vez al que entra y al que sale.
        """
        wrong = {"entrada": "aleja", "salida": "acerca"}.get(kind) if config.PAIR_TREND else None
        if config.FRONTAL_LOOKS_IN and wrong:
            wrong = {"aleja": "acerca", "acerca": "aleja"}[wrong]
        # Con los dos carriles a la vista, el lado de la imagen dice si entra o sale. Es lo que
        # sirve con un auto quieto esperando la barrera: no se acerca ni se aleja.
        side = None
        if self.lanes and kind in ("entrada", "salida"):
            entry_side = "izquierda" if self.lanes == "entrada-izquierda" else "derecha"
            side = entry_side if kind == "entrada" else {"izquierda": "derecha", "derecha": "izquierda"}[entry_side]
        cands = [s for s in self.sightings
                 if not s.used and s.score >= config.PLATES_MIN_SCORE
                 and s.last >= t - config.PAIR_BEFORE and s.first <= t + config.PAIR_AFTER
                 and not (wrong and s.trend() == wrong)
                 and not (side and s.side() and s.side() != side)
                 and not self._taken(s)]
        if not cands:
            return None
        best = min(cands, key=lambda s: s.first)
        best.used = True
        self.claimed.append((best.plate, best.first, t))  # una patente, una vez por pasada
        return best


class FrontalFile:
    """La cámara frontal grabada: se lee a la par del video de la barrera."""

    def __init__(self, path: Path, offset: float, reader: plates.PlateReader, live: bool = False,
                 lanes: str | None = None):
        self.cap = cv2.VideoCapture(str(path))
        if not self.cap.isOpened():
            raise FileNotFoundError(f"No se pudo abrir el video de la cámara frontal: {Path(path).name}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 25.0
        self.offset = offset
        self.reader = reader
        self.timeline = PlateTimeline(lanes)
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or None
        self.idx = 0
        self.done = False
        self.last_frame = None
        self.last_boxes = []   # (x, y, w, h, texto) de la última lectura, en el cuadro frontal
        # En vivo las patentes se leen en otro hilo (plates.AsyncPlates); con archivos
        # procesados de una, en el mismo, así el resultado es siempre el mismo.
        self.async_reader = plates.AsyncPlates(reader) if live else None

    def _add(self, reads, when: float) -> None:
        self.last_boxes = [(*r.box, r.text) for r in reads]
        for r in reads:
            self.timeline.add(r, when, self.width)

    def advance(self, t: float, busy: bool = True) -> None:
        """Lee la frontal hasta el momento que corresponde a t (segundos de la barrera).

        Solo busca patentes si `busy` (la cámara de la barrera ve algún auto): leer
        patentes es lo más caro, y sin autos a la vista no hay nada que leer. Los
        cuadros intermedios se saltean sin convertirlos a imagen.
        """
        target = t + self.offset
        if self.async_reader:
            for reads, when in self.async_reader.results():
                self._add(reads, when)
        while not self.done and self.idx / self.fps <= target:
            last = (self.idx + 1) / self.fps > target  # el último antes de alcanzar a la barrera
            want = busy and (self.idx + 1) % config.PLATES_EVERY == 0 \
                and (self.async_reader is None or self.async_reader.idle())
            if not (want or last):
                if not self.cap.grab():
                    self.done = True
                    break
                self.idx += 1
                continue
            ok, frame = self.cap.read()
            if not ok:
                self.done = True
                break
            self.idx += 1
            self.last_frame = frame
            if want:
                when = self.idx / self.fps - self.offset
                if self.async_reader:
                    self.async_reader.submit(frame, when)
                else:
                    self._add(self.reader.read(frame), when)

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
        if self.async_reader:
            self.async_reader.close()
        self.cap.release()
