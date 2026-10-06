"""Baja un video real de tránsito para probar el sistema con imágenes de verdad.

    python tools/descargar_video_real.py

Deja videos/calle_real.avi (12,5 s, 320x176, una calle vista desde arriba con
5 autos que cruzan de izquierda a derecha) y videos/calle_real.json con la
línea virtual vertical que le corresponde. Después elegilo en la web o corré:

    python -m app.video_processor --video videos/calle_real.avi --reset

Fuente: dataset del proyecto simple_vehicle_counting de Andrews Sobral
(https://github.com/andrewssobral/simple_vehicle_counting). Ese repo no declara
licencia, por eso el video no se incluye en este repositorio: se baja solo
para pruebas locales.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = "https://github.com/andrewssobral/simple_vehicle_counting"
RAW = "https://raw.githubusercontent.com/andrewssobral/simple_vehicle_counting/master/dataset/video.avi"
VIDEOS = Path(__file__).resolve().parent.parent / "videos"
DEST = VIDEOS / "calle_real.avi"

SETTINGS = {
    "descripcion": "Calle real vista desde arriba: 5 autos cruzan de izquierda a derecha (entradas).",
    "fuente": REPO,
    # Línea vertical en el medio, dibujada de abajo hacia arriba: así cruzar
    # de izquierda a derecha cuenta como ENTRADA y al revés como SALIDA.
    "line": [0.5, 0.95, 0.5, 0.05],
    "entry_direction": "down",
    # El video es chico (320x176): los autos ocupan ~1000 px, el mínimo baja.
    "min_area": 300,
}


def download() -> None:
    try:
        urllib.request.urlretrieve(RAW, DEST)
        if DEST.stat().st_size > 100_000:
            return
    except Exception as e:
        print(f"Descarga directa falló ({e}); pruebo con git clone...")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", "-q", "--depth", "1", REPO, tmp], check=True)
        shutil.copy(Path(tmp) / "dataset" / "video.avi", DEST)


def main() -> None:
    VIDEOS.mkdir(parents=True, exist_ok=True)
    if not DEST.exists() or DEST.stat().st_size < 100_000:
        download()
    DEST.with_suffix(".json").write_text(json.dumps(SETTINGS, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Listo: {DEST} ({DEST.stat().st_size // 1024} KB) y {DEST.with_suffix('.json').name}")


if __name__ == "__main__":
    sys.exit(main())
