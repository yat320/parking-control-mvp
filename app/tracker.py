"""Tracker por centroides.

Le da un id estable (tracking_id) a cada vehículo mientras está en cuadro,
uniendo cada detección con el track más cercano del frame anterior. Es lo
mínimo para saber que el auto que estaba arriba de la línea es el mismo que
ahora está abajo. Con YOLO se puede cambiar por ByteTrack/DeepSORT sin tocar
el resto, siempre que devuelva el mismo dict {id: Track}.
"""
from dataclasses import dataclass, field

from .detection import Detection


@dataclass
class Track:
    id: int
    detection: Detection
    history: list = field(default_factory=list)  # centroides recientes
    missed: int = 0      # frames seguidos sin verlo
    seen: int = 1        # frames en que se lo vio
    side: int = 0        # de qué lado de la línea está (-1, 0, +1)
    counted: set = field(default_factory=set)  # eventos ya registrados ('entrada'/'salida')

    @property
    def centroid(self):
        return self.detection.centroid


class CentroidTracker:
    def __init__(self, max_distance: float = 120, max_missed: int = 15):
        self.max_distance = max_distance
        self.max_missed = max_missed
        self.tracks: dict[int, Track] = {}
        self._next_id = 1

    def update(self, detections: list[Detection]) -> dict[int, Track]:
        # Emparejar de forma "greedy": primero los pares más cercanos.
        pairs = []
        for tid, t in self.tracks.items():
            tx, ty = t.centroid
            for i, d in enumerate(detections):
                dx, dy = d.centroid
                dist = ((tx - dx) ** 2 + (ty - dy) ** 2) ** 0.5
                if dist <= self.max_distance:
                    pairs.append((dist, tid, i))
        pairs.sort()
        used_tracks, used_dets = set(), set()
        for _, tid, i in pairs:
            if tid in used_tracks or i in used_dets:
                continue
            t = self.tracks[tid]
            t.detection = detections[i]
            t.missed = 0
            t.seen += 1
            t.history = (t.history + [t.centroid])[-30:]
            used_tracks.add(tid)
            used_dets.add(i)

        # Tracks que no se vieron en este frame.
        for tid in list(self.tracks):
            if tid not in used_tracks:
                self.tracks[tid].missed += 1
                if self.tracks[tid].missed > self.max_missed:
                    del self.tracks[tid]

        # Detecciones nuevas -> tracks nuevos.
        for i, d in enumerate(detections):
            if i not in used_dets:
                self.tracks[self._next_id] = Track(self._next_id, d, history=[d.centroid])
                self._next_id += 1
        return self.tracks
