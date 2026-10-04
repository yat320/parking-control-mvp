"""Procesamiento del video: detección -> tracking -> línea virtual -> evento -> barrera.

Se puede usar de dos formas:
  - desde la web (app/main.py), que lo corre en un hilo y muestra los frames;
  - por consola:  python -m app.video_processor --video videos/video_test.mp4

Flujo por frame:
  1. el detector devuelve cajas de vehículos (MotionDetector o YoloDetector),
  2. el tracker les asigna un tracking_id estable,
  3. si el centroide de un track pasa de un lado al otro de la línea virtual,
     se registra ENTRADA (hacia abajo) o SALIDA (hacia arriba) en SQLite,
  4. el evento abre la barrera simulada, que se cierra sola a los N segundos,
  5. se dibuja todo sobre el frame y se guarda en el video de salida.
"""
import argparse
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from . import config, database
from .barrier_controller import OPEN, BarrierController
from .demo_video import generate as generate_demo_video
from .detection import create_detector
from .pricing import format_duration
from .tracker import CentroidTracker

MIN_SEEN_FRAMES = 3   # un track tiene que verse al menos 3 frames para ser "válido"
MESSAGE_SECONDS = 2.0  # cuánto queda en pantalla "EVENTO REGISTRADO"

YELLOW, GREEN, RED, WHITE, BLACK = (0, 220, 255), (60, 200, 60), (40, 40, 230), (255, 255, 255), (0, 0, 0)


def side_of_line(p, a, b) -> int:
    """-1 / +1 según de qué lado de la recta a->b está el punto p (0 si está fuera del segmento)."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    # Proyección: si el punto queda fuera de los extremos del segmento, no cuenta.
    t = ((px - ax) * dx + (py - ay) * dy) / float(dx * dx + dy * dy)
    if t < 0 or t > 1:
        return 0
    cross = dx * (py - ay) - dy * (px - ax)
    return 1 if cross > 0 else -1 if cross < 0 else 0


class VideoProcessor:
    def __init__(self, barrier: BarrierController | None = None, detector_kind: str = config.DETECTOR,
                 line=config.LINE, entry_direction: str = config.ENTRY_DIRECTION,
                 time_scale: float = config.TIME_SCALE, min_area: int = config.MIN_AREA):
        self.barrier = barrier or BarrierController(config.BARRIER_OPEN_SECONDS)
        self.detector_kind = detector_kind
        self.line_rel = line
        # Con la línea de izquierda a derecha, "+1" es abajo de la línea.
        self.entry_sign = 1 if entry_direction == "down" else -1
        self.time_scale = time_scale
        self.min_area = min_area

        # Estado que lee la web (se actualiza en cada frame).
        self.lock = threading.Lock()
        self.latest_jpeg: bytes | None = None
        self.running = False
        self.progress = 0.0
        self.last_message = ""
        self.detected_now = 0
        self._stop = threading.Event()

    # ---------- API ----------

    def stop(self) -> None:
        self._stop.set()

    def process(self, video_path: Path, output_path: Path | None = None, realtime: bool = False,
                show: bool = False) -> list[dict]:
        """Procesa el video entero. Devuelve los eventos registrados."""
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise FileNotFoundError(f"No se pudo abrir el video: {video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        a = (int(self.line_rel[0] * w), int(self.line_rel[1] * h))
        b = (int(self.line_rel[2] * w), int(self.line_rel[3] * h))

        writer = None
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

        detector = create_detector(self.detector_kind, self.min_area)
        tracker = CentroidTracker(max_distance=max(w, h) * 0.2)
        self.barrier.reset()
        start_wall = datetime.now()  # hora "real" que corresponde al segundo 0 del video
        events, message, message_until = [], "", -1.0
        self._stop.clear()
        self.running = True
        frame_idx, t0 = 0, time.monotonic()
        print(f"[VIDEO] {video_path} {w}x{h} @ {fps:.1f} fps, detector={detector.name}", flush=True)

        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    break
                video_t = frame_idx / fps
                frame_idx += 1

                detections = detector.detect(frame)
                tracks = tracker.update(detections)
                self.barrier.update(video_t)

                for tr in tracks.values():
                    if tr.missed:
                        continue
                    side = side_of_line(tr.centroid, a, b)
                    if side == 0:
                        continue
                    prev, tr.side = tr.side, side
                    # Cruce: el lado cambió y el vehículo es "válido" (visto varios frames).
                    if prev != 0 and prev != side and tr.seen >= MIN_SEEN_FRAMES:
                        kind = "entrada" if side == self.entry_sign else "salida"
                        if kind in tr.counted:
                            continue
                        tr.counted.add(kind)
                        # La barrera se abre ANTES de registrar, así el evento guarda "abierta".
                        self.barrier.open(video_t, reason=f"{kind} track {tr.id}")
                        ts = start_wall + timedelta(seconds=video_t * self.time_scale)
                        if kind == "entrada":
                            ev = database.register_entry(ts, tr.id, self.barrier.state)
                            message = f"EVENTO REGISTRADO: ENTRADA {ev['vehicle_id']}"
                        else:
                            ev = database.register_exit(ts, tr.id, self.barrier.state)
                            extra = ""
                            if ev["monto"] is not None:
                                extra = f" {format_duration(ev['duracion_seg'])} ${ev['monto']:.0f}"
                            message = f"EVENTO REGISTRADO: SALIDA {ev['vehicle_id']}{extra}"
                        message_until = video_t + MESSAGE_SECONDS
                        ev["video_seg"] = round(video_t, 2)
                        events.append(ev)
                        print(f"[EVENTO] t={video_t:6.2f}s {message}", flush=True)

                shown_msg = message if video_t < message_until else ""
                self._draw(frame, tracks, a, b, video_t, shown_msg)
                if writer:
                    writer.write(frame)

                with self.lock:
                    self.latest_jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
                    self.progress = frame_idx / total if total else 0.0
                    self.detected_now = sum(1 for t in tracks.values() if not t.missed)
                    if shown_msg:
                        self.last_message = shown_msg

                if show:
                    cv2.imshow("Parking Control MVP", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                if realtime:
                    # Esperar para ir a la velocidad real del video (para verlo en la web).
                    delay = frame_idx / fps - (time.monotonic() - t0)
                    if delay > 0:
                        time.sleep(delay)
        finally:
            cap.release()
            if writer:
                writer.release()
            if show:
                cv2.destroyAllWindows()
            self.barrier.reset()
            self.running = False
        print(f"[VIDEO] fin: {frame_idx} frames, {len(events)} eventos", flush=True)
        if output_path:
            print(f"[VIDEO] video procesado: {output_path}", flush=True)
        return events

    # ---------- dibujo ----------

    def _draw(self, frame: np.ndarray, tracks, a, b, video_t: float, message: str) -> None:
        is_open = self.barrier.state == OPEN

        # Línea virtual + brazo de la barrera simulada (horizontal cerrada, levantado abierta).
        cv2.line(frame, a, b, YELLOW, 2)
        cv2.putText(frame, "LINEA VIRTUAL", (a[0] + 4, a[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, YELLOW, 1)
        pivot = (b[0] - 10, b[1])
        arm_len = int(abs(b[0] - a[0]) * 0.55)
        end = (pivot[0] - (arm_len // 6 if is_open else arm_len), pivot[1] - (arm_len if is_open else 0))
        cv2.line(frame, pivot, end, WHITE, 7)
        cv2.line(frame, pivot, end, RED, 3)
        cv2.rectangle(frame, (pivot[0] - 8, pivot[1] - 8), (pivot[0] + 8, pivot[1] + 25), (80, 80, 80), -1)

        # Cajas de los vehículos.
        active = [t for t in tracks.values() if not t.missed]
        for t in active:
            d = t.detection
            cv2.rectangle(frame, (d.x, d.y), (d.x + d.w, d.y + d.h), GREEN, 2)
            label = f"#{t.id} {d.label}" + (f" {d.confidence:.0%}" if d.confidence < 1 else "")
            cv2.putText(frame, label, (d.x, max(12, d.y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREEN, 2)
            cv2.circle(frame, t.centroid, 4, YELLOW, -1)
            if len(t.history) > 1:
                cv2.polylines(frame, [np.array(t.history, np.int32)], False, YELLOW, 1)

        # Textos de estado (arriba a la izquierda).
        def badge(text, y, color):
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(frame, (8, y - th - 8), (16 + tw, y + 6), BLACK, -1)
            cv2.putText(frame, text, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        badge("BARRERA ABIERTA" if is_open else "BARRERA CERRADA", 28, GREEN if is_open else RED)
        if active:
            badge("AUTO DETECTADO", 58, YELLOW)
        if message:
            badge(message, frame.shape[0] - 14, WHITE)
        cv2.putText(frame, f"t={video_t:5.1f}s", (frame.shape[1] - 90, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Procesa un video y registra entradas/salidas.")
    parser.add_argument("--video", type=Path, default=config.VIDEO_PATH)
    parser.add_argument("--output", type=Path, default=config.OUTPUT_PATH)
    parser.add_argument("--no-output", action="store_true", help="no guardar el video procesado")
    parser.add_argument("--detector", default=config.DETECTOR, choices=["motion", "yolo"])
    parser.add_argument("--realtime", action="store_true", help="procesar a la velocidad del video")
    parser.add_argument("--show", action="store_true", help="mostrar ventana (requiere opencv-python, no headless)")
    parser.add_argument("--reset", action="store_true", help="borrar los eventos antes de empezar")
    args = parser.parse_args()

    database.init_db()
    if not args.video.exists() and args.video == config.VIDEO_PATH:
        print(f"No existe {args.video}: genero el video de prueba.")
        generate_demo_video(args.video, 40)
    if args.reset:
        database.reset_events()
    proc = VideoProcessor(detector_kind=args.detector)
    events = proc.process(args.video, None if args.no_output else args.output, realtime=args.realtime, show=args.show)
    s = database.stats()
    print(f"\nResumen: {len(events)} eventos en este video | entradas {s['entradas']} | "
          f"salidas {s['salidas']} | adentro {s['adentro']} | recaudado ${s['recaudado']:.2f}")


if __name__ == "__main__":
    main()
