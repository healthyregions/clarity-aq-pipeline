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


## Early Testing
You can run this script with either Python or Docker

Clone this repo to get started:
```bash
git clone https://github.com/healthyregions/clarity-aq-pipeline
cd clarity-aq-pipeline/
```

Then create a copy of `.env.example` to set your API KEY:
```bash
cp .env.example .env
vi .env
```

### Python (Local Development)
```bash
pip install -r requirements.txt
python ./fetch-clarity-data.py
```

### Docker / Compose (Staging)
Build and run the image in one step:
```bash
docker compose run --build clarityfetch
```

This is equivalent to running the `build` and `run` commands separately:
```bash
docker build -t herop/clarityfetch .
docker run -it herop/clarityfetch
```

### Future: GitHub Action (Production)
The production process is run automatically on a schedule, but it can be triggered manually for testing as well.

Navigate to https://github.com/healthyregions/clarity-aq-pipeline/actions/workflows/data-cleanup.yml

From here, you can choose to manually Run the Workflow :+1:


