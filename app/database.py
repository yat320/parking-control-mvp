"""Persistencia en SQLite.

Tabla `eventos`: una fila por cada cruce de la línea virtual.
  - tipo_evento: 'entrada' o 'salida'
  - timestamp: hora del evento (ISO 8601)
  - vehicle_id: id del vehículo (V0001...). Sin lectura de patentes, la salida
    se empareja con la entrada abierta más vieja (FIFO).
  - tracking_id: id del tracker en el video (cambia en cada procesamiento)
  - estado_barrera: estado de la barrera al registrar el evento
  - monto / duracion_seg: solo en las salidas
  - entrada_id: en una salida, el id de la entrada que cierra

Tabla `config`: clave/valor (tarifa por hora y fracción).

Cada función abre su propia conexión: el procesador de video corre en otro
hilo y SQLite no comparte conexiones entre hilos.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import config
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
    entrada_id INTEGER REFERENCES eventos(id)
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

def register_entry(ts: datetime, tracking_id: int, barrier_state: str) -> dict:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO eventos (tipo_evento, timestamp, tracking_id, estado_barrera) VALUES ('entrada', ?, ?, ?)",
            (ts.isoformat(timespec="seconds"), tracking_id, barrier_state),
        )
        n = conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo_evento = 'entrada'").fetchone()[0]
        vehicle_id = f"V{n:04d}"
        conn.execute("UPDATE eventos SET vehicle_id = ? WHERE id = ?", (vehicle_id, cur.lastrowid))
        return {"id": cur.lastrowid, "tipo_evento": "entrada", "vehicle_id": vehicle_id}


def register_exit(ts: datetime, tracking_id: int, barrier_state: str) -> dict:
    """Registra una salida, la empareja con la entrada abierta más vieja y cobra."""
    pricing = get_pricing()
    with connect() as conn:
        entry = conn.execute(
            """SELECT e.* FROM eventos e
               WHERE e.tipo_evento = 'entrada'
                 AND NOT EXISTS (SELECT 1 FROM eventos s WHERE s.entrada_id = e.id)
               ORDER BY e.id LIMIT 1"""
        ).fetchone()
        monto = duracion = entrada_id = None
        vehicle_id = "sin-entrada"
        if entry:
            entrada_id = entry["id"]
            vehicle_id = entry["vehicle_id"]
            duracion = max(0.0, (ts - datetime.fromisoformat(entry["timestamp"])).total_seconds())
            monto = calculate_amount(duracion, pricing["tarifa_hora"], pricing["fraccion_min"])
        cur = conn.execute(
            """INSERT INTO eventos (tipo_evento, timestamp, vehicle_id, tracking_id, estado_barrera,
                                    monto, duracion_seg, entrada_id)
               VALUES ('salida', ?, ?, ?, ?, ?, ?, ?)""",
            (ts.isoformat(timespec="seconds"), vehicle_id, tracking_id, barrier_state, monto, duracion, entrada_id),
        )
        return {"id": cur.lastrowid, "tipo_evento": "salida", "vehicle_id": vehicle_id,
                "monto": monto, "duracion_seg": duracion}


def list_events(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM eventos ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def list_inside() -> list[dict]:
    """Autos que entraron y todavía no salieron."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT e.id, e.vehicle_id, e.timestamp FROM eventos e
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
