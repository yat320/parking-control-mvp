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

    El fondo aprende despacio (`history`) y no aprende donde hay un vehículo
    (`still_frames`): con lo que trae OpenCV un auto quieto ~2,5 s pasaba a ser
    fondo, y con mucho movimiento el lugar donde se espera la barrera terminaba
    aprendiendo "auto oscuro" como fondo; en los dos casos el que esperaba
    desaparecía y al arrancar aparecía como otro, justo sobre la línea.

    De noche (fondo oscuro) se descarta además la luz de los faros sobre el piso:
    lo que se aclaró sin cambiar de color. Si no, el haz que va delante del auto
    se suma a la mancha y le corre el centro.

    Limitaciones conocidas: una persona o un perro grande también cuentan si
    superan el área mínima; algo que se queda quieto más de `still_frames` termina
    siendo "fondo". Para el MVP alcanza; YOLO resuelve ambas cosas.
    """

    name = "motion"
    PAD = 8             # px alrededor de cada vehículo donde el fondo no aprende
    NIGHT_LEVEL = 65    # brillo medio del fondo por debajo del cual es de noche
    LIGHT_RATIO = 1.1   # cuánto más claro que el fondo para ser luz
    LIGHT_CHROMA = 0.15  # cuánto puede cambiar el color y seguir siendo luz

    def __init__(self, min_area: int = 2500, warmup_frames: int = 25, history: int = 500,
                 still_frames: int = 0):
        self.min_area = min_area
        self.warmup_frames = warmup_frames
        self.still_frames = still_frames
        self.frames = 0
        self.subtractor = cv2.createBackgroundSubtractorMOG2(history=history, varThreshold=40, detectShadows=True)
        self.kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self.kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
        self.protect = None  # dónde no aprende el fondo en el próximo frame
        self.age = None      # frames seguidos que cada píxel lleva protegido
        self.last_mask = None  # se guarda para depurar

    def detect(self, frame: np.ndarray) -> list[Detection]:
        self.frames += 1
        blurred = cv2.GaussianBlur(frame, (5, 5), 0)
        # Los primeros frames el modelo todavía está aprendiendo el fondo.
        if self.frames <= self.warmup_frames:
            self.subtractor.apply(blurred)
            return []
        background = self.subtractor.getBackgroundImage()
        if self.still_frames:
            # Se mira el frame sin aprender y se aprende de una copia donde los
            # vehículos se tapan con el fondo que ya había.
            mask = self.subtractor.apply(blurred, learningRate=0)
            learn = blurred
            if self.protect is not None and self.protect.any():
                learn = blurred.copy()
                learn[self.protect] = background[self.protect]
            self.subtractor.apply(learn)
        else:
            mask = self.subtractor.apply(blurred)
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)  # fuera sombras
        if background.mean() < self.NIGHT_LEVEL:
            mask[light_mask(blurred, background, self.LIGHT_RATIO, self.LIGHT_CHROMA)] = 0
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
        if self.still_frames:
            boxes = np.zeros(mask.shape, bool)
            p = self.PAD
            for d in detections:
                boxes[max(0, d.y - p):d.y + d.h + p, max(0, d.x - p):d.x + d.w + p] = True
            self.age = np.where(boxes, (0 if self.age is None else self.age) + 1, 0)
            self.protect = boxes & (self.age < self.still_frames)
        return detections


def light_mask(frame: np.ndarray, background: np.ndarray, ratio: float, chroma: float) -> np.ndarray:
    """Píxeles más claros que el fondo y del mismo color: luz que cae sobre el piso."""
    f = frame.astype(np.float32) + 1
    b = background.astype(np.float32) + 1
    gf, gb = f.mean(axis=2), b.mean(axis=2)
    change = np.abs(f / gf[..., None] - b / gb[..., None]).max(axis=2)
    return (gf / gb > ratio) & (change < chroma)


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


def create_detector(kind: str, min_area: int = 2500, fps: float = 25.0) -> BaseDetector:
    if kind == "yolo":
        return YoloDetector()
    if kind == "motion":
        from app import config
        return MotionDetector(min_area=min_area, history=max(500, round(config.BG_SECONDS * fps)),
                              still_frames=round(config.STILL_SECONDS * fps))
    raise ValueError(f"Detector desconocido: {kind!r} (usar 'motion' o 'yolo')")
