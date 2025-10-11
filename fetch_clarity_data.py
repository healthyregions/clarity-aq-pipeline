# Built-ins
import argparse
import datetime
import json
import logging
import os
import pandas as pd
from pandas import DataFrame
from pathlib import Path
import requests
import s3fs
import sys

# Configure logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Connection details for Clarity API
CLARITY_ORG_NAME = os.getenv('CLARITY_ORG_NAME', 'cityof58A9')
CLARITY_API_KEY = os.getenv('CLARITY_API_KEY')
CLARITY_HOSTNAME = 'https://clarity-data-api.clarity.io/v2'

# S3 Endpoint URL: use default for AWS S3 or override for MinIO (e.g. http://localhost:9000)
S3_ENDPOINT_URL = os.getenv('S3_ENDPOINT_URL', None)

# if using AWS S3, you should specify a region (e.g. us-east-2)
S3_REGION = os.getenv('S3_REGION', 'us-east-2')

# The S3 bucket where the files should be uploaded
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME', 'chicago-aq')

# Credentials for S3
S3_ACCESS_KEY = os.getenv('S3_ACCESS_KEY', None)
S3_SECRET_KEY = os.getenv('S3_SECRET_KEY', None)
IS_MINIO = True if S3_ENDPOINT_URL else False

# Build an S3 client using the given credentials + endpoint
s3_client = s3fs.S3FileSystem(anon=False, key=S3_ACCESS_KEY, secret=S3_SECRET_KEY, client_kwargs={
    # Use custom endpoint URL for self-hosted (e.g. MinIO)
    'endpoint_url': S3_ENDPOINT_URL
}) if IS_MINIO else s3fs.S3FileSystem(anon=False, key=S3_ACCESS_KEY, secret=S3_SECRET_KEY, client_kwargs={
    # For default endpoint, assume AWS S3
    # for this case, we will want to specify a region
    'region_name': S3_REGION
})

# Directory within the bucket (debug / testing only)
# Use S3_UPLOAD_PATH if given, otherwise use timestamp: 2025-10-02@16:41:25
S3_UPLOAD_PATH = os.getenv('S3_UPLOAD_PATH', None)
if not S3_UPLOAD_PATH
    S3_UPLOAD_PATH = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d@%H:%M:%S')

# Local directory that will be uploaded to the bucket
LOCAL_OUTPUT_DIR = os.getenv('LOCAL_OUTPUT_DIR', '/usr/app/data')
RAW_DATA_OUTPUT_PATH = os.getenv('RAW_DATA_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/raw.csv')
CLEANED_DATA_OUTPUT_PATH = os.getenv('CLEANED_DATA_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/cleaned.json')

CONTINUATION_TOKEN_OUTPUT_PATH = os.getenv('CONTINUATION_TOKEN_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/token.txt')
QUERY_OUTPUT_PATH = os.getenv('QUERY_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/query.json')

OUTPUT_LOCATIONS_AS_JSON = os.getenv('OUTPUT_LOCATIONS_AS_JSON', 'False').lower() in ('true', '1', 't')
LOCATIONS_OUTPUT_PATH = os.getenv('LOCATIONS_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/locations.json')


# Data cleanup process will be written in R
# It is likely that these cleanup scripts will expect CSV format
def read_csv(local_path: str):
    return pd.read_csv(local_path)

def write_csv(local_path: str, df: DataFrame):
    df.to_csv(local_path)

def write_txt(local_path: str, data: str):
    with open(local_path, 'w') as f:
        f.write(data)

# Final pipeline output is likely to be JSON
# This ensures that final data is easily consumable by the frontend dashboard
def from_json(data: dict):
    return DataFrame(data)

def write_json_dict(local_path: str, data: dict):
    write_txt(local_path, data=json.dumps(data, indent=4))

# Likely needed to convert the cleaned data back to JSON
def convert_csv_to_json(path: str):
    base_path = Path(path).stem
    df = pd.read_csv(f'{base_path}.csv')
    df.to_json(f'{base_path}.json')


# Likely needed to convert the cleaned data back to JSON
# TODO: convert to 4-digit precision for latlong
def run_postprocessing(input_path: str, output_path: str):
    df = pd.read_csv(input_path, index_col=0)
    df.to_json(output_path, orient='records')


# globals: S3_ENDPOINT_URL, IS_MINIO, S3_REGION, s3_client
class S3API(object):
    def __init__(self, s3_client: s3fs.S3FileSystem):
        self.s3_client = s3_client

    def push_to_s3(self, local_path: str, remote_path: str):
        # Determine destination within S3
        s3_label = 'MinIO' if IS_MINIO else 'AWS S3'
        s3_extra = S3_ENDPOINT_URL if IS_MINIO else S3_REGION

        try:
            # Notify user that upload is about to start
            log.debug(f'Uploading to {s3_label} ({s3_extra}): {local_path}')

            # Upload the local file to S3
            self.s3_client.put(local_path, remote_path)

            # Notify user that upload is about has completed
            log.info(f'Uploaded successfully to {s3_label} ({s3_extra}): {local_path} -> {remote_path}')
        except Exception as ex:
            formatted = f'{local_path} -> {remote_path}'
            log.error(f'Failed to upload file to {s3_label}: {formatted} - {str(ex)}')
            sys.exit(100)


# globals: S3_BUCKET_NAME, CLARITY_API_KEY, CLARITY_ORG_NAME, CONTINUATION_TOKEN_OUTPUT_PATH, s3_client
class ClarityAPI(object):
    def __init__(self, s3_client: s3fs.S3FileSystem):
        self.s3_client = s3_client

        # Endpoint URLs / default headers for Clarity's API
        self.orgName = CLARITY_ORG_NAME
        self.measurementsUrl = f'{CLARITY_HOSTNAME}/recent-datasource-measurements-query'
        self.continuationUrl = f'{CLARITY_HOSTNAME}/recent-datasource-measurements-continuation'
        self.headers = {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'Accept-Encoding': 'gzip',
          'x-api-key': CLARITY_API_KEY
        }

    # Fetch existing continuation token from S3 bucket
    def read_continuation_token(self):
        token_local_path = f'{S3_BUCKET_NAME}/token.txt'
        if self.s3_client.exists(token_local_path):
            with self.s3_client.open(token_local_path, 'r') as f:
                 return f.read()
        return None

    # Write continuation token to local output directory
    # This will be written to S3 with the rest of the uploaded output files
    def write_continuation_token(self, token):
        # store latest continuation token locally
        with open(CONTINUATION_TOKEN_OUTPUT_PATH, 'w') as f:
            f.write(token)

    # Example: curl https://clarity-data-api.clarity.io/v2/recent-datasource-measurements-query -H 'Content-Type: application/json' -H 'x-api-key: ${CLARITY_API_KEY}' -H 'Accept: application/json' -d '{'org':'cityof58A9','allDatasources':true,'replyWithContinuationToken':true,'outputFrequency':'hour','format':'csv-wide'}' -vvvv
    # Currently unused - this allows us to experiment with using continuation tokens
    def fetch_sensor_data(self):
        token = self.read_continuation_token()
        url = self.continuationUrl if token else self.measurementsUrl
        body: dict = { 'org': self.orgName }
        # 'outputFrequency':'hour','format':'csv-wide'
        if token:
            body['continuationToken'] = token
        else:
            body['allDatasources'] = True
            body['outputFrequency'] = 'hour'
            body['replyWithContinuationToken'] = True
            # body['format'] = 'csv-wide'

        redacted = json.loads(json.dumps(body))
        if 'continuationToken' in redacted:
            redacted['continuationToken'] = f'{body["continuationToken"][:20]}...'

        try:
            log.info(f'Submitting query: {redacted}')
            r = requests.post(url=url, headers=self.headers, data=json.dumps(body))
            log.debug(r)
            response = r.json()
            log.debug(response)
            r.raise_for_status()

            query = response['request']
            log.debug(f'Submitted query: {query}')
            data = from_json(response['data'])
            log.debug(f'Fetched data: {data}')
            if 'locations' not in response:
                log.warning(f'Warning: no metric updates detected since last run - skipping pushing empty data file')
                sys.exit(25)

            locations = response['locations']
            log.debug(f'Fetched locations: {locations}')
            token = r.headers['x-clarity-continuation-token']
            log.debug(f'New continuation token: {token}')

            return data, locations, token, query

        except requests.exceptions.ConnectionError as ex:
            log.error(f'Connection Error: Could not connect to the server. Details: {ex}')
            sys.exit(3)
        except requests.exceptions.Timeout as ex:
            log.error(f'Timeout Error: The request timed out. Details: {ex}')
            sys.exit(4)
        except requests.exceptions.HTTPError as ex:
            log.error(f'HTTP Error: A bad HTTP status code was received. Details: {ex}')
            sys.exit(5)
        except requests.exceptions.RequestException as ex:
            log.error(f'An unexpected Requests error occurred. Details: {ex}')
            sys.exit(6)


def main(args):
    if not args.fetch and not args.push:
        log.error('You must specify either -f (--fetch) or -p (--push)')
        sys.exit(200)

    # Fetch metrics from the clarity API
    # Write raw metrics (uncleaned) into the output folder
    if args.fetch:
        log.debug(f'Fetching sensor data from {CLARITY_HOSTNAME}')
        clarity = ClarityAPI(s3_client)
        data, locations, token, query = clarity.fetch_sensor_data()

        # Store metadata about this request: query.json & token.txt
        log.debug(f'Saving continuation token to file: {CONTINUATION_TOKEN_OUTPUT_PATH}')
        write_txt(CONTINUATION_TOKEN_OUTPUT_PATH, token)

        log.info(f'Writing query to file: {QUERY_OUTPUT_PATH}')
        write_json_dict(QUERY_OUTPUT_PATH, query)

        # Since we're using continuation tokens, this
        # won't be the full list of locations every time
        if OUTPUT_LOCATIONS_AS_JSON:
            log.info(f'Writing locations to file: {LOCATIONS_OUTPUT_PATH}')
            write_json_dict(LOCATIONS_OUTPUT_PATH, locations)

        # Output metrics and run post-processing
        log.info(f'Writing data to file: {RAW_DATA_OUTPUT_PATH}')
        write_csv(RAW_DATA_OUTPUT_PATH, data)

        # TODO: adjust after cleaning process is codified
        log.info(f'Running post-processing: {RAW_DATA_OUTPUT_PATH} -> {CLEANED_DATA_OUTPUT_PATH}')
        run_postprocessing(RAW_DATA_OUTPUT_PATH, CLEANED_DATA_OUTPUT_PATH)

        log.info('Data fetched successfully!')

    # Push select files from the output folder to the proper destinations in S3
    if args.push:
        # Mapping of local file source path -> destination path within S3
        outfile_mapping: dict[str, list[str]] = {
            # Comment this line if we don't want to store raw (uncleaned) metrics in S3
            f'{RAW_DATA_OUTPUT_PATH}': [f'{S3_BUCKET_NAME}/{S3_UPLOAD_PATH}/raw.csv'],
            f'{LOCATIONS_OUTPUT_PATH}': [f'{S3_BUCKET_NAME}/{S3_UPLOAD_PATH}/locations.json'],
            f'{QUERY_OUTPUT_PATH}': [f'{S3_BUCKET_NAME}/{S3_UPLOAD_PATH}/query.json'],
            f'{CLEANED_DATA_OUTPUT_PATH}': [
                f'{S3_BUCKET_NAME}/{S3_UPLOAD_PATH}/cleaned.json',
                f'{S3_BUCKET_NAME}/latest.json'
            ],
            f'{CONTINUATION_TOKEN_OUTPUT_PATH}': [f'{S3_BUCKET_NAME}/token.txt'],
        }

        s3api = S3API(s3_client)
        for key in outfile_mapping:
            for dest in outfile_mapping[key]:
                s3api.push_to_s3(local_path=key, remote_path=dest)

        log.info('Data pushed successfully!')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Chicago Air Quality Sensor Grid')
    parser.add_argument('-f', '--fetch', action='store_true', help='Fetch sensor metrics from Clarity REST API v2')
    #parser.add_argument('-l', '--locations',  action='store_false' if OUTPUT_LOCATIONS_AS_JSON else 'store_true', help='Also output locations from Clarity REST API v2')
    # Data cleanup will take place in between these two independent steps
    parser.add_argument('-p', '--push',  action='store_true',  help='Upload resulting output data to S3 (Minio or AWS S3)')

    main(args=parser.parse_args())
