import os
import clarityio
import pandas as pd

# Fetch input parameters from envvars
api_connection = clarityio.ClarityAPIConnection(
    api_key=os.getenv('CLARITY_API_KEY'),
    org=os.getenv('CLARITY_ORG_NAME', 'cityof58A9')
)


def list_datasources():
    datasources_response = api_connection.get_datasources()
    return pd.json_normalize(datasources_response['datasources'])


def get_datasource_details(datasource_id):
    source_details_response = api_connection.get_datasource_details(datasource_id)
    source_details = pd.json_normalize(source_details_response['datasource'])


def convert_to_epa_aqi():
    clarityio.scale_raw_to_aqi('pm2.5_24hr', 18.84)  # 69.14676806083651
    clarityio.scale_raw_to_aqi('nitrogen_dioxide_1hr', 300)  # 138.64864864864865


def fetch_metrics():
    request_body = {
        'allDatasources': True,
        'outputFrequency': 'hour',
        'format': 'json-long',
        'startTime': '2025-06-22T00:00:00Z'
    }
    response = api_connection.get_recent_measurements(data=request_body)
    return pd.DataFrame(response['data'])


def main():
    print("Fetching metrics...")
    df = fetch_metrics()
    print("Results:", df)


if __name__ == "__main__":
    main()