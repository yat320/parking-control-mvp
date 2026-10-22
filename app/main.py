"""Servidor web del MVP (FastAPI).

    uvicorn app.main:app --reload        # y abrir http://localhost:8000

Rutas:
  GET  /                  panel web
  GET  /video_feed        video procesado en vivo (MJPEG)
  GET  /api/estado        barrera, contadores, progreso del procesamiento
  GET  /api/eventos       últimos eventos (?limit=50)
  POST /api/procesar      arranca a procesar el video (en un hilo aparte)
  POST /api/subir         sube un video propio (form: video, orientacion, invertir, patentes;
                          opcional frontal + desfase: la cámara frontal que lee las patentes)
  GET  /api/patente/{id}.jpg  foto recortada de la patente de un evento
  POST /api/detener       corta el procesamiento
  POST /api/tarifa        {"tarifa_hora": 1000, "fraccion_min": 15}
  POST /api/barrera/abrir apertura manual (como el botón de la cabina)
  POST /api/reset         borra los eventos y cierra la barrera
  GET  /juego             simulador jugable: manejás los autos y la partida se procesa
  POST /api/juego         {"autos": [{"color": "#c83232", "entra": 1.0, "sale": 12.5, "patente": "AB123CD"}, ...],
                           "patentes": true}  (con patentes arma también la cámara frontal)
                          arma el video de la partida en videos/ y devuelve su nombre
"""
import json
import threading
from contextlib import asynccontextmanager
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import config, database, juego, uploads
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


VIDEO_EXTS = uploads.ALLOWED
_current_video = config.VIDEO_PATH.name


def _list_videos() -> list[dict]:
    """Videos en la carpeta videos/, con la descripción de su .json si tiene."""
    folder = config.VIDEO_PATH.parent
    videos, frontales = [], set()
    for p in sorted(folder.iterdir()) if folder.exists() else []:
        if p.suffix.lower() not in VIDEO_EXTS or p.name.startswith("."):  # ".x.mp4": a medio escribir
            continue
        desc = ""
        if p.with_suffix(".json").exists():
            try:
                data = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
                desc = data.get("descripcion", "")
                if data.get("frontal"):
                    frontales.add(Path(str(data["frontal"])).name)
            except ValueError:
                desc = "(el .json de este video tiene un error)"
        videos.append({"nombre": p.name, "descripcion": desc})
    # La cámara frontal de un par no se procesa sola: va con el video de su barrera.
    return [v for v in videos if v["nombre"] not in frontales]


def _run_processing(video: Path, save_output: bool) -> None:
    global _last_error
    try:
        _last_error = ""
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
        "max_mb": config.UPLOAD_MAX_MB,
        "max_seg": config.UPLOAD_MAX_SECONDS,
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


@app.get("/juego", response_class=HTMLResponse)
def pagina_juego(request: Request):
    return templates.TemplateResponse(request, "juego.html", {
        "time_scale": config.TIME_SCALE,
        "constantes": juego.constants(),
    })


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
        "aviso": processor.notice,
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
    evs = database.list_events(min(max(limit, 1), 500))
    for e in evs:
        e["foto"] = bool(e.get("patente")) and (config.PLATES_DIR / f"{e['id']}.jpg").exists()
    return evs


@app.get("/api/patente/{event_id}.jpg")
def foto_patente(event_id: int):
    path = config.PLATES_DIR / f"{event_id}.jpg"
    if not path.exists():
        raise HTTPException(404, "Ese evento no tiene foto de patente")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


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
    _thread = threading.Thread(target=_run_processing, args=(config.VIDEO_PATH.parent / name, save), daemon=True)
    _thread.start()
    return {"ok": True}


@app.post("/api/subir")
def subir(request: Request, video: UploadFile = File(...), orientacion: str = Form("horizontal"),
          invertir: bool = Form(False), patentes: bool = Form(False),
          frontal: UploadFile | None = File(None), desfase: float = Form(0.0)):
    """Guarda un video propio en videos/ con su línea virtual; después se procesa como cualquiera."""
    size = int(request.headers.get("content-length") or 0)
    if size > (2 * config.UPLOAD_MAX_MB + 1) * 1024 * 1024:  # hasta dos videos (barrera + frontal)
        raise HTTPException(413, f"El video pesa más de {config.UPLOAD_MAX_MB} MB. Grabá uno más corto.")
    try:
        con_frontal = frontal is not None and bool(frontal.filename)
        name = uploads.save(video.file, video.filename, orientacion, invertir, patentes=patentes,
                            frontal=frontal.file if con_frontal else None,
                            frontal_name=frontal.filename if con_frontal else None, desfase=desfase)
    except uploads.UploadError as e:
        raise HTTPException(400, str(e))
    return {"nombre": name}


class AutoJuego(BaseModel):
    color: str = "#c83232"
    entra: float
    sale: float | None = None
    patente: str | None = None


class JuegoIn(BaseModel):
    autos: list[AutoJuego]
    patentes: bool = False  # armar también la cámara frontal que lee las patentes


@app.post("/api/juego")
def guardar_juego(body: JuegoIn):
    """Convierte la partida en un video de cámara cenital; después se procesa con /api/procesar."""
    try:
        name = juego.save([a.model_dump() for a in body.autos], patentes=body.patentes)
    except juego.JuegoError as e:
        raise HTTPException(400, str(e))
    return {"nombre": name}


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
