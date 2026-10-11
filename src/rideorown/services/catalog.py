"""Read the versioned, cited reference catalogue; never fetch live prices at runtime."""

import json
from importlib.resources import files


def get_car_presets() -> list[dict]:
    return json.loads(
        files("rideorown.resources").joinpath("cars_bengaluru.json").read_text(encoding="utf-8")
    )["cars"]


def get_insurance_metadata() -> dict:
    return json.loads(
        files("rideorown.resources").joinpath("acko_insurance.json").read_text(encoding="utf-8")
    )
