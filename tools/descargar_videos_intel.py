"""Baja dos videos reales de estacionamiento, con licencia libre, para probar el sistema.

    python tools/descargar_videos_intel.py

Son videos de muestra de Intel (https://github.com/intel-iot-devkit/sample-videos),
licencia CC BY 4.0: se pueden usar y publicar citando la fuente.

- videos/intel_estacionamiento.mp4: cámara fija desde arriba sobre un carril de un
  estacionamiento real; los autos pasan en los dos sentidos. Es el mismo encuadre que el
  video de prueba: la línea va horizontal en el medio.
- videos/intel_peatones.mp4: un estacionamiento con un auto, una persona y una bicicleta.
  Sirve para ver que el sistema no cuente personas ni bicis como autos.

Cada uno queda con su .json (línea, sentido, descripción y fuente).
"""
import json
import sys
import urllib.request
from pathlib import Path

VIDEOS = Path(__file__).resolve().parent.parent / "videos"
SOURCE = "https://github.com/intel-iot-devkit/sample-videos"
CREDIT = "Video: Intel IoT Devkit sample-videos (CC BY 4.0)"
URL = "https://raw.githubusercontent.com/intel-iot-devkit/sample-videos/master/{}.mp4"

VIDEOS_INTEL = {
    "intel_estacionamiento": {
        "archivo": "car-detection",
        "descripcion": "Estacionamiento real visto desde arriba: autos que pasan en los dos sentidos. " + CREDIT,
        "fuente": SOURCE, "licencia": "CC BY 4.0",
        "line": [0.05, 0.5, 0.95, 0.5], "entry_direction": "down",
    },
    "intel_peatones": {
        "archivo": "person-bicycle-car-detection",
        "descripcion": "Estacionamiento real con un auto, una persona y una bicicleta: "
                       "solo el auto tiene que contar. " + CREDIT,
        "fuente": SOURCE, "licencia": "CC BY 4.0",
        "line": [0.05, 0.5, 0.95, 0.5], "entry_direction": "down",
    },
}


def main() -> None:
    VIDEOS.mkdir(parents=True, exist_ok=True)
    for name, info in VIDEOS_INTEL.items():
        dest = VIDEOS / f"{name}.mp4"
        if not dest.exists() or dest.stat().st_size < 100_000:
            urllib.request.urlretrieve(URL.format(info["archivo"]), dest)
        if dest.stat().st_size < 100_000:
            raise SystemExit(f"{dest.name}: la descarga no trajo el video ({dest.stat().st_size} bytes)")
        settings = {k: v for k, v in info.items() if k != "archivo"}
        dest.with_suffix(".json").write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Listo: {dest} ({dest.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    sys.exit(main())
