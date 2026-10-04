"""Detectores de vehículos.

Todos cumplen la misma interfaz: `detect(frame) -> list[Detection]`. El resto
del sistema (tracker, línea virtual, barrera, base de datos) no sabe qué
detector se usa, así que pasar de la detección simple a YOLO es cambiar
`PARKING_DETECTOR=yolo` (o agregar otro detector acá con el mismo método).
"""
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Detection:
    x: int
    y: int
    w: int
    h: int
    label: str = "vehiculo"
    confidence: float = 1.0

    @property
    def centroid(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)


class BaseDetector:
    name = "base"

    def detect(self, frame: np.ndarray) -> list[Detection]:
        raise NotImplementedError


class MotionDetector(BaseDetector):
    """Estrategia simple: sustracción de fondo (MOG2).

    Con la cámara fija, el fondo (piso, barrera, paredes) casi no cambia. MOG2
    aprende ese fondo y marca como "primer plano" lo que se mueve. Después:
      1. se descartan las sombras (MOG2 las marca con gris 127),
      2. se limpia el ruido con apertura y se unen partes del auto con cierre,
      3. cada contorno con área >= min_area es un vehículo candidato.

    Limitaciones conocidas: una persona o un perro grande también cuentan si
    superan el área mínima; un auto que se queda quieto mucho tiempo termina
    siendo "fondo". Para el MVP alcanza; YOLO resuelve ambas cosas.
    """

    name = "motion"

    def __init__(self, min_area: int = 2500, warmup_frames: int = 25):
        self.min_area = min_area
        self.warmup_frames = warmup_frames
        self.frames = 0
        self.subtractor = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=40, detectShadows=True)
        self.kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self.kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
        self.last_mask = None  # se guarda para depurar

    def detect(self, frame: np.ndarray) -> list[Detection]:
        self.frames += 1
        blurred = cv2.GaussianBlur(frame, (5, 5), 0)
        mask = self.subtractor.apply(blurred)
        # Los primeros frames el modelo todavía está aprendiendo el fondo.
        if self.frames <= self.warmup_frames:
            return []
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)  # fuera sombras
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel_open)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self.kernel_close)
        self.last_mask = mask
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections = []
        for c in contours:
            if cv2.contourArea(c) < self.min_area:
                continue
            x, y, w, h = cv2.boundingRect(c)
            detections.append(Detection(x, y, w, h))
        return detections


class YoloDetector(BaseDetector):
    """Detector con YOLO (ultralytics). Opcional: `pip install ultralytics`.

    Solo se quedan las clases de vehículos de COCO. El modelo `yolov8n.pt` se
    descarga solo la primera vez (unos 6 MB).
    """

    name = "yolo"
    VEHICLE_CLASSES = {2: "auto", 3: "moto", 5: "colectivo", 7: "camion"}

    def __init__(self, model_path: str = "yolov8n.pt", min_confidence: float = 0.4):
        try:
            from ultralytics import YOLO
        except ImportError as e:  # pragma: no cover - depende de la instalación
            raise RuntimeError("Para usar YOLO instalá: pip install ultralytics") from e
        self.model = YOLO(model_path)
        self.min_confidence = min_confidence

    def detect(self, frame: np.ndarray) -> list[Detection]:
        result = self.model(frame, verbose=False)[0]
        detections = []
        for box in result.boxes:
            cls = int(box.cls[0])
            conf = float(box.conf[0])
            if cls not in self.VEHICLE_CLASSES or conf < self.min_confidence:
                continue
            x1, y1, x2, y2 = (int(v) for v in box.xyxy[0])
            detections.append(Detection(x1, y1, x2 - x1, y2 - y1, self.VEHICLE_CLASSES[cls], conf))
        return detections


def create_detector(kind: str, min_area: int = 2500) -> BaseDetector:
    if kind == "yolo":
        return YoloDetector()
    if kind == "motion":
        return MotionDetector(min_area=min_area)
    raise ValueError(f"Detector desconocido: {kind!r} (usar 'motion' o 'yolo')")
