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


if __name__ == "__main__":
    unittest.main()
