# Tax-Delinquent Parcel Finder

Turns a county tax office's bulk download into a scored, filterable list of
parcels that are likely to sell below market. It targets four seller types:

| Signal | How it is read from the roll | Default points |
|---|---|---|
| Behind on taxes | unpaid receivables whose delinquency date has passed | **+3**, plus **+1 per year** behind |
| Out-of-state owner | mailing state is not `TX` | **+2** |
| Estate / heirs | owner name matches `ESTATE`, `EST OF`, `HEIRS`, `ET AL`, `DECD`, `DECEASED`, `LIFE ESTATE` (but not `ESTATES` or `REAL ESTATE`) | **+2** |
| Owned 10+ years | deed date is 10+ years ago | **+1** |
| Vacant | $0 improvements with land value, or a vacant-land property code | **+1** |
| Absentee | no homestead exemption | **+1** |
| Tax suit filed / judgment | suit or judgment date on a receivable: the county is already heading to a sale | **+1** each |
| Bankruptcy | bankruptcy date on an unpaid receivable (automatic stay) | **−3** |
| Tax deferral | 65+/disabled deferral: the county can't foreclose | **−3** |
| Flood zone / no road access | from a GIS enrichment CSV | **−5** each |

Every weight can be changed at build time, e.g. `build --w-out-of-state 3 --w-absentee 0`.
Each parcel also gets a percentile rank, so "mail the top 25%" is a single filter.

## Run it

```bash
# Load both Denton zips (they overlap; accounts and receivables are de-duplicated)
python -m parcel_finder ingest TaxRoll_V1_20260922_085056.zip TaxRoll_V1_20260922_083833.zip

# Score (re-run any time with different weights; no re-ingest)
python -m parcel_finder build

# Browse at http://127.0.0.1:8765/
python -m parcel_finder serve

# Mailing list: top 25% of real property, mailable owners only
python -m parcel_finder export mail.csv --top-pct 25
```

On Windows, pass full paths, e.g. `C:\Users\<you>\Downloads\TaxRoll_V1_20260922_085056.zip`.
`python -m parcel_finder demo` runs the whole thing on synthetic data.
Ingest takes about 30 s and build about 5 s for Denton's roll.

## What's in Denton's download

Denton publishes its **delinquent** roll: every account that owes back taxes.
It is not the full appraisal roll. The 2026-09-22 files hold 43,939 accounts
and $45.0M due.

* `MM`/`AM`: Master. 950-character fixed-width records: owner, mailing address,
  situs street, legal, SPTB and roll codes, values, exemption codes, deed and
  deferral dates.
* `MR`/`AR`: Receivable. 224-character fixed-width records, one per account,
  year, unit, type and sequence: levy, amount due, delinquency, suit,
  judgment and bankruptcy dates.
* `MS`/`AS`: Statistic. Tab-delimited: count and amount due by year.
* `TU`: Tax units. Tab-delimited: unit number, alpha code and name.

`M` and `A` are nearly the same set. The M files add 149 accounts, mostly
agricultural rollback levies (receivable type 025). Load both. After
de-duplication, the receivables reconcile to the statistic file's total
to the dollar.

Only **6,818** accounts are real property (roll code 001). The rest are
mineral interests (25k), business personal property (7.8k), mobile homes and
similar. The page and the export default to real property. Percentiles rank
real property only against real property.

### Layout

[`layouts/denton.json`](layouts/denton.json) is taken field by field from the
county's *SFTP File Layout & Data Guide*. Record widths match the files
exactly. `01/01/9999` means a blank date; 1899/1900 deed dates are treated as
blank too.

The guide doesn't document the following, so they are **inferred** from the
data (see `_inferred` in the layout):

* **Homestead** = exemption codes 1, 2, 3 or 52. On those accounts the mailing
  address is the property address 85–92% of the time, vs 21% with no
  exemption.
* **Vacant codes**: SPTB `003`, `010`, `024`, `028`, `086`, `092`. These codes
  have $0 improvements on 90–100% of their accounts.
* **City**: the roll has no situs city, so it comes from the account's city tax
  unit (`C01`–`C48` in the TU file). No city unit means **UNINCORPORATED**.
* **Denton CAD link**: the tax-office account is the CAD property ID plus a
  district suffix, so `963342DEN` becomes
  `https://www.dentoncad.com/property-detail/963342`. Parcels that straddle
  the county line carry another district's suffix (`TAR` Tarrant, `WIS` Wise,
  `COK` Cooke, ...). The page shows that district's name instead of a link.
* **Deed date** is populated on about a quarter of accounts, so "owned 10+ years" only
  fires where it is known.

Adding another county means writing a new `layouts/<county>.json` (fixed or
delimited files, plus a `canonical` map to the app's field names) and passing
`--layout <county>`. `inspect <zip>` prints the first record parsed with a
layout, so a wrong position is easy to spot.

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
