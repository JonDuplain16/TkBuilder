"""Example backend functions used by the TkBuilder engineering examples.

These are ordinary Python functions - nothing here knows about the GUI.
"""

import math
import random
import time


def beam_deflection(load_n: float, length_m: float = 2.0, e_gpa: float = 200.0,
                    i_cm4: float = 800.0) -> dict:
    """Max deflection and stress of a simply supported beam with a centre load."""
    e = e_gpa * 1e9
    i = i_cm4 * 1e-8
    deflection = load_n * length_m ** 3 / (48 * e * i)
    moment = load_n * length_m / 4
    return {"deflection_mm": round(deflection * 1000, 3),
            "moment_knm": round(moment / 1000, 3)}


def unit_convert(value: float, conversion: str = "mm to inch") -> float:
    """Convert a value between common engineering units."""
    factors = {"mm to inch": 1 / 25.4, "inch to mm": 25.4, "bar to psi": 14.5038,
               "psi to bar": 1 / 14.5038, "kg to lb": 2.20462, "lb to kg": 1 / 2.20462}
    return round(value * factors[conversion], 4)


def read_sensor(channel: int = 0) -> float:
    """Pretend to read a temperature sensor (for the live dashboard example)."""
    return round(20 + 5 * math.sin(time.time() / 5 + channel) + random.uniform(-0.3, 0.3), 2)


def run_simulation(steps: int = 50, noise: float = 0.1):
    """A slow calculation (runs in the background) that returns x and y data."""
    xs, ys = [], []
    for k in range(int(steps)):
        time.sleep(0.02)
        xs.append(k)
        ys.append(math.sin(k / 5) + random.uniform(-noise, noise))
    return xs, ys


def summarize(values: list) -> list:
    """Return table rows [statistic, value] for a list of numbers."""
    vals = [float(v) for v in values]
    n = len(vals)
    mean = sum(vals) / n if n else 0
    sd = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5 if n else 0
    return [["Count", n], ["Mean", round(mean, 4)], ["Std dev", round(sd, 4)],
            ["Min", min(vals) if vals else ""], ["Max", max(vals) if vals else ""]]
