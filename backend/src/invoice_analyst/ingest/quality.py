"""How readable is a page image? Simple measures the confidence rules use (no model involved).

Measured on a copy scaled to 1000 px wide, so photos of different resolutions are comparable:
  sharpness  variance of the Laplacian (edge strength); blurred photos score low
  brightness mean grey level 0-255; contrast: its standard deviation
"""

from __future__ import annotations

import numpy as np
from PIL import Image

MEASURE_WIDTH = 1000


def measure(image: Image.Image) -> dict[str, float]:
    grey = image.convert("L")
    if grey.width > MEASURE_WIDTH:
        grey = grey.resize((MEASURE_WIDTH, round(grey.height * MEASURE_WIDTH / grey.width)), Image.LANCZOS)
    a = np.asarray(grey, dtype=np.float64)
    lap = a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:] - 4 * a[1:-1, 1:-1]
    return {"sharpness": round(float(lap.var()), 1), "brightness": round(float(a.mean()), 1),
            "contrast": round(float(a.std()), 1)}
