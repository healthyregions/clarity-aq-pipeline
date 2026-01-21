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

from clarity import ClarityAPI

from s3 import s3_client

import os
import sys

OPERATION = os.getenv('OPERATION', 'hourly')

# globals: S3_BUCKET_NAME, CLARITY_API_KEY, CLARITY_ORG_NAME, CONTINUATION_TOKEN_OUTPUT_PATH, s3_client
class RecentMeasurements(ClarityAPI):

    def recent_post_measurements_query(self):
        body: dict = { 'org': self.orgName }

        token = self.read_continuation_token()
        if token:
            body['continuationToken'] = token
        else:
            body['allDatasources'] = True
            body['replyWithContinuationToken'] = CLARITY_USE_CONTINUATION_TOKEN
            body['outputFrequency'] = 'minute'
            body['locationRounding'] = 4
            body['format'] = 'csv-wide'

        url = self.continuationUrl if token else self.measurementsUrl
        redacted = redact(redactable=body, key_name='continuationToken')
        log.info(f'Submitting query: {redacted}')

        return requests.post(url=url, headers=self.headers, data=json.dumps(body))



    # Example: curl https://clarity-data-api.clarity.io/v2/recent-datasource-measurements-query -H 'Content-Type: application/json' -H 'x-api-key: ${CLARITY_API_KEY}' -H 'Accept: application/json' -d '{'org':'cityof58A9','allDatasources':true,'replyWithContinuationToken':true,'outputFrequency':'hour','format':'csv-wide'}' -vvvv
    # Fetch per-hour metrics from the past 24 hours in JSON format
    def recent_fetch_metrics(self):
        try:
            r = self.recent_post_measurements_query()
            return self.parse_results_csv_wide(r=r)

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







    # # Example: curl https://clarity-data-api.clarity.io/v2/recent-datasource-measurements-query -H 'Content-Type: application/json' -H 'x-api-key: ${CLARITY_API_KEY}' -H 'Accept: application/json' -d '{'org':'cityof58A9','allDatasources':true,'replyWithContinuationToken':true,'outputFrequency':'hour','format':'csv-wide'}' -vvvv
    # # Fetch per-hour metrics from the past 24 hours in JSON format
    # def fetch_sensor_data_recent(self):
    #     token = self.read_continuation_token()
    #     url = self.continuationUrl if token else self.measurementsUrl
    #     body: dict = { 'org': self.orgName }
    #
    #     if token:
    #         body['continuationToken'] = token
    #     else:
    #         body['allDatasources'] = True
    #         body['replyWithContinuationToken'] = CLARITY_USE_CONTINUATION_TOKEN
    #         body['outputFrequency'] = 'hour'
    #         body['locationRounding'] = 4
    #         body['metricSelect'] = 'only *nowcast*'
    #         body['format'] = 'json-long'
    #
    #     redacted = json.loads(json.dumps(body))
    #     if 'continuationToken' in redacted:
    #         redacted['continuationToken'] = f'{body["continuationToken"][:20]}...'
    #
    #     try:
    #         log.info(f'Submitting query: {redacted}')
    #         r = requests.post(url=url, headers=self.headers, data=json.dumps(body))
    #         log.debug(r)
    #         log.debug(r.text)
    #         response = r.json()
    #         r.raise_for_status()
    #
    #         query = response['request']
    #         log.debug(f'Submitted query: {query}')
    #         data = from_json(response['data'])
    #         log.debug(f'Fetched data: {data}')
    #         if 'locations' not in response:
    #             log.warning(f'Warning: no metric updates detected since last run - skipping pushing empty data file')
    #             sys.exit(25)
    #
    #         locations = response['locations']
    #         log.debug(f'Fetched locations: {locations}')
    #         if CLARITY_USE_CONTINUATION_TOKEN:
    #             token = r.headers['x-clarity-continuation-token']
    #             log.debug(f'New continuation token: {token}')
    #         else:
    #             token = None
    #
    #         return data, locations, token, query
    #
    #     except requests.exceptions.ConnectionError as ex:
    #         self.log_exception(ex, 'Connection Error: Could not connect to the server')
    #         sys.exit(3)
    #     except requests.exceptions.Timeout as ex:
    #         self.log_exception(ex, 'Timeout Error: The request timed out')
    #         sys.exit(4)
    #     except requests.exceptions.HTTPError as ex:
    #         self.log_exception(ex, 'HTTP Error: A bad HTTP status code was received')
    #         sys.exit(5)
    #     except requests.exceptions.RequestException as ex:
    #         self.log_exception(ex, 'An unexpected Requests error occurred')
    #         sys.exit(6)
