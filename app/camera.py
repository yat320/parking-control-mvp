"""Lectura de una cámara en vivo (IP por RTSP/HTTP o webcam USB).

Un archivo se lee cuadro por cuadro al ritmo que uno quiera. Una cámara no:
los cuadros llegan solos, y si se los procesa más lento de lo que llegan se
acumulan y la imagen queda cada vez más atrasada. Por eso un hilo aparte lee
sin parar y se queda solo con el último cuadro.

Si la cámara deja de responder (corte de luz, wifi, reinicio), el lector
la vuelve a abrir cada `retry_seconds` hasta que vuelva o le digan que pare.
"""
import re
import threading

import cv2


def parse_source(source):
    """'0' -> 0 (webcam por número); cualquier otra cosa queda como texto (URL o ruta)."""
    s = str(source).strip()
    return int(s) if s.isdigit() else s


def mask_source(source) -> str:
    """La URL sin usuario ni clave, para mostrar en la web y en los logs."""
    return re.sub(r"//[^/@]+@", "//***@", str(source))


class FrameReader:
    def __init__(self, source, capture_factory=cv2.VideoCapture, retry_seconds: float = 3.0,
                 drop_frames: bool = True):
        self.source = parse_source(source)
        self._factory = capture_factory
        self.retry_seconds = retry_seconds
        # drop_frames=False no descarta nada (espera a que se consuma cada cuadro):
        # sirve para pruebas y para pasar una grabación como si fuera una cámara.
        self.drop_frames = drop_frames
        self.connected = False
        self.reconnects = 0
        self.size = (0, 0)
        self._cap = None
        self._frame = None
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread = None

    def start(self) -> "FrameReader":
        """Abre la cámara. Si no responde la primera vez, falla en el momento
        (lo más probable es que la dirección esté mal)."""
        self._cap = self._factory(self.source)
        if not self._cap.isOpened():
            self._cap.release()
            raise ConnectionError(f"No se pudo conectar a la cámara: {mask_source(self.source)}")
        self.size = (int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        self.connected = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def get(self, timeout: float = 1.0):
        """El cuadro más nuevo que todavía no se entregó, o None si no llegó ninguno."""
        with self._cond:
            if self._frame is None:
                self._cond.wait(timeout)
            frame, self._frame = self._frame, None
            self._cond.notify_all()
            return frame

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=5)
        if self._cap is not None:
            self._cap.release()
        self.connected = False

    def _run(self) -> None:
        while not self._stop.is_set():
            ok, frame = self._cap.read() if self._cap.isOpened() else (False, None)
            if not ok:
                if self.connected:
                    print(f"[CAMARA] sin señal, reintento cada {self.retry_seconds:g} s", flush=True)
                self.connected = False
                self._cap.release()
                if self._stop.wait(self.retry_seconds):
                    break
                self._cap = self._factory(self.source)
                continue
            if not self.connected:
                self.connected = True
                self.reconnects += 1
                print("[CAMARA] volvió la señal", flush=True)
            with self._cond:
                if not self.drop_frames:
                    while self._frame is not None and not self._stop.is_set():
                        self._cond.wait(0.1)
                self._frame = frame
                self._cond.notify_all()
