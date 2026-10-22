"""Pruebas del MVP:  python -m unittest discover tests -v"""
import os
import tempfile
import unittest
from pathlib import Path

# Base y videos en una carpeta temporal, para no tocar data/parking.db.
TMP = Path(tempfile.mkdtemp())
os.environ["PARKING_DB"] = str(TMP / "test.db")
os.environ["PARKING_PLATES_DIR"] = str(TMP / "patentes")

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


class PlatesTest(unittest.TestCase):
    """Patentes: formato, votos y emparejar la salida por patente (sin el lector)."""

    def test_formato(self):
        from app import plates
        self.assertEqual(plates.normalize("ab 123-cd"), "AB123CD")
        self.assertTrue(plates.is_valid("AB123CD") and plates.is_valid("ABC123"))
        self.assertFalse(plates.is_valid("AB123C") or plates.is_valid("1234567"))
        self.assertEqual((plates.pretty("AB123CD"), plates.pretty("ABC123")), ("AB 123 CD", "ABC 123"))

    def test_votos_recientes(self):
        import numpy as np
        from app import plates
        votes = plates.PlateVotes()
        leer = lambda t, c=0.8: plates.PlateRead(t, c, (0, 0, 10, 4), np.zeros((4, 10, 3), np.uint8))
        self.assertIsNone(votes.best())
        votes.add(leer("AB123C"))                    # formato inválido: no cuenta
        self.assertIsNone(votes.best())
        for _ in range(5):
            votes.add(leer("ABC123"))
        votes.add(leer("ABC128", 0.3))               # una mala lectura no gana
        self.assertEqual(votes.best(), "ABC123")
        cambio = [votes.add(leer("AE905KT")) for _ in range(config.PLATES_WINDOW)]
        self.assertEqual(votes.best(), "AE905KT")    # otro auto con el mismo track
        self.assertTrue(any(cambio))

    def test_salida_por_patente(self):
        from datetime import datetime, timedelta
        database.init_db()
        database.reset_events()
        database.set_pricing(1000, 15)
        t0 = datetime(2026, 10, 15, 10, 0)
        database.register_entry(t0, 1, "abierta", "AB123CD")
        database.register_entry(t0 + timedelta(minutes=5), 2, "abierta", "ABC123")
        database.register_entry(t0 + timedelta(minutes=6), 3, "abierta", None)
        s1 = database.register_exit(t0 + timedelta(minutes=20), 4, "abierta", "ABC123")   # no la más vieja
        self.assertEqual((s1["vehicle_id"], s1["duracion_seg"]), ("V0002", 15 * 60))
        s2 = database.register_exit(t0 + timedelta(minutes=30), 5, "abierta", "ZZZ999")   # sin entrada con esa patente
        self.assertEqual(s2["vehicle_id"], "V0003")                                         # va a una sin patente leída
        s3 = database.register_exit(t0 + timedelta(minutes=40), 6, "abierta", "AE905KT")  # nunca a otra patente
        self.assertEqual(s3["vehicle_id"], "sin-entrada")
        self.assertEqual([a["patente"] for a in database.list_inside()], ["AB123CD"])

    def test_base_vieja_se_migra(self):
        import sqlite3
        vieja = TMP / "vieja.db"
        c = sqlite3.connect(vieja)
        c.executescript(database.SCHEMA.replace(",\n    patente TEXT", ""))
        c.execute("INSERT INTO eventos (tipo_evento, timestamp, estado_barrera) VALUES ('entrada', '2026-01-01T00:00:00', 'abierta')")
        c.commit()
        c.close()
        antes = config.DB_PATH
        config.DB_PATH = vieja
        try:
            database.init_db()
            self.assertEqual(len(database.list_events()), 1)
            self.assertIsNone(database.list_events()[0]["patente"])
        finally:
            config.DB_PATH = antes


try:
    import fast_alpr  # noqa: F401
    HAY_LECTOR = True
except ImportError:
    HAY_LECTOR = False


@unittest.skipUnless(HAY_LECTOR, "falta fast-alpr: pip install -r requirements.txt")
class PlatesVideoTest(unittest.TestCase):
    """Cámara frontal: entran 3 autos con patente y salen en otro orden; cada salida va a su entrada."""

    def test_salidas_emparejadas_por_patente(self):
        import json
        from app.demo_plates import generate as generate_plates
        video = generate_plates(TMP / "frontal.mp4")
        from app.demo_plates import DRAWN_DETECTOR, DRAWN_OCR
        video.with_suffix(".json").write_text(json.dumps(
            {"patentes": True, "ocr": DRAWN_OCR, "detector_patentes": DRAWN_DETECTOR}))
        database.init_db()
        database.reset_events()
        VideoProcessor(detector_kind="motion").process(video)
        evs = list(reversed(database.list_events()))
        self.assertEqual([(e["tipo_evento"], e["patente"]) for e in evs], [
            ("entrada", "AB123CD"), ("entrada", "ABC123"), ("entrada", "AE905KT"),
            ("salida", "ABC123"), ("salida", "AB123CD"), ("salida", "AE905KT")])
        entradas = {e["id"]: e["patente"] for e in evs if e["tipo_evento"] == "entrada"}
        for s in (e for e in evs if e["tipo_evento"] == "salida"):
            self.assertEqual(entradas[s["entrada_id"]], s["patente"])
        self.assertTrue((config.PLATES_DIR / f"{evs[0]['id']}.jpg").exists())


class PairingTest(unittest.TestCase):
    """Dos cámaras: la patente que vio la frontal se le da al evento de la barrera."""

    def leer(self, texto, conf=0.8):
        import numpy as np
        from app import plates
        return plates.PlateRead(texto, conf, (0, 0, 10, 4), np.zeros((4, 10, 3), np.uint8))

    def test_linea_de_tiempo(self):
        from app.pairing import PlateTimeline
        tl = PlateTimeline()
        for t in (1.0, 1.2, 1.4):
            tl.add(self.leer("AB123CD"), t)
        tl.add(self.leer("AB123C"), 1.5)            # formato inválido: no cuenta
        for t in (6.0, 6.3):
            tl.add(self.leer("ABC123"), t)
        tl.add(self.leer("ABC128", 0.2), 6.1)       # error de una letra: se junta con ABC123
        tl.add(self.leer("XYZ987", 0.2), 6.1)       # mala lectura suelta: no llega al puntaje
        self.assertEqual(len(tl.sightings), 3)
        self.assertEqual(tl.claim(2.5).plate, "AB123CD")   # vista 1 s antes del evento
        self.assertEqual(tl.claim(6.2).plate, "ABC123")
        self.assertIsNone(tl.claim(6.2))                   # cada vista se usa una vez
        self.assertIsNone(tl.claim(20.0))                  # nada cerca

    def test_direccion(self):
        """Con un carril de cada lado, la frontal ve a la vez al que entra (chapa que crece)
        y al que sale (chapa que se achica): cada evento se lleva la que corresponde."""
        import numpy as np
        from app import plates
        from app.pairing import PlateTimeline
        tl = PlateTimeline()
        leer = lambda txt, w: plates.PlateRead(txt, 0.8, (0, 0, w, w // 3), np.zeros((4, 10, 3), np.uint8))
        for i, t in enumerate((10.0, 10.3, 10.6, 10.9, 11.2)):
            tl.add(leer("AE905KT", 200 - 25 * i), t - 0.5)   # se aleja: sale; apareció antes
            tl.add(leer("AB123CD", 80 + 25 * i), t)          # se acerca: entra
        self.assertEqual(tl.claim(11.0, "entrada").plate, "AB123CD")
        self.assertEqual(tl.claim(11.0, "salida").plate, "AE905KT")

    def test_una_letra_de_diferencia(self):
        """El OCR erra una letra (AA310VF por AA310WF): se junta con su vista y se cobra igual."""
        from datetime import datetime, timedelta
        from app import plates
        from app.pairing import PlateTimeline
        self.assertTrue(plates.one_off("AA310WF", "AA310VF"))
        self.assertFalse(plates.one_off("AA310WF", "AA311VF") or plates.one_off("ABC123", "AB123CD"))
        tl = PlateTimeline()
        for t, txt in ((1.0, "AA310WF"), (1.2, "AA310VF"), (1.4, "AA310WF"), (1.6, "AA310WF")):
            tl.add(self.leer(txt), t)
        self.assertEqual([s.plate for s in tl.sightings], ["AA310WF"])   # una sola vista, la que más suma
        database.init_db()
        database.reset_events()
        t0 = datetime(2026, 10, 15, 10, 0)
        database.register_entry(t0, 1, "abierta", "AA310WF")
        database.register_entry(t0, 2, "abierta", "IQD430")
        s = database.register_exit(t0 + timedelta(minutes=30), 3, "abierta", "IOD430")  # leída con un error
        self.assertEqual(s["vehicle_id"], "V0002")
        database.register_entry(t0, 4, "abierta", "AB123CD")
        database.register_entry(t0, 5, "abierta", "AB123CE")
        s = database.register_exit(t0 + timedelta(minutes=30), 6, "abierta", "AB123CF")  # dos parecidas: ninguna
        self.assertEqual(s["vehicle_id"], "sin-entrada")

    def test_salida_corregida_por_patente_tardia(self):
        from datetime import datetime, timedelta
        database.init_db()
        database.reset_events()
        database.set_pricing(1000, 15)
        t0 = datetime(2026, 10, 15, 10, 0)
        database.register_entry(t0, 1, "abierta", "AB123CD")
        database.register_entry(t0 + timedelta(minutes=5), 2, "abierta", "ABC123")
        s = database.register_exit(t0 + timedelta(minutes=20), 3, "abierta", None)  # sin patente todavía
        self.assertEqual(s["vehicle_id"], "V0001")                                  # por orden de llegada
        database.set_plate(s["id"], "ABC123")                                        # la frontal la ve después
        salida = [e for e in database.list_events() if e["id"] == s["id"]][0]
        self.assertEqual((salida["vehicle_id"], salida["duracion_seg"]), ("V0002", 15 * 60))
        self.assertEqual([a["patente"] for a in database.list_inside()], ["AB123CD"])

    def test_salida_sin_patente_va_a_entrada_sin_patente(self):
        from datetime import datetime, timedelta
        database.init_db()
        database.reset_events()
        t0 = datetime(2026, 10, 15, 10, 0)
        database.register_entry(t0, 1, "abierta", "AB123CD")
        database.register_entry(t0 + timedelta(minutes=5), 2, "abierta", None)   # la frontal no la leyó
        s = database.register_exit(t0 + timedelta(minutes=20), 3, "abierta", None)
        self.assertEqual(s["vehicle_id"], "V0002")             # no le quita la entrada a AB123CD
        s = database.register_exit(t0 + timedelta(minutes=25), 4, "abierta", "AB123CD")
        self.assertEqual(s["vehicle_id"], "V0001")

    def test_subir_par(self):
        import io
        import json
        from app import uploads
        folder = Path(tempfile.mkdtemp())
        demo = TMP / "par_demo.mp4"
        if not demo.exists():
            generate(demo, 12)
        datos = demo.read_bytes()
        nombre = uploads.save(io.BytesIO(datos), "barrera.mp4", "horizontal", False, folder=folder,
                              frontal=io.BytesIO(datos), frontal_name="frente.mov", desfase=1.5)
        opts = json.loads((folder / nombre).with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual((opts["frontal"], opts["desfase"]), (Path(nombre).stem + "-frontal.mov", 1.5))
        self.assertTrue((folder / opts["frontal"]).exists())
        with self.assertRaises(uploads.UploadError):   # la frontal no es un video: no queda nada a medias
            uploads.save(io.BytesIO(datos), "b.mp4", "horizontal", False, folder=folder,
                         frontal=io.BytesIO(b"nada"), frontal_name="f.mp4")
        self.assertEqual(len(list(folder.glob("*.mp4"))) + len(list(folder.glob("*.mov"))), 2)


@unittest.skipUnless(HAY_LECTOR, "falta fast-alpr: pip install -r requirements.txt")
class PairVideoTest(unittest.TestCase):
    """Barrera + frontal grabadas con 2,5 s de diferencia: cada evento con su patente."""

    def test_par_sincronizado(self):
        from app.demo_plates import generate_pair
        folder = Path(tempfile.mkdtemp())
        barrera = generate_pair(folder, lead=2.5)
        database.init_db()
        database.reset_events()
        VideoProcessor(detector_kind="motion").process(barrera)
        evs = list(reversed(database.list_events()))
        self.assertEqual([(e["tipo_evento"], e["patente"]) for e in evs], [
            ("entrada", "AB123CD"), ("entrada", "ABC123"), ("entrada", "AE905KT"),
            ("salida", "ABC123"), ("salida", "AB123CD"), ("salida", "AE905KT")])
        entradas = {e["id"]: e["patente"] for e in evs if e["tipo_evento"] == "entrada"}
        self.assertTrue(all(entradas[s["entrada_id"]] == s["patente"] for s in evs if s["tipo_evento"] == "salida"))


REAL_VIDEO = Path(__file__).resolve().parent.parent / "videos" / "calle_real.avi"


@unittest.skipUnless(REAL_VIDEO.exists(), "falta el video real: python tools/descargar_video_real.py")
class RealVideoTest(unittest.TestCase):
    """Video real de una calle: cruzan 5 autos de izquierda a derecha."""

    def test_cinco_entradas(self):
        database.init_db()
        database.reset_events()
        events = VideoProcessor(detector_kind="motion").process(REAL_VIDEO, TMP / "real.mp4")
        self.assertEqual([e["tipo_evento"] for e in events], ["entrada"] * 5)


class JuegoTest(unittest.TestCase):
    """El simulador jugable (/juego): la partida se convierte en video y la procesa el detector."""

    def test_valida_la_partida(self):
        from app import juego
        with self.assertRaises(juego.JuegoError):
            juego.build_schedule([])
        with self.assertRaises(juego.JuegoError):  # sale antes de terminar de entrar
            juego.build_schedule([{"color": "#c83232", "entra": 5, "sale": 6}])
        with self.assertRaises(juego.JuegoError):
            juego.build_schedule([{"color": "rojo", "entra": 1}])
        with self.assertRaises(juego.JuegoError):  # más largo que el máximo
            juego.build_schedule([{"color": "#c83232", "entra": juego.MAX_SECONDS}])
        schedule, seconds = juego.build_schedule([{"color": "#ff0000", "entra": 1, "sale": 12}])
        self.assertEqual(schedule[0][2], (0, 0, 255))  # pasa a BGR
        self.assertAlmostEqual(seconds, 12 + juego.TAIL_SECONDS)

    def test_partida_cobra_lo_que_se_jugo(self):
        """4 autos con colores claros y grises; salen en orden de llegada: el cobro tiene que dar exacto."""
        from app import juego
        autos = [{"color": "#9a9a9a", "entra": 1.0, "sale": 14.0},
                 {"color": "#e1e1e1", "entra": 3.2, "sale": 20.0},
                 {"color": "#e6c31e", "entra": 9.0, "sale": 27.0},
                 {"color": "#2e9e46", "entra": 15.0}]
        name = juego.save(autos, folder=TMP / "juego")
        database.init_db()
        database.reset_events()
        database.set_pricing(1000, 15)
        events = VideoProcessor(detector_kind="motion", time_scale=60).process(TMP / "juego" / name, None)
        self.assertEqual(sum(e["tipo_evento"] == "entrada" for e in events), 4)
        salidas = sorted((e for e in database.list_events() if e["tipo_evento"] == "salida"), key=lambda e: e["id"])
        self.assertEqual(len(salidas), 3)
        for a, s in zip(autos, salidas):  # misma cuenta que hace juego.js
            real = (a["sale"] + juego.crossing_offset("up") - a["entra"] - juego.crossing_offset("down")) * 60
            self.assertLess(abs(s["duracion_seg"] - real), 30)
            self.assertEqual(s["monto"], calculate_amount(real, 1000, 15))

    def test_valida_patentes(self):
        from app import juego
        with self.assertRaises(juego.JuegoError):  # falta la patente
            juego.build_schedule([{"color": "#c83232", "entra": 1}], patentes=True)
        with self.assertRaises(juego.JuegoError):  # formato no argentino
            juego.build_schedule([{"color": "#c83232", "entra": 1, "patente": "AB123C"}], patentes=True)
        with self.assertRaises(juego.JuegoError):  # repetida
            juego.build_schedule([{"color": "#c83232", "entra": 1, "patente": "AB123CD"},
                                  {"color": "#c83232", "entra": 4, "patente": "ab 123 cd"}], patentes=True)
        schedule, _ = juego.build_schedule([{"color": "#c83232", "entra": 1, "patente": "abc 123"}], patentes=True)
        self.assertEqual(schedule[0][3], "ABC123")

    def test_no_lista_videos_a_medio_escribir(self):
        from app import main
        folder = config.VIDEO_PATH.parent
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / ".juego-a-medio-escribir.mp4"
        tmp.write_bytes(b"")
        try:
            self.assertNotIn(tmp.name, [v["nombre"] for v in main._list_videos()])
        finally:
            tmp.unlink()

    def test_guarda_solo_las_ultimas(self):
        from app import juego
        folder = TMP / "juego-prune"
        for _ in range(juego.KEEP + 2):
            juego.save([{"color": "#c83232", "entra": 0}], folder=folder)
        self.assertEqual(len(list(folder.glob("juego-*.mp4"))), juego.KEEP)
        self.assertEqual(len(list(folder.glob("juego-*.json"))), juego.KEEP)
        self.assertEqual(list(folder.glob(".*")), [])  # no quedan temporales


@unittest.skipUnless(HAY_LECTOR, "falta fast-alpr: pip install -r requirements.txt")
class JuegoPatentesTest(unittest.TestCase):
    def test_salidas_desordenadas_se_cobran_por_patente(self):
        """Salen en otro orden que el de llegada, y uno entra justo cuando sale otro: con la
        cámara frontal, cada salida se cobra a la entrada de su patente."""
        from app import juego
        autos = [{"color": "#c82828", "entra": 1.0, "sale": 22.0, "patente": "AB123CD"},
                 {"color": "#1e6ebe", "entra": 3.5, "sale": 12.0, "patente": "AC456FG"},
                 {"color": "#e1e1e1", "entra": 7.0, "sale": 17.0, "patente": "ABC123"},
                 {"color": "#e6c31e", "entra": 12.3, "patente": "AE905KT"}]
        folder = TMP / "juego-patentes"
        name = juego.save(autos, folder=folder, patentes=True)
        self.assertTrue((folder / name.replace(".mp4", "-frontal.mp4")).exists())
        database.init_db()
        database.reset_events()
        database.set_pricing(1000, 15)
        VideoProcessor(time_scale=60).process(folder / name, None)
        evs = {e["id"]: e for e in database.list_events(100)}
        self.assertEqual(sorted(e["patente"] for e in evs.values() if e["tipo_evento"] == "entrada"),
                         sorted(a["patente"] for a in autos))
        salidas = [e for e in evs.values() if e["tipo_evento"] == "salida"]
        self.assertEqual(len(salidas), 3)
        for s in salidas:
            a = next(x for x in autos if x["patente"] == s["patente"])
            self.assertEqual(evs[s["entrada_id"]]["patente"], s["patente"])
            real = (a["sale"] + juego.crossing_offset("up") - a["entra"] - juego.crossing_offset("down")) * 60
            self.assertEqual(s["monto"], calculate_amount(real, 1000, 15))


if __name__ == "__main__":
    unittest.main()
