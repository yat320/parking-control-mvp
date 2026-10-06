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
DISPLAY_MIN_WIDTH = 640  # ancho mínimo del video procesado (los más chicos se agrandan)

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
        """Procesa el video entero. Devuelve los eventos registrados.

        Si al lado del video hay un .json con el mismo nombre (por ejemplo
        videos/calle_real.json), sus valores de línea, sentido y área mínima
        reemplazan a los de config.py solo para ese video.
        """
        opts = config.video_settings(Path(video_path), {
            "line": self.line_rel, "entry_sign": self.entry_sign, "min_area": self.min_area})
        line_rel, entry_sign, min_area = opts["line"], opts["entry_sign"], opts["min_area"]
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise FileNotFoundError(f"No se pudo abrir el video: {video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        a = (int(line_rel[0] * w), int(line_rel[1] * h))
        b = (int(line_rel[2] * w), int(line_rel[3] * h))
        # Los videos chicos (cámaras viejas, 320 px) se agrandan solo para dibujar,
        # así los textos se leen. La detección corre siempre sobre el frame original.
        scale = max(1.0, DISPLAY_MIN_WIDTH / w)
        out_size = (round(w * scale), round(h * scale))

        writer = None
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, out_size)

        detector = create_detector(self.detector_kind, min_area)
        tracker = CentroidTracker(max_distance=max(w, h) * 0.2)
        self.barrier.reset()
        start_wall = datetime.now()  # hora "real" que corresponde al segundo 0 del video
        events, message, message_until = [], "", -1.0
        self._stop.clear()
        self.running = True
        frame_idx, t0 = 0, time.monotonic()
        print(f"[VIDEO] {video_path} {w}x{h} @ {fps:.1f} fps, detector={detector.name}, "
              f"línea={tuple(line_rel)}, área mínima={min_area}", flush=True)

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
                        kind = "entrada" if side == entry_sign else "salida"
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
                view = cv2.resize(frame, out_size, interpolation=cv2.INTER_CUBIC) if scale > 1 else frame
                self._draw(view, tracks, a, b, video_t, shown_msg, scale)
                if writer:
                    writer.write(view)

                with self.lock:
                    self.latest_jpeg = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
                    self.progress = frame_idx / total if total else 0.0
                    self.detected_now = sum(1 for t in tracks.values() if not t.missed)
                    if shown_msg:
                        self.last_message = shown_msg

                if show:
                    cv2.imshow("Parking Control MVP", view)
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

    def _draw(self, frame: np.ndarray, tracks, a, b, video_t: float, message: str, scale: float = 1.0) -> None:
        """Dibuja sobre `frame`, que puede estar agrandado `scale` veces respecto del original."""
        def P(pt):
            return (int(pt[0] * scale), int(pt[1] * scale))

        is_open = self.barrier.state == OPEN
        a, b = P(a), P(b)

        # Línea virtual.
        cv2.line(frame, a, b, YELLOW, 2)
        mid = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)  # rótulo en el medio de la línea
        lx = min(max(mid[0] + 6, 4), frame.shape[1] - 120)
        ly = min(max(mid[1] - 8, 14), frame.shape[0] - 6)
        cv2.putText(frame, "LINEA VIRTUAL", (lx, ly), cv2.FONT_HERSHEY_SIMPLEX, 0.45, YELLOW, 1)

        # Brazo de la barrera simulada: sale del extremo b de la línea y la cubre
        # cuando está cerrada; abierta, gira 80° (sirve para líneas en cualquier ángulo).
        vx, vy = a[0] - b[0], a[1] - b[1]
        length = max(1.0, (vx * vx + vy * vy) ** 0.5)
        ux, uy = vx / length, vy / length
        if is_open:
            c, s_ = np.cos(np.radians(80)), np.sin(np.radians(80))
            ux, uy = ux * c - uy * s_, ux * s_ + uy * c
        arm = 0.55 * length
        end = (int(b[0] + ux * arm), int(b[1] + uy * arm))
        cv2.line(frame, b, end, WHITE, 7)
        cv2.line(frame, b, end, RED, 3)
        cv2.rectangle(frame, (b[0] - 8, b[1] - 8), (b[0] + 8, b[1] + 8), (80, 80, 80), -1)

        # Cajas de los vehículos.
        active = [t for t in tracks.values() if not t.missed]
        for t in active:
            d = t.detection
            cv2.rectangle(frame, P((d.x, d.y)), P((d.x + d.w, d.y + d.h)), GREEN, 2)
            label = f"#{t.id} {d.label}" + (f" {d.confidence:.0%}" if d.confidence < 1 else "")
            x, y = P((d.x, d.y))
            cv2.putText(frame, label, (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREEN, 2)
            cv2.circle(frame, P(t.centroid), 4, YELLOW, -1)
            if len(t.history) > 1:
                cv2.polylines(frame, [np.array([P(p) for p in t.history], np.int32)], False, YELLOW, 1)

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
