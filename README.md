# fdb-to-grib

Extract fields from FDB and write them to grouped GRIB files, driven by a YAML config.

## Requirements

- balfrin (FDB filesystem is only mounted there)
- `uenv fdb/5.18:v3`

## Scripts

| Script | Purpose |
|---|---|
| `fdb_to_grib.py` | Read config, fetch from FDB, write GRIB files |
| `grib_to_config.py` | Inspect an existing GRIB file and generate the matching config |

---

## `fdb_to_grib.py`

```
uenv run --view=<view> fdb/5.18:v3 -- python fdb_to_grib.py config.yaml
```

| Model | `--view` |
|---|---|
| `ICON-CH1-EPS`, `ICON-CH2-EPS` | `realtime` |
| `icon-rea-l-ch1` | `rea-l-ch1` |

### Config file

The config is validated against [`config_schema.json`](config_schema.json) at startup.
In VS Code, add the following line at the top of your YAML file for inline validation and autocomplete
(requires the [YAML extension](https://marketplace.visualstudio.com/items?itemName=redhat.vscode-yaml)):

```yaml
# yaml-language-server: $schema=./config_schema.json
```

#### Top-level keys

| Key | Required | Type | Description |
|---|---|---|---|
| `model` | yes | string | Model name. Sets `class`, `stream`, `expver` automatically. One of `ICON-CH1-EPS`, `ICON-CH2-EPS`, `icon-rea-l-ch1`. |
| `type` | no | string | MARS type. Default: `cf`. Typical values: `cf` (control forecast), `pf` (perturbed forecast), `an` (analysis). |
| `date_range` | yes | object | `start` and `end` dates in `YYYYMMDD` format (inclusive). |
| `times` | yes | list of strings | Run times in `HHMM` format. |
| `steps` | yes | list or range | Global step specification applied to all blocks (unless a block overrides it). |
| `blocks` | yes | list of block objects | One block per `levtype`. |
| `group_by` | yes | list of strings | GRIB metadata keys used to split output files. Fields with identical values for all keys go into the same file. |
| `output_dir` | no | string | Root directory for output files. Default: `./output`. Created if it does not exist. |
| `output_template` | no | string | Filename template using `{key}` placeholders for each `group_by` key. Slashes in the template create subdirectories. Default: `{key1}_{key2}_....grib`. |

#### Step / level specification

Both `steps` and `levels` accept either an explicit list or a range dict:

```yaml
# explicit list
steps: [0, 6, 12, 24]

# arithmetic range  →  produces "0/to/24/by/6" in the FDB request
steps:
  start: 0
  end: 24
  by: 6          # optional, default 1

# minute-based range  →  produces "0m/to/1440m/by/10m"
steps:
  start: 0
  end: 1440
  by: 10
  unit: m
```

#### Block keys

| Key | Required | Type | Description |
|---|---|---|---|
| `levtype` | yes | string | Level type: `sfc`, `ml`, `pl`, `dp`, `hl`. |
| `params` | yes | list of int | `paramId` values to retrieve. |
| `levels` | for non-sfc | list or range | Levels to retrieve (`levelist`). Not used for `levtype: sfc`. |
| `steps` | no | list or range | Per-block step override. Useful when different `levtype`s use different step units. |

---

### Examples

#### Example 1 — REA-L-CH1: surface and model-level fields, grouped by date and step

```yaml
# yaml-language-server: $schema=./config_schema.json
model: icon-rea-l-ch1
type: cf

date_range:
  start: "20250101"
  end: "20250103"

times:
  - "0000"

steps:
  start: 0
  end: 24
  by: 1

blocks:
  - levtype: sfc
    params:
      - 500011   # T_2M
      - 500017   # TD_2M
    steps:       # sfc instant vars use minute-based steps
      start: 0
      end: 1440
      by: 10
      unit: m

  - levtype: ml
    params:
      - 500014   # T
      - 500028   # U
      - 500030   # V
    levels:
      start: 1
      end: 80

group_by:
  - date
  - time
  - step

output_dir: ./output
output_template: "{date}_{time}_{step}.grib"
```

```bash
uenv run --view=rea-l-ch1 fdb/5.18:v3 -- python fdb_to_grib.py config.yaml
```

Output: one GRIB file per `(date, time, step)` combination, e.g. `output/20250101_0000_0.grib`.

---

#### Example 2 — ICON-CH1-EPS: one directory per run, one file per step

```yaml
# yaml-language-server: $schema=./config_schema.json
model: ICON-CH1-EPS
type: cf

date_range:
  start: "20260402"
  end: "20260402"

times:
  - "0900"

steps:
  start: 0
  end: 33
  by: 1

blocks:
  - levtype: sfc
    params:
      - 500011   # T_2M
      - 500041   # TOT_PREC
      - 500046   # CLCT

  - levtype: ml
    params:
      - 500014   # T
      - 500001   # P
    levels:
      start: 1
      end: 81

group_by:
  - date
  - time
  - step

output_dir: ./output
output_template: "{date}{time}/forecast_{step}.grib"
```

```bash
uenv run --view=realtime fdb/5.18:v3 -- python fdb_to_grib.py config.yaml
```

Output: `output/202604020900/forecast_0.grib`, `output/202604020900/forecast_1.grib`, …

---

#### Example 3 — group by date only (all steps in one file per day)

```yaml
group_by:
  - date

output_template: "{date}.grib"
```

All steps and levtypes for a given date are written into a single file.

---

## `grib_to_config.py`

Inspects an existing GRIB file and generates the config needed to reconstruct it from FDB.

```bash
uenv run --view=<view> fdb/5.18:v3 -- python grib_to_config.py input.grib [output_config.yaml]
```

If `output_config.yaml` is omitted the config is printed to stdout.

Steps and levels are automatically expressed as ranges where possible.
The `group_by` defaults to `[date, time, step]`; adjust manually if needed.

### Example

```bash
uenv run --view=realtime fdb/5.18:v3 -- \
    python grib_to_config.py \
    /store_new/mch/msopr/osm/ICON-CH1-EPS/MEC_RING/2026040209/i1emec01090000.ctrl \
    config_out.yaml
```

Output:

```yaml
model: ICON-CH1-EPS
type: cf
date_range:
  start: '20260402'
  end: '20260402'
times:
- 0900
steps:
- 33
blocks:
- levtype: ml
  params: [500001, 500014, 500028, 500030, 500032, 500035, 500100, 500101]
  levels:
    start: 1
    end: 81
- levtype: pl
  params: [500048, 500049, 500050]
  levels:
    start: 0
    end: 800
    by: 400
- levtype: sfc
  params: [500000, 500010, 500011, 500015, 500016, 500017, 500027, 500029,
           500041, 500045, 500046, 500164, 500480, 500481]
group_by: [date, time, step]
output_dir: ./output
```
