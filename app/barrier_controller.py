"""Simulación de la barrera.

La barrera no mira el reloj de la PC: recibe el tiempo desde afuera (`now`).
Así, al procesar un video más rápido que en tiempo real, la barrera se cierra
a los N segundos *de video*, y en el video de salida se ve igual que en vivo.

Para conectar una barrera real (relé, PLC, GPIO) alcanza con reemplazar
`_actuate()`.
"""
import threading

OPEN = "abierta"
CLOSED = "cerrada"


class BarrierController:
    def __init__(self, open_seconds: float = 3.0, on_change=None):
        self.open_seconds = open_seconds
        self.state = CLOSED
        self._close_at = None
        self._on_change = on_change  # callback(estado) para avisar a la web
        self._lock = threading.Lock()

    def open(self, now: float, reason: str = "") -> None:
        """Abre la barrera (o extiende el tiempo si ya estaba abierta)."""
        with self._lock:
            self._close_at = now + self.open_seconds
            if self.state != OPEN:
                self._set(OPEN, reason)

    def update(self, now: float) -> str:
        """Llamar en cada frame: cierra la barrera cuando vence el tiempo."""
        with self._lock:
            if self.state == OPEN and self._close_at is not None and now >= self._close_at:
                self._close_at = None
                self._set(CLOSED, "tiempo cumplido")
            return self.state

    def remaining(self, now: float) -> float:
        if self.state != OPEN or self._close_at is None:
            return 0.0
        return max(0.0, self._close_at - now)

    def reset(self) -> None:
        with self._lock:
            self._close_at = None
            if self.state != CLOSED:
                self._set(CLOSED, "reset")

    def _set(self, state: str, reason: str) -> None:
        self.state = state
        self._actuate(state)
        print(f"[BARRERA] {state.upper()}" + (f" ({reason})" if reason else ""), flush=True)
        if self._on_change:
            self._on_change(state)

    def _actuate(self, state: str) -> None:
        """Punto de enganche para hardware real. En el MVP no hace nada."""
        pass
