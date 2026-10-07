"""Cálculo de la tarifa.

Regla: se cobra por fracción iniciada. Con tarifa 1000/hora y fracción de 15 min,
una estadía de 20 min son 2 fracciones = 500. Siempre se cobra al menos una
fracción.

Dos reglas opcionales (0 = desactivada):
  - tolerancia: si la estadía no pasa de N minutos, no se cobra (el que entra
    y sale enseguida). Pasada la tolerancia se cobra la estadía completa.
  - tope diario: ningún bloque de 24 h cuesta más que el tope (la "estadía").
    Dos días y medio = 2 topes + lo que corresponda por el resto, sin pasar
    de otro tope.
"""
import math

DAY_SECONDS = 24 * 3600


def _by_fraction(duration_seconds: float, rate_per_hour: float, fraction_minutes: int) -> float:
    minutes = max(0.0, duration_seconds) / 60
    fractions = max(1, math.ceil(minutes / fraction_minutes - 1e-9))
    return fractions * fraction_minutes / 60 * rate_per_hour


def calculate_amount(duration_seconds: float, rate_per_hour: float, fraction_minutes: int = 15,
                     tolerance_minutes: float = 0, daily_cap: float = 0) -> float:
    if rate_per_hour <= 0:
        return 0.0
    fraction_minutes = max(1, int(fraction_minutes))
    duration_seconds = max(0.0, duration_seconds)
    if tolerance_minutes > 0 and duration_seconds <= tolerance_minutes * 60 + 1e-6:
        return 0.0
    if daily_cap <= 0:
        return round(_by_fraction(duration_seconds, rate_per_hour, fraction_minutes), 2)
    days, rest = divmod(duration_seconds, DAY_SECONDS)
    amount = days * daily_cap
    if rest > 0 or days == 0:
        amount += min(daily_cap, _by_fraction(rest, rate_per_hour, fraction_minutes))
    return round(amount, 2)


def format_duration(duration_seconds: float) -> str:
    total = int(round(duration_seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"
