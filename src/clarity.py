from io import StringIO

import pandas as pd
from requests import RequestException, Response
import sys

from config import log, CLARITY_HOSTNAME, CLARITY_ORG_NAME, CLARITY_API_KEY, CLARITY_USE_CONTINUATION_TOKEN
from s3 import S3API
from utils import from_json


# globals: S3_BUCKET_NAME, CLARITY_API_KEY, CLARITY_ORG_NAME, CONTINUATION_TOKEN_OUTPUT_PATH, s3_client
class ClarityAPI(object):
    def __init__(self, s3api: S3API):
        self.s3api = s3api

        # Endpoint URLs / default headers for Clarity's API
        self.orgName = CLARITY_ORG_NAME
        self.measurementsUrl = f'{CLARITY_HOSTNAME}/recent-datasource-measurements-query'
        self.continuationUrl = f'{CLARITY_HOSTNAME}/recent-datasource-measurements-continuation'
        self.historicalUrl = f'{CLARITY_HOSTNAME}/report-requests'
        self.reportsUrl = CLARITY_HOSTNAME + '/report-request/{report_id}'  # /{report_id} must be appended
        self.headers = {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'Accept-Encoding': 'gzip',
          'x-api-key': CLARITY_API_KEY
        }


    def log_exception(self, ex: RequestException, message: str):
        log.error(f'{message}. Details: {ex}')
        try:
            log.error(ex.response.text)
        except Exception as ex2:
            log.fatal(f'FATAL: {str(ex2)}')
            log.fatal('Encountered a failure while logging response error. Shutting down....')
            sys.exit(99)


    def gather_locations(self, measurements_df):
        location_df_columns = ['datasourceId', 'sourceId', 'locationLatitude', 'locationLongitude']
        return measurements_df[location_df_columns] \
                .drop_duplicates(subset=['datasourceId', 'sourceId']) \
                .round(decimals=4)



    # Parse csv-wide Response and return CSV contents as a pandas Dataframe
    def parse_results_csv_wide(self, r: Response):
        r.raise_for_status()
        data_buffer = StringIO(r.text)
        return pd.read_csv(data_buffer)


    # DEPRECATED: Not currently used, but helpful as a reference
    # Parse json-long Response and return a 4-tuple
    #    data => sensor data for each requested datasource
    #    locations => a separate list of the lat/long coordinates for each related datasource
    #    query => the initial request submitted for this report
    #    token => continuation token, if requested, otherwise returns None
    def parse_results_json_long(self, r: Response):
        r.raise_for_status()
        response = r.json()

        query = response['request']
        log.debug(f'Submitted query: {query}')
        data = from_json(response['data'])
        log.debug(f'Fetched data: {data}')
        if 'locations' not in response:
            log.warning(f'Warning: no metric updates detected since last run - skipping pushing empty data file')
            sys.exit(25)

        locations = response['locations']
        log.debug(f'Fetched locations: {locations}')
        if CLARITY_USE_CONTINUATION_TOKEN:
            token = r.headers['x-clarity-continuation-token']
            log.debug(f'New continuation token: {token}')
        else:
            token = None

        return data, locations, token, query

