# Contract Vehicle Route Analysis

A read-only dashboard for the fixed contract-vehicle route **Coimbatore → Salem → Trichy → Chennai**. The API defaults to the rolling one-year period ending today and accepts `startDate` and `endDate` overrides.

## Stack

- Python 3.11+ and FastAPI for the REST API
- psycopg 3 for PostgreSQL access (every connection sets the transaction to read-only)
- NetworkX for the four-node, three-edge route graph
- HTML, CSS and JavaScript for a lightweight dashboard served by FastAPI
- Pydantic Settings for local environment configuration

## Observed database mapping

The local database schema was inspected read-only. The implementation uses these existing objects:

| Purpose | Table and fields |
| --- | --- |
| Directed scheduled leg definitions | `dbo.route` (`route`, `start`, `stop`, `via`, `type`) |
| Dated trip and vehicle | `dbo.tbl_despatch_header` (`tssno`, `tssdate`, `vehicleno`, `route`) |
| Contract trip marker and stored hire amount | `dbo.tbl_contvehent` (`tssno`, `tothire`) |
| Dispatched waybill relationship | `dbo.tbl_despatch_detl` (`tssno`, `prefix`, `wayno`) |
| Charge waybill weight used for load analysis | `dbo.wbhead` (`prefix`, `wayno`, `chargewt`) |
| Vehicle capacity | `dbo.tbl_tss_truck` (`regno`, `capacity`) |
| Physical dispatch and arrival events | `dbo.tbl_veharrival_despatch` (`tssno`, `type`, `date`, `time`) |

The fixed station codes used in this database are Coimbatore `CBETR`, Salem `SALTR`, Trichy `TPJTR`, and Chennai `MASTR`. Only route rows with type `R`, direct via `DIR`, and those consecutive endpoints are considered. Contract membership is identified by a matching `tbl_contvehent.tssno`; owned/other vehicle types are not used as the contract filter. No foreign-key constraints were declared for these legacy relationships, so the joins use the observed business keys.

`wbhead.chargewt` is treated as kilograms and `tbl_tss_truck.capacity` as metric tons, using `LOAD_TO_CAPACITY_FACTOR=0.001`. Change this setting if the data source's units differ. Travel duration is calculated only when a matching `Despatch` and `Arrival` event exists for the trip. The summary trip count is the sum of the three leg movement counts because this schema does not store one through-route trip identity spanning all three legs.

## Run locally

1. Use Python 3.11 or newer.
2. Create a virtual environment and install dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and set the local PostgreSQL password. The existing local `.env` is ignored by Git.
4. Start the app:

   ```powershell
   uvicorn app.main:app --reload
   ```

5. Open `http://127.0.0.1:8000`. API docs are at `/docs`.

## API

`GET /api/v1/network/contract-route-analysis`

Optional ISO date parameters: `startDate`, `endDate`. Without parameters, the range is one calendar year ending today in the `Asia/Kolkata` time zone.

`GET /api/v1/network/health` checks the configured connection and reports only whether it is reachable.

## Status thresholds

The initial thresholds in `.env.example` are provisional configuration defaults, not approved business targets: utilization from 70% through 100% is eligible for `GOOD`, under 40% is `POOR`, above capacity is `POOR`, average travel above 12 hours triggers `NEEDS ATTENTION`, and above 18 hours triggers `POOR`. These are deliberately exposed as environment settings so the business can replace them. Price is displayed and summarized, but is not used to judge performance because no price target was provided.

If a required KPI is absent, the API returns `null` and the UI displays “Insufficient Data”; it does not replace missing fields with zero.
