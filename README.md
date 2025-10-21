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
python ./fetch_clarity_data.py
```

### Docker / Compose (Staging)
Build and run the image in one step:
```bash
docker compose run --env-file .env --build clarityfetch
```

This is equivalent to running the `build` and `run` commands separately:
```bash
docker build -t herop/clarityfetch .
docker run -it --env-file .env herop/clarityfetch
```

### Local MinIO for prototyping connection to AWS S3 API
```bash
docker compose -f docker-compose.minio.yml up -d
```

This will run a local MinIO container on ports 9000/9001.

To use it, set `S3_ENDPOINT_URL=http://localhost:9000`

You can administrate with MinIO using your browser http://localhost:9000

Log in using the values set for `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` (default: `minioadmin` / `minioadmin`)

### Future: GitHub Action (Production)
The production process will eventually be run automatically on a schedule, but it can be triggered manually for early testing as well.

Navigate to https://github.com/healthyregions/clarity-aq-pipeline/actions/workflows/data-cleanup.yml

From here, you can choose to manually Run the Workflow :+1:

On the right side choose "Run workflow"

You should see a dialog open allowing you to choose which branch to run the workflow on - choose `main` unless you are working on a different branch

After selecting the branch, click the green "Run workflow" button at the button of the dialog

Either refresh the page or wait a few seconds, and you should see a new Run appear in the list :tada:

You can click on this run to drill down and see the progress and log out


## Quick Start
Copy `.env.example` to `.env` and edit the `CLARITY_API_KEY`, `S3_ACCESS_KEY`, and `S3_SECRET_KEY`

By default AWS S3 will be used, but MinIO can be configured instead (see below)

Build the Docker image:
```bash
docker compose --profile pipeline build
```

Fetch sensor metrics data from clarity to the local data folder:
```bash
docker compose run -it clarityfetch

INFO:__main__:Submitting query: {'org': 'cityof58A9', 'continuationToken': 'aknJUeyJvIjogIm...'}
INFO:__main__:Writing query to file: /usr/app/data/query.json
INFO:__main__:Writing data to file: /usr/app/data/raw.csv
INFO:__main__:Writing locations to file: /usr/app/data/locations.json
INFO:__main__:Running post-processing: /usr/app/data/raw.csv -> /usr/app/data/cleaned.json
INFO:__main__:Data fetched successfully!
```

FUTURE: Clean the downloaded data before uploading:
```bash
# docker compose run -it datacleanup
```

Push cleaned output data to AWS S3:
```bash
docker compose run -it s3push

INFO:__main__:Uploaded successfully to AWS S3 (us-east-2): /usr/app/data/locations.json -> chicago-aq/2025-10-09@19:54:20/locations.json
INFO:__main__:Uploaded successfully to AWS S3 (us-east-2): /usr/app/data/cleaned.json -> chicago-aq/2025-10-09@19:54:20/cleaned.json
INFO:__main__:Uploaded successfully to AWS S3 (us-east-2): /usr/app/data/cleaned.json -> chicago-aq/latest.json
INFO:__main__:Uploaded successfully to AWS S3 (us-east-2): /usr/app/data/raw.csv -> chicago-aq/2025-10-09@19:54:20/raw.csv
INFO:__main__:Uploaded successfully to AWS S3 (us-east-2): /usr/app/data/token.txt -> chicago-aq/token.txt
INFO:__main__:Data pushed successfully!
```

### Using MinIO
Edit `.env` to also update the `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD`

(Optional) start up a local MinIO instance for free testing:
```bash
docker compose --profile minio up -d
```

This will start up MinIO on port 9000 which you can access using your browser: http://localhost:9000

You should be able to log in using the user / password supplied above. Default: `minioadmin` / `minioadmin`

To use MinIO with the script, set the following:
```
S3_ACCESS_KEY=MINIO_ROOT_USER
S3_SECRET_KEY=MINIO_ROOT_PASSWORD
S3_ENDPOINT_URL=http://localhost:9000
```

When you run the pipeline, you will see your files are uploaded to MinIO instead:
```bash
docker compose run -it s3push

INFO:__main__:Uploaded successfully to MinIO (http://localhost:9000): /usr/app/data/locations.json -> chicago-aq/2025-10-09@19:54:20/locations.json
INFO:__main__:Uploaded successfully to MinIO (http://localhost:9000): /usr/app/data/cleaned.json -> chicago-aq/2025-10-09@19:54:20/cleaned.json
INFO:__main__:Uploaded successfully to MinIO (http://localhost:9000): /usr/app/data/cleaned.json -> chicago-aq/latest.json
INFO:__main__:Uploaded successfully to MinIO (http://localhost:9000): /usr/app/data/raw.csv -> chicago-aq/2025-10-09@19:54:20/raw.csv
INFO:__main__:Uploaded successfully to MinIO (http://localhost:9000): /usr/app/data/token.txt -> chicago-aq/token.txt
INFO:__main__:Data pushed successfully!
```
