"""Servidor web del MVP (FastAPI).

    uvicorn app.main:app --reload        # y abrir http://localhost:8000

Rutas:
  GET  /                  panel web
  GET  /video_feed        video procesado en vivo (MJPEG)
  GET  /api/estado        barrera, contadores, progreso del procesamiento
  GET  /api/eventos       últimos eventos (?limit=50)
  POST /api/procesar      arranca a procesar el video (en un hilo aparte)
  POST /api/detener       corta el procesamiento
  POST /api/tarifa        {"tarifa_hora": 1000, "fraccion_min": 15}
  POST /api/barrera/abrir apertura manual (como el botón de la cabina)
  POST /api/reset         borra los eventos y cierra la barrera
"""
import threading
from contextlib import asynccontextmanager
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import config, database
from .barrier_controller import BarrierController
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


def _run_processing(save_output: bool) -> None:
    global _last_error
    try:
        _last_error = ""
        processor.process(config.VIDEO_PATH, config.OUTPUT_PATH if save_output else None, realtime=True)
    except Exception as e:  # se muestra en la web en vez de morir en silencio
        _last_error = str(e)
        print(f"[ERROR] {e}", flush=True)


def _manual_clock() -> float:
    return time.monotonic() - _manual_open_t0


# ---------- páginas ----------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "video": config.VIDEO_PATH.name,
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
        "progreso": round(processor.progress, 3),
        "detectados": processor.detected_now if processor.running else 0,
        "ultimo_mensaje": processor.last_message,
        "error": _last_error,
        "tarifa": database.get_pricing(),
        "stats": database.stats(),
        "adentro": database.list_inside(),
        "video": config.VIDEO_PATH.name,
        "hay_video_salida": config.OUTPUT_PATH.exists(),
        "hora": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/eventos")
def eventos(limit: int = 50):
    return database.list_events(min(max(limit, 1), 500))


class ProcesarIn(BaseModel):
    guardar_video: bool = True


@app.post("/api/procesar")
def procesar(body: ProcesarIn | None = None):
    global _thread
    if processor.running:
        raise HTTPException(409, "Ya se está procesando un video")
    if not config.VIDEO_PATH.exists():
        raise HTTPException(404, f"No existe {config.VIDEO_PATH}")
    save = body.guardar_video if body else True
    _thread = threading.Thread(target=_run_processing, args=(save,), daemon=True)
    _thread.start()
    return {"ok": True}


@app.post("/api/detener")
def detener():
    processor.stop()
    return {"ok": True}


class TarifaIn(BaseModel):
    tarifa_hora: float = Field(ge=0)
    fraccion_min: int = Field(default=15, ge=1, le=1440)


@app.post("/api/tarifa")
def tarifa(body: TarifaIn):
    database.set_pricing(body.tarifa_hora, body.fraccion_min)
    return database.get_pricing()


@app.post("/api/barrera/abrir")
def abrir_barrera():
    if processor.running:
        raise HTTPException(409, "La barrera la maneja el video mientras se procesa")
    barrier.open(_manual_clock(), reason="manual")
    return {"barrera": barrier.state}


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
