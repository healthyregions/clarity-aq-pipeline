# clarity-aq-pipeline

This repo holds scripts and Github Actions that extract and manipulate air quality sensor data from Clarity's API for aggregated storage and display in TDB web-based visualizations.

## Resources

- Clarity API docs: https://api-guide.clarity.io/
- Sample data: ./sample-data/Clarity_sample_data-40k.csv
    - We've been provided this CSV of sample data from Clarity. Originally this was a much bigger CSV (~100mb) so I've truncated it to ~40k rows.
    - The sample data looks to be in the [csv-wide](https://api-guide.clarity.io/v2/measurements/recent-datasource-measurements/#csv-wide) format, and it includes many different measurements (the majority of which have empty values).
    - On first glance it includes a continuous series of measurements from 4 different sources over a period of time
    - We will presumably be selecting a much smaller set of variables. There is also a [json-long](https://api-guide.clarity.io/v2/measurements/recent-datasource-measurements/#json-long) format.

## Workflow considerations

Draft workflow pipeline:

![image](https://github.com/user-attachments/assets/2790d444-d92f-420e-a28b-99f469c08399)

### Unknowns

- What metrics will we be collecting?
- How many measurements will we be getting per day?
- What temporal resolution is needed on the frontend visualizations?
    - Do we need to show current air conditions?
    - Do we need to show yesterday, 7-day average, etc?
- How often should we request data?
    - Probably once every 24 hours
    - [Continuation tokens](https://api-guide.clarity.io/v2/measurements/recent-datasource-measurements/#continuations) allow us to query for all measurements that have been taken since our last query
- Where will we store the data?
    - Options include static files in S3, RDS database, Google BigQuery, etc.
    - If we go the static file route, [parquet](https://parquet.apache.org/) or [geoparquet](https://geoparquet.org/) files are an intriguing option, as they are static files that can be queried with SQL using DuckDB
        - Workflow example with (geo)parquet files:
            1. Script/job 1 fetches data from Clarity API, performs cleaning/transformations on it, and writes data to new daily parquet file
            2. Script/job 2 runs sql queries across latest file and past files to generate various
        - A geoparquet file would allow for spatial queries, such as all censor measurements within a certain area of interest


## Quick Start
Python client for fetching recent and historical measurements from the [Clarity](https://www.clarity.io/) REST API V2.

Cleans measurement data using R script.

Merges resulting cleaned data into Parquet dataset in S3.

## Getting Started
Prerequisites:
* Recommended: Docker Engine (Linux) or Docker Desktop (macOS / Windows)
* Conda + Python for local development

Clone this repo to get started:
```bash
git clone https://github.com/healthyregions/clarity-aq-pipeline
cd clarity-aq-pipeline/
```

First, copy `.env.example` to `.env` and edit the `CLARITY_API_KEY`, `S3_ACCESS_KEY`, and `S3_SECRET_KEY`:
```bash
cp .env.example .env
vi .env
```
*By default AWS S3 will be used, but MinIO can be configured instead (see below)*

### Docker / Compose (Staging)
To build a fresh Docker image:
```bash
docker compose build aq
```
If you modify any of the source code, you will need to rebuild the image (or add `--build` to any command below)

Each of these examples will run all stages of the pipeline (see below). We provide:
```bash
# Recent measurements (default: past ~3 hours)
docker compose run --rm -it aq --recent --fetch --clean --merge
# Historical measurements (default: past full month)
docker compose run --rm -it aq --historical --fetch --clean --merge

# Past full day (>20 valid hours)
docker compose run --rm -it aq --historical --fetch --clean --merge --daily
# Past full week (>5 valid days)
docker compose run --rm -it aq --historical --fetch --clean --merge --weekly
# Past full month (>21 valid days)
docker compose run --rm -it aq --historical --fetch --clean --merge --monthly
# Past full season (>60 valid days)
docker compose run --rm -it aq --historical --fetch --clean --merge --seasonal
# Past full year (>220 valid days)
docker compose run --rm -it aq --historical --fetch --clean --merge --yearly

# Custom date range: between 2026-01-01T00:00:00Z and now
docker compose run --rm -it aq --recent --fetch --clean --merge '2026-01-01T00:00:00Z'
# Custom date range: between 2026-01-01T00:00:00Z and 2026-02-01T00:00:00Z
docker compose run --rm -it aq --historical --fetch --clean --merge '2026-01-01T00:00:00Z' '2026-02-01T00:00:00Z'
```

### Conda + Python (Local Development)
Create and switch to new conda environment, then run python script
```bash
conda create -n clarity -f environment.yml
conda activate clarity

# Recent measurements (default: past ~3 hours)
python ./main.py --recent --fetch --clean --merge
# Historical measurements (default: past full month)
python ./main.py --historical --fetch --clean --merge

# Past full day (>20 valid hours)
python ./main.py --historical --fetch --clean --merge --daily
# Past full week (>5 valid days)
python ./main.py --historical --fetch --clean --merge --weekly
# Past full month (>21 valid days)
python ./main.py --historical --fetch --clean --merge --monthly
# Past full season (>60 valid days)
python ./main.py --historical --fetch --clean --merge --seasonal
# Past full year (>220 valid days)
python ./main.py --historical --fetch --clean --merge --yearly

# Custom date range: between 2026-01-01T00:00:00Z and now
python ./main.py --recent --fetch --clean --merge '2026-01-01T00:00:00Z'
# Custom date range: between 2026-01-01T00:00:00Z and 2026-02-01T00:00:00Z
python ./main.py --historical --fetch --clean --merge '2026-01-01T00:00:00Z' '2026-02-01T00:00:00Z'
```


## Modes of Operation
There are 2 different types of measurements we can fetch:
* `--historical` Measurements occurred between start time and end time
* `--recent` Measurements occurred between start time and now

### Pipeline Stages
There are 3 stages that will always be run in a predetermined order:
* `--fetch` new measurement values from Clarity API. 
  * Also fetches and merges `locations.parquet`, since it is only returned as part of the request to fetch measurements from the API.
  * Performs and additional fetch on the Datasources API to gather the name, group, and tags for each sensor.
  * For testing if `--fetch` is not provided, the most recently fetched measurements will be used instead.
* `--clean` the most recently fetched measurements using the R script
  * The R script currently expects the following metrics to be part of the returned data:
    * `pm2_5ConcMassIndividual`
    * `relHumidInternalIndividual`
    * `temperatureInternalIndividual`
  * Validity Thresholds - averages will be disregarded if they do not meet these minimums
    * `hourly`: >=75% completeness to qualify as a valid hourly average
    * `daily`: >20 valid hours to qualify as a valid daily average
    * `weekly`: >5 valid days to qualify as a valid weekly average
    * `monthly`: >21 valid days to qualify as a valid monthly average
    * `seasonal`: >60 valid days to qualify as a valid seasonal average
    * `yearly`: >220 valid days to qualify as a valid yearly average
* `--merge` the cleaned measurements into the Parquet dataset in S3

### Resulting S3 Files
Various Parquet datasets are produced by this process. If these files exist, their contents will be merged with any updated data received.

`locations.parquet` - contains sensor lat/long, names, groups, tags, zip*, neighborhood*
  * \* denotes a column that was manually added - these columns are not returned by the Clarity API
  * This is created during the `--fetch` stage (see below)
```bash
INFO:config:Successfully updated locations dataset!
    datasourceId  sourceId currentSourceId  ...               name  group     tags
0      DACZY2913  A3KT6T74        A3KT6T74  ...      Rogers Park 2   None       []     
1      DAETQ5676  A74GKPFK        A74GKPFK  ...        Montclare 2   None       []     
2      DAEXF8484  AP7HCCXJ        AP7HCCXJ  ...  Mount Greenwood 1   None       []     
3      DAHAJ0678  A9QKCK4N        A9QKCK4N  ...          Ashburn 1   None       []     
4      DAHWZ2321  AQ96FCW7        AQ96FCW7  ...      Albany Park 2   None       []     
..           ...       ...             ...  ...                ...    ...      ...     
275    DZIHS7092  A44F7W3K        A44F7W3K  ...   Archer Heights 2   None       []     
276    DZKWB0839  ANNVFLYT        ANNVFLYT  ...        East Side 2   None  [ComEd]     
277    DZLAV7766  AQ3669WV        AQ3669WV  ...           Austin 4   None       []     
278    DZTFU6199  A6LYVKHK        A6LYVKHK  ...          Ashburn 7   None       []     
279    DZUWB2477  ANMRQ7C4        ANMRQ7C4  ...   North Lawndale 1   None  [ComEd]     

[280 rows x 9 columns]
```


`{metric_name}.parquet` - contains sensor values for this metrics
  * One Parquet file for each metric tracked (currently only `mean_pm25`)
  * This is created during the `--merge` stage (see below)
```bash
INFO:config:Successfully updated parquet file in S3: chicago-aq/current/mean_pm25.parquet
INFO:config:Successfully updated mean_pm25 dataset!
datasourceId   type                 date  DACZY2913  ...  DZLAV7766  DZTFU6199  DZUWB2477
0             month              2026-01  13.263748  ...  14.957234  17.036295  23.073780
5              week             2026-W06  22.814504  ...  28.335486  31.677738  33.831081
4              week             2026-W05   7.020174  ...   9.402333  13.721105  24.269538
3              week             2026-W04   7.042138  ...        NaN        NaN        NaN
2              week             2026-W03   8.427384  ...   8.194707   9.966354  13.482440
..              ...                  ...        ...  ...        ...        ...        ...
49             hour  2026-01-01 04:00:00   3.963333  ...   3.640000   8.830000   6.770000
48             hour  2026-01-01 03:00:00   6.847500  ...   8.086667  10.962500  10.385000
47             hour  2026-01-01 02:00:00   9.153333  ...  13.365000  21.553333  16.566667
46             hour  2026-01-01 01:00:00  14.027500  ...  19.863333  19.992500  22.990000
45             hour           2026-01-01  19.163333  ...  20.497500  28.845000  30.080000

[986 rows x 281 columns]
```


These Parquet files are publicly available for download in our S3 bucket: LINK


## Local Testing Using MinIO
Edit `.env` to also update the `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD`

(Optional) start up a local MinIO instance for free testing:
```bash
docker compose up -d
```

This will start up MinIO on port 9000 which you can access using your browser: http://localhost:9000

You should be able to log in using the user / password supplied above. Default: `minioadmin` / `minioadmin`

You will the need to set the following in `.env`:
```
S3_ACCESS_KEY=MINIO_ROOT_USER
S3_SECRET_KEY=MINIO_ROOT_PASSWORD
S3_ENDPOINT_URL=http://localhost:9000
```

Then run the same pipeline command that you would normally run.


## Future: GitHub Action (Production)
The production process will eventually be run automatically on a schedule, but it can be triggered manually for early testing as well.

Navigate to https://github.com/healthyregions/clarity-aq-pipeline/actions/workflows/data-cleanup.yml

From here, you can choose to manually Run the Workflow :+1:

On the right side choose "Run workflow"

You should see a dialog open allowing you to choose which branch to run the workflow on - choose `main` unless you are working on a different branch

After selecting the branch, click the green "Run workflow" button at the button of the dialog

Either refresh the page or wait a few seconds, and you should see a new Run appear in the list :tada:

You can click on this run to drill down and see the progress and log out