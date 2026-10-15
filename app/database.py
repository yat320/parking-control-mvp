"""Persistencia en SQLite.

Tabla `eventos`: una fila por cada cruce de la línea virtual.
  - tipo_evento: 'entrada' o 'salida'
  - timestamp: hora del evento (ISO 8601)
  - vehicle_id: id del vehículo (V0001...).
  - patente: la patente leída por la cámara frontal (AB123CD), o NULL si no se leyó.
    Una salida con patente se empareja con la entrada abierta de esa patente; sin
    patente (o si no hay entrada con esa patente), con la entrada abierta más vieja (FIFO).
  - tracking_id: id del tracker en el video (cambia en cada procesamiento)
  - estado_barrera: estado de la barrera al registrar el evento
  - monto / duracion_seg: solo en las salidas
  - entrada_id: en una salida, el id de la entrada que cierra

Tabla `config`: clave/valor (tarifa por hora y fracción).

Cada función abre su propia conexión: el procesador de video corre en otro
hilo y SQLite no comparte conexiones entre hilos.
"""
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import config
from .plates import one_off
from .pricing import calculate_amount

SCHEMA = """
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo_evento TEXT NOT NULL CHECK (tipo_evento IN ('entrada', 'salida')),
    timestamp TEXT NOT NULL,
    vehicle_id TEXT,
    tracking_id INTEGER,
    estado_barrera TEXT NOT NULL,
    monto REAL,
    duracion_seg REAL,
    entrada_id INTEGER REFERENCES eventos(id),
    patente TEXT
);
CREATE TABLE IF NOT EXISTS config (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
"""


@contextmanager
def connect():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Bases creadas antes de leer patentes: agregar la columna.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(eventos)")}
        if "patente" not in cols:
            conn.execute("ALTER TABLE eventos ADD COLUMN patente TEXT")
        conn.execute("INSERT OR IGNORE INTO config VALUES ('tarifa_hora', ?)", (str(config.DEFAULT_RATE_PER_HOUR),))
        conn.execute("INSERT OR IGNORE INTO config VALUES ('fraccion_min', ?)", (str(config.DEFAULT_FRACTION_MINUTES),))


# ---------- configuración ----------

def get_pricing() -> dict:
    with connect() as conn:
        rows = dict(conn.execute("SELECT clave, valor FROM config").fetchall())
    return {
        "tarifa_hora": float(rows.get("tarifa_hora", config.DEFAULT_RATE_PER_HOUR)),
        "fraccion_min": int(float(rows.get("fraccion_min", config.DEFAULT_FRACTION_MINUTES))),
    }


def set_pricing(tarifa_hora: float, fraccion_min: int) -> None:
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO config VALUES ('tarifa_hora', ?)", (str(float(tarifa_hora)),))
        conn.execute("INSERT OR REPLACE INTO config VALUES ('fraccion_min', ?)", (str(int(fraccion_min)),))


# ---------- eventos ----------

def register_entry(ts: datetime, tracking_id: int, barrier_state: str, patente: str | None = None) -> dict:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO eventos (tipo_evento, timestamp, tracking_id, estado_barrera, patente) VALUES ('entrada', ?, ?, ?, ?)",
            (ts.isoformat(timespec="seconds"), tracking_id, barrier_state, patente),
        )
        n = conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo_evento = 'entrada'").fetchone()[0]
        vehicle_id = f"V{n:04d}"
        conn.execute("UPDATE eventos SET vehicle_id = ? WHERE id = ?", (vehicle_id, cur.lastrowid))
        return {"id": cur.lastrowid, "tipo_evento": "entrada", "vehicle_id": vehicle_id, "patente": patente}


OPEN_ENTRIES = """SELECT e.* FROM eventos e
                  WHERE e.tipo_evento = 'entrada'
                    AND NOT EXISTS (SELECT 1 FROM eventos s WHERE s.entrada_id = e.id)"""


def _entry_for_plate(conn, patente: str):
    """La entrada abierta de esa patente; si no hay, la única que difiere en una letra
    (el OCR leyó AA310VF a la salida y AA310WF a la entrada). Si hay dos parecidas, ninguna."""
    entry = conn.execute(OPEN_ENTRIES + " AND e.patente = ? ORDER BY e.id LIMIT 1", (patente,)).fetchone()
    if entry is not None:
        return entry
    near = [e for e in conn.execute(OPEN_ENTRIES + " AND e.patente IS NOT NULL ORDER BY e.id").fetchall()
            if one_off(e["patente"], patente)]
    return near[0] if len(near) == 1 else None


def register_exit(ts: datetime, tracking_id: int, barrier_state: str, patente: str | None = None) -> dict:
    """Registra una salida, la empareja con su entrada y cobra.

    Con patente: la entrada abierta de esa patente (o la única que difiere en una letra);
    si no hay, la más vieja de las que entraron sin patente leída (nunca la de otra
    patente). Sin patente: la más vieja (FIFO).
    """
    pricing = get_pricing()
    with connect() as conn:
        entry = None
        if patente:
            entry = _entry_for_plate(conn, patente)
            if entry is None:
                entry = conn.execute(OPEN_ENTRIES + " AND e.patente IS NULL ORDER BY e.id LIMIT 1").fetchone()
        else:
            entry = conn.execute(OPEN_ENTRIES + " ORDER BY e.id LIMIT 1").fetchone()
        monto = duracion = entrada_id = None
        vehicle_id = "sin-entrada"
        if entry:
            entrada_id = entry["id"]
            vehicle_id = entry["vehicle_id"]
            duracion = max(0.0, (ts - datetime.fromisoformat(entry["timestamp"])).total_seconds())
            monto = calculate_amount(duracion, pricing["tarifa_hora"], pricing["fraccion_min"])
        cur = conn.execute(
            """INSERT INTO eventos (tipo_evento, timestamp, vehicle_id, tracking_id, estado_barrera,
                                    monto, duracion_seg, entrada_id, patente)
               VALUES ('salida', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts.isoformat(timespec="seconds"), vehicle_id, tracking_id, barrier_state, monto, duracion, entrada_id,
             patente),
        )
        return {"id": cur.lastrowid, "tipo_evento": "salida", "vehicle_id": vehicle_id,
                "monto": monto, "duracion_seg": duracion, "patente": patente}


def set_plate(event_id: int, patente: str) -> None:
    """Patente leída después de registrar el evento (el auto cruzó antes de que se le viera la chapa).

    Si es una salida que se había emparejado por orden de llegada con la entrada de otra
    patente, se la pasa a la entrada de esta patente y se recalcula el cobro; la entrada
    que tenía antes vuelve a quedar adentro.
    """
    pricing = get_pricing()
    with connect() as conn:
        ev = conn.execute("SELECT * FROM eventos WHERE id = ?", (event_id,)).fetchone()
        if ev is None or ev["patente"] is not None:
            return
        conn.execute("UPDATE eventos SET patente = ? WHERE id = ?", (patente, event_id))
        if ev["tipo_evento"] != "salida":
            return
        if ev["entrada_id"] is not None:
            actual = conn.execute("SELECT patente FROM eventos WHERE id = ?", (ev["entrada_id"],)).fetchone()
            if actual and actual["patente"] and (actual["patente"] == patente or one_off(actual["patente"], patente)):
                return
        mejor = _entry_for_plate(conn, patente)
        if mejor is None:
            return
        duracion = max(0.0, (datetime.fromisoformat(ev["timestamp"])
                             - datetime.fromisoformat(mejor["timestamp"])).total_seconds())
        monto = calculate_amount(duracion, pricing["tarifa_hora"], pricing["fraccion_min"])
        conn.execute("UPDATE eventos SET entrada_id = ?, vehicle_id = ?, duracion_seg = ?, monto = ? WHERE id = ?",
                     (mejor["id"], mejor["vehicle_id"], duracion, monto, event_id))


def list_events(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM eventos ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def list_inside() -> list[dict]:
    """Autos que entraron y todavía no salieron."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT e.id, e.vehicle_id, e.timestamp, e.patente FROM eventos e
               WHERE e.tipo_evento = 'entrada'
                 AND NOT EXISTS (SELECT 1 FROM eventos s WHERE s.entrada_id = e.id)
               ORDER BY e.id"""
        ).fetchall()
    return [dict(r) for r in rows]


def stats() -> dict:
    with connect() as conn:
        row = conn.execute(
            """SELECT
                 COALESCE(SUM(tipo_evento = 'entrada'), 0) AS entradas,
                 COALESCE(SUM(tipo_evento = 'salida'), 0) AS salidas,
                 COALESCE(SUM(monto), 0) AS recaudado
               FROM eventos"""
        ).fetchone()
    return {"entradas": row["entradas"], "salidas": row["salidas"],
            "adentro": len(list_inside()), "recaudado": round(row["recaudado"], 2)}


def reset_events() -> None:
    """Borra los eventos (la tarifa configurada se mantiene)."""
    with connect() as conn:
        conn.execute("DELETE FROM eventos")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'eventos'")
    # Las fotos de patentes van por id de evento, que vuelve a empezar de 1.
    shutil.rmtree(config.PLATES_DIR, ignore_errors=True)
