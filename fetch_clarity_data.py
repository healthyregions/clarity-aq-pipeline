import os
import clarityio
import pandas as pd
import sys

RAW_DATA_OUTPUT_PATH = os.getenv('OUTPUT_PATH', '/usr/app/raw.csv')

# Fetch input parameters from envvars
api_connection = clarityio.ClarityAPIConnection(
    api_key=os.getenv('CLARITY_API_KEY'),
    org=os.getenv('CLARITY_ORG_NAME', 'cityof58A9')
)


def fetch_metrics():
    request_body = {
        'allDatasources': True,
        'outputFrequency': 'hour',
        'format': 'json-long',
        'startTime': '2025-06-22T00:00:00Z'
    }
    response = api_connection.get_recent_measurements(data=request_body)
    if response is not None:
        return pd.DataFrame(response['data'])
    else:
        print('Failed to fetch metrics from Clarity API')
        sys.exit(1)


def main():
    print("Fetching metrics...")
    metrics = fetch_metrics()
    print("Metrics fetched:", metrics)

    print(f"Writing to file: {RAW_DATA_OUTPUT_PATH}")
    metrics.to_csv(RAW_DATA_OUTPUT_PATH, index=False)


if __name__ == "__main__":
    main()
