"""Pruebas del MVP:  python -m unittest discover tests -v"""
import os
import tempfile
import unittest
from pathlib import Path

# Base y videos en una carpeta temporal, para no tocar data/parking.db.
TMP = Path(tempfile.mkdtemp())
os.environ["PARKING_DB"] = str(TMP / "test.db")

from app import config, database  # noqa: E402
from app.barrier_controller import CLOSED, OPEN, BarrierController  # noqa: E402
from app.demo_video import generate  # noqa: E402
from app.pricing import calculate_amount  # noqa: E402
from app.video_processor import VideoProcessor, side_of_line  # noqa: E402


class PricingTest(unittest.TestCase):
    def test_fracciones(self):
        self.assertEqual(calculate_amount(60, 1000, 15), 250)        # mínimo una fracción
        self.assertEqual(calculate_amount(15 * 60, 1000, 15), 250)   # justo una
        self.assertEqual(calculate_amount(20 * 60, 1000, 15), 500)   # dos iniciadas
        self.assertEqual(calculate_amount(3600, 1200, 60), 1200)
        self.assertEqual(calculate_amount(3600, 0, 15), 0)


class BarrierTest(unittest.TestCase):
    def test_abre_y_cierra_sola(self):
        b = BarrierController(open_seconds=3)
        self.assertEqual(b.state, CLOSED)
        b.open(10.0)
        self.assertEqual(b.update(12.9), OPEN)
        self.assertEqual(b.update(13.0), CLOSED)

    def test_reabrir_extiende(self):
        b = BarrierController(open_seconds=3)
        b.open(0)
        b.open(2)
        self.assertEqual(b.update(4), OPEN)
        self.assertEqual(b.update(5), CLOSED)


class LineTest(unittest.TestCase):
    def test_lados(self):
        a, b = (0, 100), (200, 100)
        self.assertEqual(side_of_line((50, 50), a, b), -1)   # arriba
        self.assertEqual(side_of_line((50, 150), a, b), 1)   # abajo
        self.assertEqual(side_of_line((300, 150), a, b), 0)  # fuera del segmento


class EndToEndTest(unittest.TestCase):
    """Procesa el video de prueba: 3 autos entran y los 3 salen."""

    def test_video_demo(self):
        video = TMP / "demo.mp4"
        generate(video, 40)
        database.init_db()
        database.reset_events()
        database.set_pricing(1000, 15)
        events = VideoProcessor(detector_kind="motion", time_scale=60).process(video, TMP / "out.mp4")
        tipos = [e["tipo_evento"] for e in events]
        self.assertEqual(tipos, ["entrada", "entrada", "salida", "entrada", "salida", "salida"])
        s = database.stats()
        self.assertEqual((s["entradas"], s["salidas"], s["adentro"]), (3, 3, 0))
        salidas = [e for e in database.list_events() if e["tipo_evento"] == "salida"]
        self.assertTrue(all(e["monto"] and e["monto"] > 0 for e in salidas))
        self.assertTrue(all(e["estado_barrera"] == "abierta" for e in database.list_events()))
        self.assertTrue((TMP / "out.mp4").stat().st_size > 0)


class VideoSettingsTest(unittest.TestCase):
    def test_json_al_lado_del_video(self):
        video = TMP / "cam.mp4"
        defaults = {"line": (0, 0.5, 1, 0.5), "entry_sign": 1, "min_area": 2500}
        self.assertEqual(config.video_settings(video, defaults), defaults)  # sin .json
        video.with_suffix(".json").write_text('{"line": [0.5, 1, 0.5, 0], "entry_direction": "up"}')
        opts = config.video_settings(video, defaults)
        self.assertEqual(opts["line"], (0.5, 1.0, 0.5, 0.0))
        self.assertEqual(opts["entry_sign"], -1)
        self.assertEqual(opts["min_area"], 2500)


class UploadTest(unittest.TestCase):
    """Videos subidos desde la web: se guardan con su .json, se validan y se recortan."""

    @classmethod
    def setUpClass(cls):
        cls.demo = TMP / "subir_demo.mp4"
        if not cls.demo.exists():
            generate(cls.demo, 40)

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def subir(self, nombre="mio.mp4", orientacion="horizontal", invertir=False, datos=None):
        import io
        from app import uploads
        datos = self.demo.read_bytes() if datos is None else datos
        return uploads.save(io.BytesIO(datos), nombre, orientacion, invertir, folder=self.folder)

    def test_guarda_video_y_linea(self):
        import json
        nombre = self.subir(orientacion="vertical", invertir=True)
        self.assertTrue(nombre.startswith("subido-") and nombre.endswith(".mp4"))
        opts = json.loads((self.folder / nombre).with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(opts["line"], [0.5, 0.95, 0.5, 0.05])
        self.assertEqual(opts["entry_direction"], "up")
        self.assertIn("40 s", opts["descripcion"])

    def test_rechaza_lo_que_no_sirve(self):
        from app import uploads
        with self.assertRaises(uploads.UploadError):
            self.subir(nombre="foto.jpg")
        with self.assertRaises(uploads.UploadError):
            self.subir(datos=b"esto no es un video")
        maximo = config.UPLOAD_MAX_SECONDS
        config.UPLOAD_MAX_SECONDS = 10
        try:
            with self.assertRaises(uploads.UploadError):
                self.subir()  # dura 40 s
        finally:
            config.UPLOAD_MAX_SECONDS = maximo
        self.assertEqual(list(self.folder.iterdir()), [])  # no deja archivos a medias

    def test_guarda_solo_los_ultimos(self):
        import os as _os
        guardar = config.UPLOAD_KEEP
        config.UPLOAD_KEEP = 2
        try:
            nombres = []
            for i in range(3):
                nombres.append(self.subir())
                _os.utime(self.folder / nombres[-1], (1000 + i, 1000 + i))  # el orden no depende del reloj
            from app import uploads
            uploads.prune(self.folder)
            quedan = sorted(p.name for p in self.folder.glob("subido-*.mp4"))
            self.assertEqual(quedan, sorted(nombres[1:]))
            self.assertFalse((self.folder / nombres[0]).with_suffix(".json").exists())
        finally:
            config.UPLOAD_KEEP = guardar

    def test_video_grande_se_achica(self):
        """Un video de celular en 1280x720 da los mismos eventos que el original."""
        import cv2
        grande = TMP / "grande.mp4"
        cap = cv2.VideoCapture(str(self.demo))
        fps = cap.get(cv2.CAP_PROP_FPS)
        out = cv2.VideoWriter(str(grande), cv2.VideoWriter_fourcc(*"mp4v"), fps, (1280, 720))
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            out.write(cv2.resize(frame, (1280, 720)))
        cap.release()
        out.release()
        database.init_db()
        database.reset_events()
        events = VideoProcessor(detector_kind="motion", time_scale=60).process(grande)
        self.assertEqual([e["tipo_evento"] for e in events],
                         ["entrada", "entrada", "salida", "entrada", "salida", "salida"])


REAL_VIDEO = Path(__file__).resolve().parent.parent / "videos" / "calle_real.avi"


@unittest.skipUnless(REAL_VIDEO.exists(), "falta el video real: python tools/descargar_video_real.py")
class RealVideoTest(unittest.TestCase):
    """Video real de una calle: cruzan 5 autos de izquierda a derecha."""

    def test_cinco_entradas(self):
        database.init_db()
        database.reset_events()
        events = VideoProcessor(detector_kind="motion").process(REAL_VIDEO, TMP / "real.mp4")
        self.assertEqual([e["tipo_evento"] for e in events], ["entrada"] * 5)


if __name__ == "__main__":
    unittest.main()
