"""Partida en vivo: el simulador (/juego y /cabina) se procesa mientras se juega.

El navegador avisa cada cambio de la partida (entró un auto, salió, le abrieron la barrera)
y acá dos cámaras virtuales dibujan cada cuadro en el momento en que corresponde, con lo
que se sabe hasta ahí. El sistema las lee como si fueran cámaras de verdad, así que los
eventos aparecen en el panel apenas el auto cruza la línea.

Los cuadros van DELAY segundos atrás del reloj del juego: así un cambio que tardó en llegar
por la red (de hasta DELAY s) ya está cuando se dibuja su cuadro. Todo lo que pasa en el
juego antes de ese momento se puede dibujar sin adivinar nada: un auto que espera la barrera
se dibuja quieto hasta que llega la apertura.
"""
import threading
import time

import cv2

from . import demo_plates, demo_video, juego

DELAY = 1.5          # segundos que la cámara va atrás del juego
IDLE_SECONDS = 60    # sin noticias del navegador (se cerró la pestaña): la partida termina sola


class LiveGame:
    def __init__(self, patentes: bool, t: float = 0.0):
        """`t`: segundo del juego en el que está el navegador ahora (el reloj arranca ahí)."""
        self.patentes = patentes
        self.t0 = time.monotonic() - t
        self.lock = threading.Lock()
        self.schedule = []      # (segundo, sentido, color BGR, patente, pausa), como juego.build_schedule
        self.end = None         # segundos de video, cuando el navegador avisa que terminó
        self.closed = False
        self.last_news = time.monotonic()

    def now(self) -> float:
        """Segundo de video que ya se puede dibujar."""
        return time.monotonic() - self.t0 - DELAY

    def update(self, autos: list[dict], fin: float | None = None) -> None:
        """La partida hasta ahora (mismo formato que /api/juego). Con `fin`, terminó en ese segundo."""
        if not autos:
            with self.lock:
                self.last_news = time.monotonic()
                if fin is not None:
                    self.end = fin
            return
        schedule, seconds = juego.build_schedule(autos, self.patentes, fin)
        with self.lock:
            self.schedule = schedule
            self.last_news = time.monotonic()
            if fin is not None:
                self.end = seconds

    def close(self) -> None:
        self.closed = True

    def snapshot(self):
        with self.lock:
            return list(self.schedule)

    def finished(self, t: float) -> bool:
        if self.closed:
            return True
        with self.lock:
            end, news = self.end, self.last_news
        if end is not None and t >= end:
            return True
        return time.monotonic() - news > IDLE_SECONDS or t > juego.MAX_SECONDS


class VirtualCamera:
    """Una cámara de la partida en vivo, con lo mínimo de cv2.VideoCapture que usa el sistema."""

    def __init__(self, game: LiveGame, frontal: bool = False):
        self.game = game
        self.frontal = frontal
        self.fps = demo_plates.FPS if frontal else demo_video.FPS
        self.size = (demo_plates.W, demo_plates.H) if frontal else (demo_video.W, demo_video.H)
        self.renderer = demo_plates.Renderer(lanes=True) if frontal else demo_video.Renderer()
        self.idx = 0
        # Para que el sistema vaya al ritmo de esta cámara y no del reloj de la PC.
        self.start_time = game.t0 + DELAY

    def isOpened(self) -> bool:  # noqa: N802 (nombre de OpenCV)
        return True

    def get(self, prop):
        return {cv2.CAP_PROP_FPS: self.fps, cv2.CAP_PROP_FRAME_WIDTH: self.size[0],
                cv2.CAP_PROP_FRAME_HEIGHT: self.size[1], cv2.CAP_PROP_FRAME_COUNT: 0}.get(prop, 0)

    def _wait(self, t: float) -> bool:
        """Espera a que el cuadro del segundo t se pueda dibujar. False si la partida terminó."""
        while True:
            if self.game.finished(t):
                return False
            if self.game.now() >= t:
                return True
            time.sleep(min(0.05, t - self.game.now()))

    def grab(self) -> bool:
        if not self._wait(self.idx / self.fps):
            return False
        self.idx += 1
        return True

    def read(self):
        if not self._wait(self.idx / self.fps):
            return False, None
        schedule = self.game.snapshot()
        if self.frontal:
            frame = self.renderer.frame(self.idx, [(t, way, p, color, pause) for t, way, color, p, pause in schedule])
        else:
            frame = self.renderer.frame(self.idx, [(t, way, color, pause) for t, way, color, _, pause in schedule])
        self.idx += 1
        return True, frame

    def release(self) -> None:
        pass


def settings(game: LiveGame) -> dict:
    """Ajustes del sistema para la partida en vivo (los mismos que juego.save pone en el .json)."""
    opts = {"line": None, "frontal": None, "frontal_lanes": None}
    if game.patentes:
        opts.update(frontal=VirtualCamera(game, frontal=True), offset=0.0, frontal_lanes="entrada-izquierda",
                    ocr=demo_plates.DRAWN_OCR, plate_detector=demo_plates.DRAWN_DETECTOR)
    return {k: v for k, v in opts.items() if v is not None}
