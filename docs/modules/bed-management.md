# Bed management (`app/beds/`, `app/api/beds.py`)

**Purpose:** replace IHH's Excel bed sheet: beds and their attributes, availability, holds while a referral is
reviewed, assignments on approval, discharge.

**Model:** `Site` → `Bed` (sex designation male/female/any, wheelchair_accessible, ground_floor, oxygen_ok,
isolation_ok, stored status available/occupied/out_of_service) · `Hold` (referral_id, expires_at, default 4 h) ·
`Assignment` (referral_id, client_label, dates, active/released). **"held" is derived** from an active, unexpired hold.

**Rules:** matching filters by availability, sex compatibility (unknown sex → only "any" beds), wheelchair, oxygen,
isolation; it ranks the least specialized bed first so accessible/oxygen beds are kept for those who need them.
`assign` re-checks availability inside the transaction and raises `BedUnavailable` (→ HTTP 409).

**Import:** `POST /api/beds/import` takes .csv/.xlsx. Headers are matched case-insensitively (`DEFAULT_MAPPING` in
`importer.py`). Bed ID = explicit `id` column, else `slug(site-room-bed)`. Re-import updates.

**Pending:** IHH's real sheet layout. When it arrives, extend `DEFAULT_MAPPING` and the `Bed` fields only. The
`service.py` interface stays stable.

**Testing:** `pytest tests/test_beds_service.py tests/test_beds_import.py tests/test_api_beds.py`.
