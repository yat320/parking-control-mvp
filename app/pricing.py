"""Cálculo de la tarifa.

Regla: se cobra por fracción iniciada. Con tarifa 1000/hora y fracción de 15 min,
una estadía de 20 min son 2 fracciones = 500. Siempre se cobra al menos una
fracción. Se separa en un módulo propio para poder cambiar la regla (tope
diario, primeros minutos gratis, etc.) sin tocar el resto.
"""
import math


def calculate_amount(duration_seconds: float, rate_per_hour: float, fraction_minutes: int = 15) -> float:
    if rate_per_hour <= 0:
        return 0.0
    fraction_minutes = max(1, int(fraction_minutes))
    minutes = max(0.0, duration_seconds) / 60
    fractions = max(1, math.ceil(minutes / fraction_minutes - 1e-9))
    return round(fractions * fraction_minutes / 60 * rate_per_hour, 2)


def format_duration(duration_seconds: float) -> str:
    total = int(round(duration_seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"
