"""Procesamiento del video: detección -> tracking -> línea virtual -> evento -> barrera.

Se puede usar de dos formas:
  - desde la web (app/main.py), que lo corre en un hilo y muestra los frames;
  - por consola:  python -m app.video_processor --video videos/video_test.mp4

Flujo por frame:
  1. el detector devuelve cajas de vehículos (MotionDetector o YoloDetector),
  2. el tracker les asigna un tracking_id estable,
  3. si el centroide de un track pasa de un lado al otro de la línea virtual,
     se registra ENTRADA (hacia abajo) o SALIDA (hacia arriba) en SQLite,
     con la patente si es una cámara frontal (app/plates.py),
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

from . import config, database, pairing, plates
from .barrier_controller import OPEN, BarrierController
from .demo_video import generate as generate_demo_video
from .detection import create_detector
from .pricing import format_duration
from .tracker import CentroidTracker

MIN_SEEN_FRAMES = 3   # un track tiene que verse al menos 3 frames para ser "válido"
MESSAGE_SECONDS = 2.0  # cuánto queda en pantalla "EVENTO REGISTRADO"
DISPLAY_MIN_WIDTH = 640  # ancho mínimo del video procesado (los más chicos se agrandan)

YELLOW, GREEN, RED, WHITE, BLACK = (0, 220, 255), (60, 200, 60), (40, 40, 230), (255, 255, 255), (0, 0, 0)
CYAN = (230, 200, 40)


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


def travel_toward(history, a, b, side: int) -> float:
    """Cuánto avanzó el auto (en px) hacia el lado `side` de la recta a->b en sus últimos centroides."""
    if len(history) < 2:
        return 0.0
    (ax, ay), (bx, by) = a, b
    nx, ny = -(by - ay), bx - ax                  # normal que apunta al lado +1 (ver side_of_line)
    norm = (nx * nx + ny * ny) ** 0.5 or 1.0
    (x0, y0), (x1, y1) = history[0], history[-1]
    return side * ((x1 - x0) * nx + (y1 - y0) * ny) / norm


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
        self.notice = ""  # aviso para la web (por ejemplo, que no se pueden leer patentes)
        self._stop = threading.Event()
        self._plate_boxes = []  # patentes vistas en la última lectura, para dibujarlas

    # ---------- API ----------

    def stop(self) -> None:
        self._stop.set()

    def process(self, video_path: Path, output_path: Path | None = None, realtime: bool = False,
                show: bool = False) -> list[dict]:
        """Procesa el video entero. Devuelve los eventos registrados (ver _process)."""
        # Ocupado desde ya: cargar los modelos de patentes tarda unos segundos y en ese
        # rato la web no tiene que dejar arrancar otro video (por ejemplo, con un doble toque).
        self.running = True
        try:
            return self._process(video_path, output_path, realtime, show)
        finally:
            self.running = False

    def _process(self, video_path: Path, output_path: Path | None, realtime: bool, show: bool) -> list[dict]:
        """Procesa el video entero. Devuelve los eventos registrados.

        Si al lado del video hay un .json con el mismo nombre (por ejemplo
        videos/calle_real.json), sus valores de línea, sentido y área mínima
        reemplazan a los de config.py solo para ese video.
        """
        opts = config.video_settings(Path(video_path), {
            "line": self.line_rel, "entry_sign": self.entry_sign, "min_area": self.min_area,
            "plates": config.PLATES, "frontal": None, "offset": 0.0, "ocr": None, "plate_detector": None,
            "frontal_lanes": None})
        line_rel, entry_sign, min_area = opts["line"], opts["entry_sign"], opts["min_area"]
        want_plates = opts["plates"] or bool(opts["frontal"])
        reader = plates.create_reader(opts["ocr"], opts["plate_detector"]) if want_plates else None
        self.notice = ("No se pueden leer patentes en este servidor: los autos quedan con número."
                       if want_plates and reader is None else "")
        self._plate_boxes = []
        # Con cámara frontal, las patentes se leen ahí; si no, en este mismo video.
        frontal = pairing.FrontalFile(opts["frontal"], opts["offset"], reader, live=realtime,
                                      lanes=opts["frontal_lanes"]) \
            if opts["frontal"] and reader else None
        same_cam = reader if not frontal else None
        same_async = plates.AsyncPlates(same_cam) if same_cam and realtime else None
        pending_pair = []  # (evento, segundo) registrados sin patente, esperando que la frontal la vea
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise FileNotFoundError(f"No se pudo abrir el video: {video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        # Los videos grandes (celular en 1080p o 4K) se achican antes de todo: la
        # detección, el tracking y el dibujo trabajan sobre el frame achicado.
        shrink, plate_scale = None, 1.0
        if config.PROCESS_MAX_WIDTH and w > config.PROCESS_MAX_WIDTH:
            plate_scale = config.PROCESS_MAX_WIDTH / w  # las patentes se leen en el original, más nítido
            shrink = (config.PROCESS_MAX_WIDTH, max(1, round(h * config.PROCESS_MAX_WIDTH / w)))
            w, h = shrink
        min_travel = config.CROSS_MIN_TRAVEL * max(w, h)  # px que tiene que venir avanzando al cruzar
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

        detector = create_detector(self.detector_kind, min_area, fps)
        tracker = CentroidTracker(max_distance=max(w, h) * 0.2)
        self.barrier.reset()
        start_wall = datetime.now()  # hora "real" que corresponde al segundo 0 del video
        events, message, message_until = [], "", -1.0
        self._stop.clear()
        self.running = True
        frame_idx, t0 = 0, time.monotonic()
        cams = f"cámara frontal {Path(opts['frontal']).name} (desfase {opts['offset']:+g} s)" if frontal else \
            ("sí" if same_cam else "no")
        print(f"[VIDEO] {video_path} {w}x{h} @ {fps:.1f} fps, detector={detector.name}, "
              f"línea={tuple(line_rel)}, área mínima={min_area}, patentes={cams}", flush=True)

        skipped = 0
        try:
            while not self._stop.is_set():
                if realtime and frame_idx:
                    # En vivo nunca se va más lento que el video: si el procesamiento se
                    # atrasó (servidor chico), se saltean cuadros hasta alcanzarlo. El
                    # detector y el tracker toleran huecos; lo que se pierde es fluidez.
                    behind = int((time.monotonic() - t0) * fps) - frame_idx
                    for _ in range(min(behind, int(fps))):  # a lo sumo 1 s de golpe
                        if not cap.grab():
                            break
                        frame_idx += 1
                        skipped += 1
                ok, frame = cap.read()
                if not ok:
                    break
                original = frame
                if shrink:
                    frame = cv2.resize(frame, shrink, interpolation=cv2.INTER_AREA)
                video_t = frame_idx / fps
                frame_idx += 1

                detections = detector.detect(frame)
                tracks = tracker.update(detections)
                self.barrier.update(video_t)

                # Una sola cámara que también ve las patentes: leerlas cada N frames, si hay autos.
                # En vivo, en otro hilo: se usan las lecturas que van terminando.
                if same_cam:
                    done = same_async.results() if same_async else []
                    if frame_idx % config.PLATES_EVERY == 0 and any(not t.missed for t in tracks.values()):
                        if same_async:
                            same_async.submit(original.copy(), video_t)
                        else:
                            done = [(same_cam.read(original), video_t)]
                    for reads, _ in done:
                        plates.assign(reads, tracks, plate_scale)
                        self._plate_boxes = [(tuple(int(v * plate_scale) for v in r.box), r.text) for r in reads]
                    if done:
                        self._fill_late_plates(tracks, video_t)

                # Dos cámaras: la frontal avanza a la par y anota las patentes que ve.
                if frontal:
                    frontal.advance(video_t, busy=any(not t.missed for t in tracks.values()))
                    pending_pair = self._fill_pair_plates(frontal.timeline, pending_pair, video_t)

                for tr in tracks.values():
                    if tr.missed:
                        continue
                    side = side_of_line(tr.centroid, a, b)
                    if side == 0:
                        continue
                    prev, tr.side = tr.side, side
                    # Cruce: el lado cambió, el vehículo es "válido" (visto varios frames) y viene
                    # avanzando hacia ese lado (no es un salto del centro, por ejemplo cuando el
                    # brazo de la barrera se pega a la mancha del auto).
                    if prev != 0 and prev != side and tr.seen >= MIN_SEEN_FRAMES \
                            and travel_toward(tr.history, a, b, side) >= min_travel:
                        kind = "entrada" if side == entry_sign else "salida"
                        if kind in tr.counted:
                            continue
                        tr.counted.add(kind)
                        # La barrera se abre ANTES de registrar, así el evento guarda "abierta".
                        self.barrier.open(video_t, reason=f"{kind} track {tr.id}")
                        ts = start_wall + timedelta(seconds=video_t * self.time_scale)
                        plate, crop = None, None
                        if frontal:
                            seen = frontal.timeline.claim(video_t, kind)
                            if seen:
                                plate, crop = seen.plate, seen.crop
                        elif same_cam:
                            plate = tr.plates.best()
                            crop = tr.plates.crop(plate) if plate else None
                        who = plates.pretty(plate) if plate else None
                        if kind == "entrada":
                            ev = database.register_entry(ts, tr.id, self.barrier.state, plate)
                            message = f"EVENTO REGISTRADO: ENTRADA {who or ev['vehicle_id']}"
                        else:
                            ev = database.register_exit(ts, tr.id, self.barrier.state, plate)
                            extra = ""
                            if ev["monto"] is not None:
                                extra = f" {format_duration(ev['duracion_seg'])} ${ev['monto']:.0f}"
                            message = f"EVENTO REGISTRADO: SALIDA {who or ev['vehicle_id']}{extra}"
                        if plate:
                            save_plate_photo(ev["id"], crop)
                        elif frontal:
                            pending_pair.append((ev["id"], video_t, kind))  # la frontal la puede ver enseguida
                        elif same_cam:
                            tr.pending_plate.append((ev["id"], video_t))  # se completa si se lee enseguida
                        message_until = video_t + MESSAGE_SECONDS
                        ev["video_seg"] = round(video_t, 2)
                        events.append(ev)
                        print(f"[EVENTO] t={video_t:6.2f}s {message}", flush=True)

                shown_msg = message if video_t < message_until else ""
                view = cv2.resize(frame, out_size, interpolation=cv2.INTER_CUBIC) if scale > 1 else frame
                self._draw(view, tracks, a, b, video_t, shown_msg, scale)
                if frontal:
                    self._draw_inset(view, frontal.inset(view.shape[1] // 3))
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
            if frontal:
                frontal.release()
            if same_async:
                same_async.close()
            if writer:
                writer.release()
            if show:
                cv2.destroyAllWindows()
            self.barrier.reset()
            self.running = False
        print(f"[VIDEO] fin: {frame_idx} frames, {len(events)} eventos"
              + (f", {skipped} cuadros salteados para ir en vivo" if skipped else ""), flush=True)
        if output_path:
            print(f"[VIDEO] video procesado: {output_path}", flush=True)
        return events

    @staticmethod
    def _fill_late_plates(tracks, now: float) -> None:
        """Eventos que se registraron antes de leerle la patente al auto: se completan ahora,
        si no pasaron más de PLATES_LATE_SECONDS (después podría ser otro auto con el mismo track)."""
        for t in tracks.values():
            t.pending_plate[:] = [(i, s) for i, s in t.pending_plate if now - s <= config.PLATES_LATE_SECONDS]
            plate = t.plates.best() if t.pending_plate else None
            if plate:
                for ev_id, _ in t.pending_plate:
                    database.set_plate(ev_id, plate)
                    save_plate_photo(ev_id, t.plates.crop(plate))
                t.pending_plate.clear()

    @staticmethod
    def _fill_pair_plates(timeline, pending: list, now: float) -> list:
        """Eventos sin patente: si la frontal la vio en los PAIR_AFTER segundos siguientes, se completan."""
        left = []
        for ev_id, t, kind in pending:
            seen = timeline.claim(t, kind)
            if seen:
                database.set_plate(ev_id, seen.plate)
                save_plate_photo(ev_id, seen.crop)
            elif now - t <= config.PAIR_AFTER:
                left.append((ev_id, t, kind))
        return left

    @staticmethod
    def _draw_inset(view: np.ndarray, inset) -> None:
        """La cámara frontal en un recuadro arriba a la derecha (para ver que van sincronizadas)."""
        if inset is None:
            return
        h, w = inset.shape[:2]
        x, y = view.shape[1] - w - 8, 30
        if y + h + 4 > view.shape[0]:
            return
        cv2.rectangle(view, (x - 2, y - 2), (x + w + 1, y + h + 1), WHITE, 2)
        view[y:y + h, x:x + w] = inset
        cv2.putText(view, "FRONTAL", (x + 4, y + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, WHITE, 2)

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
            plate = t.plates.best()
            label = f"#{t.id} " + (plates.pretty(plate) if plate else d.label + (f" {d.confidence:.0%}" if d.confidence < 1 else ""))
            x, y = P((d.x, d.y))
            cv2.putText(frame, label, (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREEN, 2)
            cv2.circle(frame, P(t.centroid), 4, YELLOW, -1)
            if len(t.history) > 1:
                cv2.polylines(frame, [np.array([P(p) for p in t.history], np.int32)], False, YELLOW, 1)

        # Patentes vistas en la última lectura.
        for (x, y, bw, bh), text in self._plate_boxes:
            cv2.rectangle(frame, P((x, y)), P((x + bw, y + bh)), CYAN, 2)
            px, py = P((x, y + bh))
            cv2.putText(frame, text, (px, py + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, CYAN, 2)

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


def save_plate_photo(event_id: int, crop) -> None:
    """Guarda el recorte de la patente como data/patentes/<evento>.jpg (lo muestra la web)."""
    if crop is None or not crop.size:
        return
    config.PLATES_DIR.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(config.PLATES_DIR / f"{event_id}.jpg"), crop)


def main() -> None:
    parser = argparse.ArgumentParser(description="Procesa un video y registra entradas/salidas.")
    parser.add_argument("--video", type=Path, default=config.VIDEO_PATH)
    parser.add_argument("--output", type=Path, default=config.OUTPUT_PATH)
    parser.add_argument("--no-output", action="store_true", help="no guardar el video procesado")
    parser.add_argument("--detector", default=config.DETECTOR, choices=["motion", "yolo"])
    parser.add_argument("--realtime", action="store_true", help="procesar a la velocidad del video")
    parser.add_argument("--show", action="store_true", help="mostrar ventana (requiere opencv-python, no headless)")
    parser.add_argument("--reset", action="store_true", help="borrar los eventos antes de empezar")
    parser.add_argument("--patentes", action="store_true", help="cámara frontal: leer patentes")
    args = parser.parse_args()

    if args.patentes:
        config.PLATES = True
    database.init_db()
    if not args.video.exists() and args.video == config.VIDEO_PATH:
        print(f"No existe {args.video}: genero el video de prueba.")
        generate_demo_video(args.video, 40)
    if args.reset:
        database.reset_events()
    proc = VideoProcessor(detector_kind=args.detector)
    events = proc.process(args.video, None if args.no_output else args.output, realtime=args.realtime, show=args.show)
    s = database.stats()
    for e in reversed(database.list_events(len(events) or 1)):
        if e.get("patente"):
            print(f"  {e['tipo_evento']:7} {e['vehicle_id']} patente {plates.pretty(e['patente'])}")
    print(f"\nResumen: {len(events)} eventos en este video | entradas {s['entradas']} | "
          f"salidas {s['salidas']} | adentro {s['adentro']} | recaudado ${s['recaudado']:.2f}")


if __name__ == "__main__":
    main()
