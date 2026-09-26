# Tax-Delinquent Parcel Finder

Turns a county tax office's bulk download into a scored, filterable list of
parcels that are likely to sell below market. It targets four seller types:

| Signal | How it is read from the roll | Default points |
|---|---|---|
| Behind on taxes | unpaid receivable rows for tax years already past due (Texas bills go delinquent Feb 1) | **+3**, plus **+1 per year** behind |
| Out-of-state owner | mailing state is not `TX` | **+2** |
| Estate / heirs | owner name matches `ESTATE`, `EST OF`, `HEIRS`, `ET AL`, `DECD`, `LIFE ESTATE` (but not `ESTATES` or `REAL ESTATE`) | **+2** |
| Owned 10+ years | deed date is 10+ years ago (only if the file has a deed date) | **+1** |
| Vacant | improvement value is $0, or property code is C1 / D1 / D2 / E | **+1** |
| Absentee | no homestead exemption | **+1** |
| Flood zone | from a GIS enrichment CSV | **−5** |
| No road access | from a GIS enrichment CSV | **−5** |

Every weight can be changed at build time, e.g. `build --w-out-of-state 3 --w-absentee 0`.
Each parcel also gets a percentile rank, so "mail the top 25%" is a single filter.

## Run it on your own computer (Windows)

Denton's downloads are large, so load them on your own computer. From a clone of this repo:

```powershell
# 1. Check the file layout against the real files (see "Layout" below)
python -m parcel_finder inspect C:\Users\<you>\Downloads\TaxRoll_V1_20260922_085056.zip

# 2. Load both zips. Accounts that appear in both zips are de-duplicated.
python -m parcel_finder ingest C:\Users\<you>\Downloads\TaxRoll_V1_20260922_085056.zip C:\Users\<you>\Downloads\TaxRoll_V1_20260922_083833.zip

# 3. Score (re-run any time with different weights; no re-ingest)
python -m parcel_finder build

# 4. Browse
python -m parcel_finder serve        # then open http://127.0.0.1:8765/

# 5. Mailing list
python -m parcel_finder export mail.csv --top-pct 25
```

`python -m parcel_finder demo` does all of this on synthetic data, with no download.

## Layout: finish this step before trusting results

Denton's zips hold four kinds of file, identified by name prefix:

* `MM` / `AM`: Master (owner, mailing address, situs, legal, values, exemptions)
* `MR` / `AR`: Receivable (one row per account / tax year / taxing unit, with the amount due)
* `MS` / `AS`: Statistic, by year
* `TU`: Tax unit codes and names

Field positions are in [`layouts/denton.json`](layouts/denton.json). **They are
placeholders.** The county site and its File Layout Guide PDF could not be
reached from the environment where this was written. To fix them:

1. Open the File Layout Guide PDF.
2. For each file, set every field's `start` (1-based) and `length`.
3. Where the guide says a field has implied decimals, set `"type": "decimal", "implied_decimals": 2`.
4. If a file is delimited instead of fixed-width, set `"format": "delimited", "delimiter": "|"`, plus `"header": true` if it has a header row.
5. Run `inspect` again. It prints the first record parsed with your layout, so a wrong position is easy to spot.

The `canonical` block maps the app's field names (`owner_name`, `total_due`, ...)
to the layout's column names. If the PDF uses different names, rename them in
one place. A field the county doesn't publish (for example, deed date) can be
left out of `canonical`, and its signal simply won't fire.

Adding another county means adding a new `layouts/<county>.json` and passing
`--layout <county>`.

## Flood zone and road access (optional)

Build a CSV with columns `account,flood_zone,road_access` (values 1, 0, or blank
for unknown) from a GIS join. Use TxGIO StratMap parcels, FEMA NFHL flood
zones, and TxDOT or county roads. Then:

```bash
python -m parcel_finder enrich gis.csv && python -m parcel_finder build
```

An unknown value never costs a parcel points.

## Files

```
parcel_finder/
  layouts/denton.json  county file layout + canonical field map
  layout.py            fixed-width / delimited parsing, file-kind detection
  db.py                ingest (zips -> raw_* tables) and build (-> scored parcels)
  scoring.py           seller signals + score (pure)
  queries.py           filters / sort / paging shared by web + export
  web.py               stdlib HTTP server + JSON API
  static/index.html    the browser page
  sample.py            synthetic county download for the demo and tests
```
