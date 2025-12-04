import json
import os
from io import StringIO
from time import sleep

import pandas as pd
import requests

from config import log, CLARITY_HOSTNAME, CLARITY_ORG_NAME, CLARITY_API_KEY, CLARITY_USE_CONTINUATION_TOKEN
from config import CONTINUATION_TOKEN_OUTPUT_PATH, S3_BUCKET_NAME
from config import MONTHLY_START_TIME, MONTHLY_END_TIME, CLARITY_REPORT_ID
from utils import from_json, to_json, write_txt

from s3 import s3_client

import os
import sys

OPERATION = os.getenv('OPERATION', 'hourly')

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



    # Fetch per-minute metrics from the previous month in JSON format
    def fetch_sensor_data_monthly(self):
        token = self.read_continuation_token()
        body: dict = { 'org': self.orgName }

        if token:
            body['continuationToken'] = token
        else:
            body['allDatasources'] = True
            body['replyWithContinuationToken'] = CLARITY_USE_CONTINUATION_TOKEN
            body['outputFrequency'] = 'minute'
            body['locationRounding'] = 4
            body['report'] = 'datasource-measurements'
            body['format'] = 'csv-wide'
            body['startTime'] = MONTHLY_START_TIME
            body['endTime'] = MONTHLY_END_TIME
            body['qcAssessment'] = True
            body['qcFlags'] = True

        redacted = json.loads(json.dumps(body))
        if 'continuationToken' in redacted:
            redacted['continuationToken'] = f'{body["continuationToken"][:20]}...'

        # WARNING: the `POST /report-requests` endpoint has a limit of 30 new reports per day
        # After that, HTTP 429 will be returned indicating that the user needs to wait

        # TODO: Handle passed-in parameters for report_id?
        # This might help us workaround the 30 reports / day limit during testing and development testing

        try:
            global CLARITY_REPORT_ID
            if CLARITY_REPORT_ID == '':
                # Request a new report for the past month as per-minute data
                log.info(f'Submitting query: {redacted}')
                r = requests.post(url=self.historicalUrl, headers=self.headers, data=json.dumps(body))
                r.raise_for_status()
                # format: {"reportId": "JBLLZT8NW9", "reportStatus": "in-progress", "message": "Processing", "report": "datasource-measurements", "urls": [], "query": {"datasourceIds": ["DZFUM1742", "DRJLK4822", ,,, ], "endTime": "2025-11-01T00:00:00.000Z", "outputFrequency": "minute", "startTime": "2025-10-01T00:00:00.000Z", "format": "csv-wide", "metricLabelStyle": "canonical", "qcAssessment": true, "qcFlags": true}}
                report_processing = r.json()
                log.debug(report_processing)

                # Uncomment for testing
                #report_processing = { "reportId": "JBLLZT8NW9", "reportStatus": "in-progress", "message": "Processing" }

                # Poll for the report that we submitted
                CLARITY_REPORT_ID = report_processing['reportId']
            else:
                report_processing = None

            while report_processing is None or report_processing['reportStatus'] == 'in-progress' and report_processing['message'] == 'Processing':
                # Format: "reportId": "JBLLZT8NW9", "reportStatus": "succeeded", "message": "Ready", "report": "datasource-measurements", "urls": ["https://combined-measurements-export-prd.s3.amazonaws.com/historical/JBLLZT8NW9/JBLLZT8NW9.csv.gz?..."], "query": {"datasourceIds": ["DZFUM1742", "DZQSX5311", ... ], "endTime": "2025-11-01T00:00:00.000Z", "outputFrequency": "minute", "startTime": "2025-10-01T00:00:00.000Z", "format": "csv-wide", "metricLabelStyle": "canonical", "qcAssessment": true, "qcFlags": true}, "urlsExpireAt": "2025-11-19T18:40:20.249Z"}
                log.debug(self.reportsUrl)
                r = requests.get(url=self.reportsUrl.format(report_id=CLARITY_REPORT_ID), headers=self.headers)
                r.raise_for_status()
                report_processing = r.json()
                log.info(f'Poll status: {report_processing}')

                if 'urls' not in report_processing:
                    log.debug('Report not yet ready - waiting 5 seconds to poll again...')
                    sleep(5)
                    continue
                elif report_processing['reportStatus'] == 'succeeded' and report_processing['message'] == 'Ready':
                    break

            return report_processing['urls']

        except requests.exceptions.ConnectionError as ex:
            self.log_exception(ex, 'Connection Error: Could not connect to the server')
            sys.exit(3)
        except requests.exceptions.Timeout as ex:
            self.log_exception(ex, 'Timeout Error: The request timed out')
            sys.exit(4)
        except requests.exceptions.HTTPError as ex:
            self.log_exception(ex, 'HTTP Error: A bad HTTP status code was received')
            sys.exit(5)
        except requests.exceptions.RequestException as ex:
            self.log_exception(ex, 'An unexpected Requests error occurred')
            sys.exit(6)

    def log_exception(self, ex: Exception, message: str):
        log.error(f'{message}. Details: {ex}')
        try:
            log.error(ex.response.text)
        except Exception as ex:
            log.fatal('Encountered a failure while logging response error. Shutting down....')
            sys.exit(99)


    # Example: curl https://clarity-data-api.clarity.io/v2/recent-datasource-measurements-query -H 'Content-Type: application/json' -H 'x-api-key: ${CLARITY_API_KEY}' -H 'Accept: application/json' -d '{'org':'cityof58A9','allDatasources':true,'replyWithContinuationToken':true,'outputFrequency':'hour','format':'csv-wide'}' -vvvv
    # Fetch per-hour metrics from the past 24 hours in JSON format
    def fetch_sensor_data(self):
        token = self.read_continuation_token()
        url = self.continuationUrl if token else self.measurementsUrl
        body: dict = { 'org': self.orgName }

        if token:
            body['continuationToken'] = token
        else:
            body['allDatasources'] = True
            body['replyWithContinuationToken'] = CLARITY_USE_CONTINUATION_TOKEN
            body['outputFrequency'] = 'hour'
            body['locationRounding'] = 4
            body['metricSelect'] = 'only *nowcast*'
            body['format'] = 'json-long'

        redacted = json.loads(json.dumps(body))
        if 'continuationToken' in redacted:
            redacted['continuationToken'] = f'{body["continuationToken"][:20]}...'

        try:
            log.info(f'Submitting query: {redacted}')
            r = requests.post(url=url, headers=self.headers, data=json.dumps(body))
            log.debug(r)
            log.debug(r.text)
            response = r.json()
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
            if CLARITY_USE_CONTINUATION_TOKEN:
                token = r.headers['x-clarity-continuation-token']
                log.debug(f'New continuation token: {token}')
            else:
                token = None

            return data, locations, token, query

        except requests.exceptions.ConnectionError as ex:
            self.log_exception(ex, 'Connection Error: Could not connect to the server')
            sys.exit(3)
        except requests.exceptions.Timeout as ex:
            self.log_exception(ex, 'Timeout Error: The request timed out')
            sys.exit(4)
        except requests.exceptions.HTTPError as ex:
            self.log_exception(ex, 'HTTP Error: A bad HTTP status code was received')
            sys.exit(5)
        except requests.exceptions.RequestException as ex:
            self.log_exception(ex, 'An unexpected Requests error occurred')
            sys.exit(6)
