import os
import clarityio
import pandas as pd
import requests
import sys

from pandas.io import json


RAW_DATA_OUTPUT_PATH = os.getenv('RAW_DATA_OUTPUT_PATH', '/usr/app/data/raw.csv')
OUTPUT_LOCATIONS_AS_CSV = os.getenv('OUTPUT_LOCATIONS_AS_CSV', 'False').lower() in ('true', '1', 't')
LOCATIONS_OUTPUT_PATH = os.getenv('LOCATIONS_OUTPUT_PATH', '/usr/app/data/locations.csv')

# Endpoint URLs for Clarity's API
RECENT_MEASUREMENTS_URL='https://clarity-data-api.clarity.io/v2/recent-datasource-measurements-query'
CONTINUATION_URL='https://clarity-data-api.clarity.io/v2/recent-datasource-measurements-continuation'

# Save this for later
CLARITY_API_KEY = os.getenv('CLARITY_API_KEY')
CLARITY_ORG_NAME = os.getenv('CLARITY_ORG_NAME', 'cityof58A9')
CLARITY_CONTINUATION_TOKEN = os.getenv('CLARITY_CONTINUATION_TOKEN', None)

# Fetch input parameters from envvars
api_connection = clarityio.ClarityAPIConnection(
    api_key=CLARITY_API_KEY,
    org=CLARITY_ORG_NAME
)

headers = {
  'Content-Type': 'application/json',
  'Accept': 'application/json',
  'Accept-Encoding': 'gzip',
  'x-api-key': CLARITY_API_KEY
}

# Example: curl https://clarity-data-api.clarity.io/v2/recent-datasource-measurements-query -H 'Content-Type: application/json' -H "x-api-key: ${CLARITY_API_KEY}" -H 'Accept: application/json' -d '{"org":"cityof58A9","allDatasources":true,"replyWithContinuationToken":true,"outputFrequency":"hour","format":"csv-wide"}' -vvvv
# Currently unused - this allows us to experiment with using continuation tokens
def fetch_metrics_direct():
    if CONTINUATION_TOKEN is None:
        requests.post(
            url=RECENT_MEASUREMENTS_URL,
            headers=headers,
            data=json.dumps({
                'org': CLARITY_ORG_NAME,
                'allDatasources': True,
            }))
        # TODO: store continuation token somewhere? S3?
    else:
        requests.post(
            url=CONTINUATION_URL,
            headers=headers,
            data=json.dumps({
                'org': CLARITY_ORG_NAME,
                'continuationToken': CONTINUATION_TOKEN
            }))
        # TODO: how to pass in stored continuation token?

def fetch_metrics():
    request_body = {
        'allDatasources': True,
        'outputFrequency': 'hour',
        'format': 'json-long'
    }
    response = api_connection.get_recent_measurements(data=request_body)
    if response is not None:
        return pd.DataFrame(response['data']), pd.DataFrame(response['locations'])
    else:
        print('Failed to fetch metrics from Clarity API')
        sys.exit(1)


def main():
    print("Fetching metrics...")
    metrics, locations = fetch_metrics()
    print("Metrics fetched:")
    print(metrics)

    print(f"Writing data to file: {RAW_DATA_OUTPUT_PATH}")
    metrics.to_csv(RAW_DATA_OUTPUT_PATH, index=False)

    if OUTPUT_LOCATIONS_AS_CSV:
        print("Locations fetched:")
        print(locations)

        print(f"Writing locations to file: {LOCATIONS_OUTPUT_PATH}")
        locations.to_csv(LOCATIONS_OUTPUT_PATH, index=False)


if __name__ == "__main__":
    main()
