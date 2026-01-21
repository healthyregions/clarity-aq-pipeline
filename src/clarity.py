import json
import os
from io import StringIO
from time import sleep

import pandas as pd
import requests
from requests import RequestException, Response
from urllib3.exceptions import HTTPError

from config import log, CLARITY_HOSTNAME, CLARITY_ORG_NAME, CLARITY_API_KEY, CLARITY_USE_CONTINUATION_TOKEN
from config import CONTINUATION_TOKEN_OUTPUT_PATH, S3_BUCKET_NAME
from config import HISTORICAL_START_TIME, HISTORICAL_END_TIME, CLARITY_REPORT_ID
from utils import redact, from_json, to_json, write_txt

from s3 import s3_client

import os
import sys

# globals: S3_BUCKET_NAME, CLARITY_API_KEY, CLARITY_ORG_NAME, CONTINUATION_TOKEN_OUTPUT_PATH, s3_client
class ClarityAPI(object):
    def __init__(self):
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

    # Fetch existing continuation token from S3 bucket
    def read_continuation_token(self):
        token_local_path = f'{S3_BUCKET_NAME}/token.txt'
        if CLARITY_USE_CONTINUATION_TOKEN:
            if s3_client.exists(token_local_path):
                with s3_client.open(token_local_path, 'r') as f:
                     return f.read()
        return None

    # Write continuation token to local output directory
    # This will be written to S3 with the rest of the uploaded output files
    def write_continuation_token(self, token):
        # store latest continuation token locally
        if CLARITY_USE_CONTINUATION_TOKEN:
            with open(CONTINUATION_TOKEN_OUTPUT_PATH, 'w') as f:
                f.write(token)

    # Parse csv-wide Response and return CSV contents as a text blob
    def parse_results_csv_wide(self, r: Response):
        r.raise_for_status()
        return r.text

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

