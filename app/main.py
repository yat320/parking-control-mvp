"""Servidor web del MVP (FastAPI).

    uvicorn app.main:app --reload        # y abrir http://localhost:8000

Rutas:
  GET  /                  panel web
  GET  /video_feed        video procesado en vivo (MJPEG)
  GET  /api/estado        barrera, contadores, progreso del procesamiento
  GET  /api/eventos       últimos eventos (?limit=50)
  POST /api/procesar      arranca a procesar el video (en un hilo aparte)
  POST /api/detener       corta el procesamiento
  GET  /api/eventos.csv   todos los eventos en CSV (se abre con Excel)
  POST /api/tarifa        {"tarifa_hora": 1000, "fraccion_min": 15, "tolerancia_min": 0, "tope_diario": 0}
  POST /api/barrera/abrir apertura manual (como el botón de la cabina)
  POST /api/linea         {"line": [x1, y1, x2, y2], "entry_direction": "down"} línea de la cámara en vivo
  POST /api/reset         borra los eventos y cierra la barrera
"""
import csv
import io
import json
import threading
from contextlib import asynccontextmanager
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import config, database
from .barrier_controller import BarrierController
from .camera import mask_source
from .demo_video import generate as generate_demo_video
from .video_processor import VideoProcessor

HERE = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app):
    database.init_db()
    if not config.VIDEO_PATH.exists():
        print(f"No existe {config.VIDEO_PATH}: genero el video de prueba.")
        generate_demo_video(config.VIDEO_PATH, 40)
    yield
    processor.stop()


app = FastAPI(title="Parking Control MVP", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")

barrier = BarrierController(config.BARRIER_OPEN_SECONDS)
processor = VideoProcessor(barrier=barrier)
_thread: threading.Thread | None = None
_last_error = ""
_manual_open_t0 = time.monotonic()  # reloj para la apertura manual (fuera del video)


VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv"}
CAMERA_NAME = "camara"  # nombre de la cámara en vivo en el selector (ningún archivo se llama así: no tiene extensión)
_current_video = config.VIDEO_PATH.name


def _list_videos() -> list[dict]:
    """Videos en la carpeta videos/, con la descripción de su .json si tiene."""
    folder = config.VIDEO_PATH.parent
    out = []
    if config.CAMERA:
        out.append({"nombre": CAMERA_NAME, "etiqueta": "Cámara en vivo", "en_vivo": True,
                    "descripcion": f"Cámara en vivo ({mask_source(config.CAMERA)})"})
    for p in sorted(folder.iterdir()) if folder.exists() else []:
        if p.suffix.lower() not in VIDEO_EXTS:
            continue
        desc = ""
        if p.with_suffix(".json").exists():
            try:
                desc = json.loads(p.with_suffix(".json").read_text(encoding="utf-8")).get("descripcion", "")
            except ValueError:
                desc = "(el .json de este video tiene un error)"
        out.append({"nombre": p.name, "descripcion": desc})
    return out


def _run_processing(video: Path | None, save_output: bool) -> None:
    """video=None procesa la cámara en vivo."""
    global _last_error
    try:
        _last_error = ""
        if video is None:
            linea = database.get_camera_line()
            processor.process_live(config.CAMERA, line=linea["line"], entry_direction=linea["entry_direction"])
        else:
            processor.process(video, config.OUTPUT_PATH if save_output else None, realtime=True)
    except Exception as e:  # se muestra en la web en vez de morir en silencio
        _last_error = str(e)
        print(f"[ERROR] {e}", flush=True)


def _manual_clock() -> float:
    return time.monotonic() - _manual_open_t0


# ---------- páginas ----------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "detector": config.DETECTOR,
        "time_scale": config.TIME_SCALE,
    })


@app.get("/video_feed")
def video_feed():
    """Stream MJPEG: el navegador lo muestra con un simple <img src="/video_feed">."""
    def frames():
        # El stream dura lo que dura el procesamiento (más unos segundos de
        # espera para que arranque); así no quedan conexiones colgadas y el
        # navegador se queda mostrando el último frame.
        last, deadline = None, time.monotonic() + 5
        while processor.running or time.monotonic() < deadline:
            with processor.lock:
                jpeg = processor.latest_jpeg
            if jpeg is not None and jpeg is not last:
                last = jpeg
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            if processor.running:
                deadline = time.monotonic() + 1
            time.sleep(0.03)
    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


# ---------- API ----------

@app.get("/api/estado")
def estado():
    if not processor.running:
        barrier.update(_manual_clock())  # cierra la apertura manual a tiempo
    return {
        "barrera": barrier.state,
        "procesando": processor.running,
        "en_vivo": processor.live,
        "sin_senal": processor.live and not processor.signal,
        "reconexiones": processor.reconnects if processor.live else 0,
        "linea_camara": database.get_camera_line() if config.CAMERA else None,
        "progreso": round(processor.progress, 3),
        "detectados": processor.detected_now if processor.running else 0,
        "ultimo_mensaje": processor.last_message,
        "error": _last_error,
        "tarifa": database.get_pricing(),
        "stats": database.stats(),
        "adentro": database.list_inside(),
        "video": _current_video,
        "videos": _list_videos(),
        "hay_video_salida": config.OUTPUT_PATH.exists(),
        "hora": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/eventos")
def eventos(limit: int = 50):
    return database.list_events(min(max(limit, 1), 500))


CSV_COLUMNS = ["id", "tipo_evento", "timestamp", "vehicle_id", "tracking_id",
               "estado_barrera", "duracion_seg", "monto", "entrada_id"]


@app.get("/api/eventos.csv")
def eventos_csv():
    """Todos los eventos para planilla. Con ; y BOM, que es como lo abre Excel en español."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for e in database.all_events():
        row = [e[c] for c in CSV_COLUMNS]
        for i in (6, 7):  # coma decimal
            if row[i] is not None:
                row[i] = f"{row[i]:.2f}".replace(".", ",")
        w.writerow(["" if v is None else v for v in row])
    nombre = f"eventos_{datetime.now():%Y-%m-%d}.csv"
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


class ProcesarIn(BaseModel):
    guardar_video: bool = True
    video: str | None = None  # nombre de un archivo de videos/ (por defecto, video_test.mp4)


@app.post("/api/procesar")
def procesar(body: ProcesarIn | None = None):
    global _thread, _current_video
    if processor.running:
        raise HTTPException(409, "Ya se está procesando un video")
    name = (body.video if body else None) or config.VIDEO_PATH.name
    # Solo se aceptan nombres de la lista (evita rutas como ../../algo).
    if name not in {v["nombre"] for v in _list_videos()}:
        raise HTTPException(404, f"No existe el video {name!r} en {config.VIDEO_PATH.parent}")
    _current_video = name
    save = body.guardar_video if body else True
    video = None if name == CAMERA_NAME else config.VIDEO_PATH.parent / name
    _thread = threading.Thread(target=_run_processing, args=(video, save), daemon=True)
    _thread.start()
    return {"ok": True}


@app.post("/api/detener")
def detener():
    processor.stop()
    return {"ok": True}


class TarifaIn(BaseModel):
    tarifa_hora: float = Field(ge=0)
    fraccion_min: int = Field(default=15, ge=1, le=1440)
    tolerancia_min: int | None = Field(default=None, ge=0, le=1440)  # None = no cambiar
    tope_diario: float | None = Field(default=None, ge=0)            # 0 = sin tope


@app.post("/api/tarifa")
def tarifa(body: TarifaIn):
    database.set_pricing(body.tarifa_hora, body.fraccion_min, body.tolerancia_min, body.tope_diario)
    return database.get_pricing()


@app.post("/api/barrera/abrir")
def abrir_barrera():
    if processor.running:
        raise HTTPException(409, "La barrera la maneja el video mientras se procesa")
    barrier.open(_manual_clock(), reason="manual")
    return {"barrera": barrier.state}


class LineaIn(BaseModel):
    line: list[float] = Field(min_length=4, max_length=4)  # x1, y1, x2, y2 en fracciones del cuadro
    entry_direction: str = Field(default="down", pattern="^(down|up)$")


@app.post("/api/linea")
def linea(body: LineaIn):
    """Guarda la línea virtual de la cámara y, si se está viendo, la aplica al toque."""
    if not config.CAMERA:
        raise HTTPException(409, "No hay cámara configurada (variable PARKING_CAMERA)")
    x1, y1, x2, y2 = body.line
    if not all(0 <= v <= 1 for v in body.line):
        raise HTTPException(422, "Los puntos van de 0 a 1 (fracción del ancho y del alto)")
    if abs(x2 - x1) + abs(y2 - y1) < 0.05:
        raise HTTPException(422, "Los dos puntos de la línea están demasiado cerca")
    database.set_camera_line(body.line, body.entry_direction)
    if processor.live:
        processor.set_line(body.line, body.entry_direction)
    return database.get_camera_line()


@app.post("/api/reset")
def reset():
    processor.stop()
    if _thread:
        _thread.join(timeout=5)
    database.reset_events()
    barrier.reset()
    processor.last_message = ""
    with processor.lock:
        processor.latest_jpeg = None
        processor.progress = 0.0
    return {"ok": True}
