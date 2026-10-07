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

    def test_tolerancia(self):
        self.assertEqual(calculate_amount(5 * 60, 1000, 15, tolerance_minutes=10), 0)
        self.assertEqual(calculate_amount(10 * 60, 1000, 15, tolerance_minutes=10), 0)   # justo en el límite
        self.assertEqual(calculate_amount(11 * 60, 1000, 15, tolerance_minutes=10), 250)  # se cobra completa
        self.assertEqual(calculate_amount(5 * 60, 1000, 15, tolerance_minutes=0), 250)    # desactivada

    def test_tope_diario(self):
        h = 3600
        self.assertEqual(calculate_amount(2 * h, 1000, 15, daily_cap=5000), 2000)    # no llega al tope
        self.assertEqual(calculate_amount(10 * h, 1000, 15, daily_cap=5000), 5000)   # topeado
        self.assertEqual(calculate_amount(24 * h, 1000, 15, daily_cap=5000), 5000)   # un día justo
        self.assertEqual(calculate_amount(26 * h, 1000, 15, daily_cap=5000), 7000)   # un día + 2 h
        self.assertEqual(calculate_amount(40 * h, 1000, 15, daily_cap=5000), 10000)  # dos topes
        self.assertEqual(calculate_amount(10 * h, 1000, 15, daily_cap=0), 10000)     # sin tope


class ApiTest(unittest.TestCase):
    def setUp(self):
        from datetime import datetime
        from fastapi.testclient import TestClient
        from app.main import app
        database.init_db()
        database.reset_events()
        database.set_pricing(1000, 15, 0, 0)
        self.dt, self.client = datetime, TestClient(app)

    def test_tarifa_guarda_tolerancia_y_tope(self):
        r = self.client.post("/api/tarifa", json={"tarifa_hora": 1200, "fraccion_min": 30,
                                                   "tolerancia_min": 10, "tope_diario": 8000})
        self.assertEqual(r.json(), {"tarifa_hora": 1200.0, "fraccion_min": 30,
                                    "tolerancia_min": 10, "tope_diario": 8000.0})
        # El formato viejo (sin los campos nuevos) no los pisa.
        r = self.client.post("/api/tarifa", json={"tarifa_hora": 1000, "fraccion_min": 15})
        self.assertEqual((r.json()["tolerancia_min"], r.json()["tope_diario"]), (10, 8000.0))
        self.assertEqual(self.client.post("/api/tarifa", json={"tarifa_hora": 1, "tolerancia_min": -1}).status_code, 422)

    def test_salida_dentro_de_la_tolerancia_no_cobra(self):
        database.set_pricing(1000, 15, 10, 0)
        database.register_entry(self.dt(2026, 1, 1, 10, 0), 1, "abierta")
        salida = database.register_exit(self.dt(2026, 1, 1, 10, 8), 1, "abierta")
        self.assertEqual(salida["monto"], 0)

    def test_csv(self):
        database.register_entry(self.dt(2026, 1, 1, 10, 0), 1, "abierta")
        database.register_exit(self.dt(2026, 1, 1, 10, 20), 1, "abierta")
        r = self.client.get("/api/eventos.csv")
        self.assertIn("attachment", r.headers["content-disposition"])
        lineas = r.content.decode("utf-8-sig").strip().split("\r\n")
        self.assertEqual(lineas[0].split(";")[:3], ["id", "tipo_evento", "timestamp"])
        self.assertEqual(len(lineas), 3)
        self.assertEqual(lineas[2].split(";")[6:8], ["1200,00", "500,00"])


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
        database.set_pricing(1000, 15, 0, 0)
        events = VideoProcessor(detector_kind="motion", time_scale=60).process(video, TMP / "out.mp4")
        tipos = [e["tipo_evento"] for e in events]
        self.assertEqual(tipos, ["entrada", "entrada", "salida", "entrada", "salida", "salida"])
        s = database.stats()
        self.assertEqual((s["entradas"], s["salidas"], s["adentro"]), (3, 3, 0))
        salidas = [e for e in database.list_events() if e["tipo_evento"] == "salida"]
        self.assertTrue(all(e["monto"] and e["monto"] > 0 for e in salidas))
        self.assertTrue(all(e["estado_barrera"] == "abierta" for e in database.list_events()))
        self.assertTrue((TMP / "out.mp4").stat().st_size > 0)


class CameraTest(unittest.TestCase):
    """Cámara en vivo, simulada con el video de prueba: a mitad de camino se corta y vuelve."""

    def test_fuente(self):
        from app.camera import mask_source, parse_source
        self.assertEqual(parse_source("0"), 0)
        self.assertEqual(parse_source(" rtsp://cam/1 "), "rtsp://cam/1")
        self.assertEqual(mask_source("rtsp://admin:clave123@192.168.1.50:554/s1"), "rtsp://***@192.168.1.50:554/s1")

    def test_camara_que_no_responde(self):
        class Muerta:
            def __init__(self, src): pass
            def isOpened(self): return False
            def release(self): pass
        with self.assertRaises(ConnectionError) as ctx:
            VideoProcessor(detector_kind="motion").process_live("rtsp://u:secreta@cam/1", capture_factory=Muerta)
        self.assertNotIn("secreta", str(ctx.exception))

    def test_eventos_y_reconexion(self):
        import cv2
        video = TMP / "camara.mp4"
        generate(video, 40)
        database.init_db()
        database.reset_events()
        database.set_pricing(1000, 15, 0, 0)
        proc = VideoProcessor(detector_kind="motion")
        real, estado = cv2.VideoCapture(str(video)), {"leidos": 0, "aperturas": 0}

        class CamaraFalsa:
            """Sirve los cuadros del video; la primera conexión se cae en el cuadro 400."""
            def __init__(self, src):
                estado["aperturas"] += 1
                self.n, self.viva = estado["aperturas"], True
            def isOpened(self): return self.viva
            def get(self, prop): return real.get(prop)
            def release(self): self.viva = False
            def read(self):
                if self.n == 1 and estado["leidos"] == 400:
                    return False, None
                ok, frame = real.read()
                if not ok:
                    proc.stop()  # se terminó la "transmisión"
                    return False, None
                estado["leidos"] += 1
                return True, frame

        events = proc.process_live("rtsp://falsa", capture_factory=CamaraFalsa, retry_seconds=0.01, drop_frames=False)
        real.release()
        self.assertEqual([e["tipo_evento"] for e in events],
                         ["entrada", "entrada", "salida", "entrada", "salida", "salida"])
        self.assertEqual(proc.reconnects, 1)
        self.assertEqual(estado["leidos"], 800)
        self.assertFalse(proc.running or proc.live)
        self.assertEqual(database.stats()["adentro"], 0)

    def test_linea_desde_la_web(self):
        from unittest import mock
        from fastapi.testclient import TestClient
        from app.main import app
        database.init_db()
        client = TestClient(app)
        cuerpo = {"line": [0.2, 0.9, 0.8, 0.1], "entry_direction": "up"}
        with mock.patch.object(config, "CAMERA", ""):
            self.assertEqual(client.post("/api/linea", json=cuerpo).status_code, 409)
        with mock.patch.object(config, "CAMERA", "rtsp://u:secreta@cam/1"):
            r = client.post("/api/linea", json=cuerpo)
            self.assertEqual(r.json(), {"line": [0.2, 0.9, 0.8, 0.1], "entry_direction": "up"})
            self.assertEqual(database.get_camera_line()["line"], (0.2, 0.9, 0.8, 0.1))
            self.assertEqual(client.post("/api/linea", json={"line": [0.5, 0.5, 0.51, 0.5]}).status_code, 422)
            self.assertEqual(client.post("/api/linea", json={"line": [0.5, 0.5, 1.5, 0.5]}).status_code, 422)
            estado = client.get("/api/estado").json()
            self.assertEqual(estado["videos"][0]["nombre"], "camara")
            self.assertNotIn("secreta", str(estado))  # la clave de la cámara no sale por la API
            self.assertEqual(estado["linea_camara"]["entry_direction"], "up")


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
