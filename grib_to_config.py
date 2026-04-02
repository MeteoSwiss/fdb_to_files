#!/usr/bin/env python3
"""
Inspect a GRIB file and generate the config.yaml needed to
reconstruct it from FDB using fdb_to_grib.py.

Usage:
    python grib_to_config.py input.grib [output_config.yaml]
"""

import sys
from collections import defaultdict

import earthkit.data as ekd
import yaml

# ecCodes stepUnits code -> FDB step suffix ("" means hours, no suffix needed)
_STEP_UNIT = {0: "m", 1: ""}

# Reverse lookup: (class, stream) -> model name
# Must stay in sync with MODEL_DEFAULTS in fdb_to_grib.py
_CLASS_STREAM_TO_MODEL = {
    ("od", "enfo",  "ICON-CH1-EPS"): "ICON-CH1-EPS",
    ("od", "enfo",  "ICON-CH2-EPS"): "ICON-CH2-EPS",
    ("rd", "reanl", "icon-rea-l-ch1"): "icon-rea-l-ch1",
}


# ---------------------------------------------------------------------------
# GRIB reading
# ---------------------------------------------------------------------------

def read_records(grib_path):
    ds = ekd.from_source("file", grib_path)
    records = []
    for field in ds:
        m = field.metadata
        # Try both MARS and generic key names for robustness
        cls    = m("marsClass",  default=None) or m("class",  default=None)
        stream = m("marsStream", default=None) or m("stream", default=None)
        ftype  = m("marsType",   default=None) or m("type",   default=None)
        model  = m("model",      default=None)
        records.append({
            "model":     model,
            "class":     cls,
            "stream":    stream,
            "type":      ftype,
            "date":      str(m("date",     default="")),
            "time":      str(m("time",     default=0)).zfill(4),
            "step":      m("step",         default=0),
            "step_unit": _STEP_UNIT.get(m("stepUnits", default=1), ""),
            "levtype":   m("levtype",      default=None),
            "param":     m("paramId",      default=None),
            "level":     m("level",        default=None),
        })
    return records


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def try_range(values, unit=""):
    """Return a range dict if values form an arithmetic sequence, else a list.
    Float values (e.g. depth levels) are always returned as a list.
    """
    vals = sorted(set(values))
    if len(vals) < 2:
        return vals
    if any(isinstance(v, float) for v in vals):
        return vals
    gaps = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
    if len(set(gaps)) == 1:
        d = {"start": vals[0], "end": vals[-1]}
        if gaps[0] != 1:
            d["by"] = gaps[0]
        if unit:
            d["unit"] = unit
        return d
    return vals


def resolve_model(records):
    """Return the model name, trying the 'model' key first then class/stream lookup."""
    models = {r["model"] for r in records if r["model"]}
    if models:
        if len(models) > 1:
            print(f"[warn]  Multiple models in file: {models} — using {sorted(models)[0]}", file=sys.stderr)
        return sorted(models)[0]

    # Fallback: infer from class + stream
    classes  = {r["class"]  for r in records if r["class"]}
    streams  = {r["stream"] for r in records if r["stream"]}
    if len(classes) == 1 and len(streams) == 1:
        cls, stream = classes.pop(), streams.pop()
        for (c, s, mdl) in _CLASS_STREAM_TO_MODEL:
            if c == cls and s == stream:
                print(f"[info]  Inferred model '{mdl}' from class='{cls}' stream='{stream}'")
                return mdl

    print("[warn]  Could not determine model — set it manually in the config.", file=sys.stderr)
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Config construction
# ---------------------------------------------------------------------------

def build_config(records):
    model = resolve_model(records)

    types = sorted({r["type"] for r in records if r["type"]})
    grib_type = types[0] if types else "cf"

    dates = sorted({r["date"] for r in records if r["date"]})
    times = sorted({r["time"] for r in records if r["time"]})

    # Group by levtype
    groups = defaultdict(lambda: {"params": set(), "levels": set(), "steps": set(), "unit": ""})
    for r in records:
        lt = r["levtype"]
        if lt is None:
            continue
        groups[lt]["params"].add(r["param"])
        groups[lt]["steps"].add(r["step"])
        groups[lt]["unit"] = r["step_unit"]   # assume uniform unit per levtype
        if r["level"] is not None:
            groups[lt]["levels"].add(r["level"])

    # Global steps: use only when all levtypes share identical (steps, unit)
    step_signatures = {(frozenset(d["steps"]), d["unit"]) for d in groups.values()}
    if len(step_signatures) == 1:
        sig_steps, sig_unit = next(iter(step_signatures))
        global_steps = try_range(sorted(sig_steps), sig_unit)
    else:
        # Heterogeneous steps: fall back to the full union; blocks will override
        all_steps = sorted({r["step"] for r in records})
        global_steps = try_range(all_steps)

    blocks = []
    for levtype, data in sorted(groups.items()):
        block = {
            "levtype": levtype,
            "params":  sorted(data["params"]),
        }
        block_steps = try_range(sorted(data["steps"]), data["unit"])
        if block_steps != global_steps:
            block["steps"] = block_steps
        # sfc fields encode height-above-ground in the GRIB level key, but FDB
        # does not use levelist for levtype=sfc — it is implicit in the param.
        if data["levels"] and levtype != "sfc":
            block["levels"] = try_range(sorted(data["levels"]))
        blocks.append(block)

    return {
        "model":      model,
        "type":       grib_type,
        "date_range": {"start": dates[0], "end": dates[-1]},
        "times":      times,
        "steps":      global_steps,
        "blocks":     blocks,
        "group_by":   ["date", "time", "step"],
        "output_dir": "./output",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(grib_path, out_path=None):
    print(f"[read]  {grib_path}")
    records = read_records(grib_path)
    if not records:
        sys.exit("[error] No fields found in GRIB file.")
    print(f"[info]  {len(records)} field(s) found")

    config = build_config(records)

    # Ensure times stay as quoted strings (leading zeros must be preserved)
    config["times"] = [str(t) for t in config["times"]]

    out = yaml.dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True)

    if out_path:
        with open(out_path, "w") as f:
            f.write(out)
        print(f"[done]  config written to {out_path}")
    else:
        print(out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <input.grib> [output_config.yaml]")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
