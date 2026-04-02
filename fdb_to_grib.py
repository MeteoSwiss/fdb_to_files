#!/usr/bin/env python3
"""
Extract fields from FDB and write them to grouped GRIB files.

Usage:
    python fdb_to_grib.py config.yaml
"""

import json
import os
import sys
from datetime import datetime, timedelta

import earthkit.data as ekd
import yaml

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "config_schema.json")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def load_config(path):
    config = yaml.safe_load(open(path))
    _validate(config)
    return config


def _validate(config):
    try:
        import jsonschema
    except ImportError:
        return  # validation is optional
    with open(_SCHEMA_PATH) as f:
        schema = json.load(f)
    errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
    if errors:
        for e in errors:
            loc = " -> ".join(str(p) for p in e.absolute_path) or "(root)"
            print(f"[config error] {loc}: {e.message}", file=sys.stderr)
        sys.exit(1)


def generate_dates(start, end):
    fmt = "%Y%m%d"
    start_dt = datetime.strptime(str(start), fmt)
    end_dt = datetime.strptime(str(end), fmt)
    dates = []
    current = start_dt
    while current <= end_dt:
        dates.append(current.strftime(fmt))
        current += timedelta(days=1)
    return dates


def to_fdb_list(value):
    """Convert a list or a range dict to a slash-separated FDB string.

    A range dict uses keys ``start``, ``end``, optionally ``by`` (default 1)
    and optionally ``unit`` (e.g. ``m`` for minutes):

        steps:
          start: 0
          end: 1440
          by: 10
          unit: m      # produces "0m/to/1440m/by/10m"

    A plain list is also accepted:

        steps: [0, 6, 12, 24]
    """
    if isinstance(value, dict):
        start = value["start"]
        end = value["end"]
        by = value.get("by", 1)
        unit = value.get("unit", "")
        return f"{start}{unit}/to/{end}{unit}/by/{by}{unit}"
    return "/".join(str(v) for v in value)


# ---------------------------------------------------------------------------
# Request building
# ---------------------------------------------------------------------------

MODEL_DEFAULTS = {
    "ICON-CH1-EPS": {"class": "od", "stream": "enfo", "expver": "0001"},
    "ICON-CH2-EPS": {"class": "od", "stream": "enfo", "expver": "0001"},
    "icon-rea-l-ch1": {"class": "rd", "stream": "reanl", "expver": "r001"},
}


def build_requests(config):
    """Return one FDB request dict per block."""
    model = config["model"]
    if model not in MODEL_DEFAULTS:
        raise ValueError(
            f"Unknown model '{model}'. Known models: {list(MODEL_DEFAULTS)}"
        )

    dates = generate_dates(config["date_range"]["start"], config["date_range"]["end"])
    times = [str(t).zfill(4) for t in config["times"]]

    base = {
        **MODEL_DEFAULTS[model],
        "model": model,
        "type": config.get("type", "cf"),
        "date": "/".join(dates),
        "time": "/".join(times),
        "step": to_fdb_list(config["steps"]),
    }

    requests = []
    for block in config["blocks"]:
        req = {
            **base,
            "levtype": block["levtype"],
            "param": "/".join(str(p) for p in block["params"]),
        }
        # A block can override the global steps (e.g. minute-based vs hourly)
        if "steps" in block:
            req["step"] = to_fdb_list(block["steps"])
        if "levels" in block and block["levtype"] != "sfc":
            req["levelist"] = to_fdb_list(block["levels"])
        requests.append(req)
    return requests


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def key_to_path(key_tuple, template, output_dir):
    filename = template.format(**dict(key_tuple))
    return os.path.join(output_dir, filename)


def default_template(group_by):
    return "_".join(f"{{{k}}}" for k in group_by) + ".grib"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(config_path):
    config = load_config(config_path)

    group_by = config["group_by"]
    output_dir = config.get("output_dir", ".")
    template = config.get("output_template") or default_template(group_by)

    os.makedirs(output_dir, exist_ok=True)

    requests = build_requests(config)

    # Keep output files open while streaming fields to avoid re-opening on
    # every write. Files are opened in binary write mode (existing files are
    # overwritten at the start of a run).
    open_files: dict = {}

    try:
        for req in requests:
            print(f"[fetch] {req}")
            ds = ekd.from_source("fdb", request=req)
            count = 0
            for group_key, group in ds.group_by(*group_by):
                key = tuple((k, str(group_key[k])) for k in group_by)
                if key not in open_files:
                    path = key_to_path(key, template, output_dir)
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    print(f"[open]  {path}")
                    open_files[key] = open(path, "wb")
                for field in group:
                    count += 1
                    open_files[key].write(field.message())
            print(f"[count] {count} field(s) returned")

    finally:
        for fh in open_files.values():
            fh.close()

    print(f"\n[done]  {len(open_files)} file(s) written to '{output_dir}/'")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <config.yaml>")
        sys.exit(1)
    main(sys.argv[1])
