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
from utils import redact, write_txt

from clarity import ClarityAPI

import os
import sys

OPERATION = os.getenv('OPERATION', 'hourly')

# globals: S3_BUCKET_NAME, CLARITY_API_KEY, CLARITY_ORG_NAME, CONTINUATION_TOKEN_OUTPUT_PATH, s3_client
class HistoricalMeasurements(ClarityAPI):

    # Fetch per-minute metrics from the previous month in JSON format
    def historical_fetch_metrics(self, start_time, end_time):
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
            body['startTime'] = start_time
            body['endTime'] = end_time
            body['qcAssessment'] = True
            body['qcFlags'] = True

        # WARNING: the `POST /report-requests` endpoint has a limit of 30 new reports per day
        # After that, HTTP 429 will be returned indicating that the user needs to wait

        # TODO: Handle passed-in parameters for report_id?
        # This might help us workaround the 30 reports / day limit during testing and development testing

        try:
            global CLARITY_REPORT_ID
            # Fetch existing report request, or request a new report for the past month as per-minute data
            report_processing = self.historical_post_report_request(body=body) if CLARITY_REPORT_ID == '' else self.historical_get_report_request_fetch(report_id=CLARITY_REPORT_ID)

            # Poll until the report request has finished
            report_processing = self.historical_get_report_request_poll(report_id=report_processing['reportId'])

            # Uncomment for testing
            # report_processing = { "reportId": "JBLLZT8NW9", "reportStatus": "in-progress", "message": "Processing" }

            return report_processing


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


    # Given a processed report and an output_path, download all report output to the path
    def download_report_contents(self, report_processed: dict, output_path: str):
        #report_metadata = {}
        urls = report_processed['urls']
        for url in urls:
            idx = urls.index(url)
            r = requests.get(url=url)
            r.raise_for_status()
            report_contents = r.text
            #report_metadata[url] = { "contents": r.text, "index": idx, "url": url }
            write_txt(output_path.format(index=idx), report_contents)

            # TODO: write as parquet file

        if not urls:
            log.fatal('Monthly data fetch was not successful')
            sys.exit(101)


    # Test Data
    #    # Parquet format
    #    % curl -XPOST 'https://clarity-data-api.clarity.io/v2/report-requests' --header 'x-api-key: ljIoByIfR52LkDReggBa4gJsgQfrNLOET9Kpoxpm' --header 'Content-Type: application/json' -d '{ "org": "cityof58A9", "report": "datasource-measurements", "allDatasources": "true", "outputFrequency": "minute", "startTime": "2026-01-18T00:00:00.000Z", "endTime": "2026-01-19T00:00:00.000Z" }'
    #    {"reportId": "JB3TAATED1", "reportStatus": "in-progress", ... , "format": "parquet-wide", "metricLabelStyle": "canonical"}}


    def historical_post_report_request(self, body: dict):
        # format: {"reportId": "JBLLZT8NW9", "reportStatus": "in-progress", "message": "Processing", "report": "datasource-measurements", "urls": [], "query": {"datasourceIds": ["DZFUM1742", "DRJLK4822", ,,, ], "endTime": "2025-11-01T00:00:00.000Z", "outputFrequency": "minute", "startTime": "2025-10-01T00:00:00.000Z", "format": "csv-wide", "metricLabelStyle": "canonical", "qcAssessment": true, "qcFlags": true}}
        redacted = redact(redactable=body, key_name='continuationToken')
        log.info(f'Submitting query: {redacted}')
        r = requests.post(url=self.historicalUrl, headers=self.headers, data=json.dumps(body))
        r.raise_for_status()
        report_processing = r.json()
        log.debug(f'Fetched metadata: {report_processing}')

        # Poll for the report that we submitted
        return report_processing


    # Returns report request metadata immediately
    # For checking status, validation, etc
    def historical_get_report_request_fetch(self, report_id):
        log.info(f'Fetching report status: {report_id}')
        # Format: "reportId": "JBLLZT8NW9", "reportStatus": "succeeded", "message": "Ready", "report": "datasource-measurements", "urls": ["https://combined-measurements-export-prd.s3.amazonaws.com/historical/JBLLZT8NW9/JBLLZT8NW9.csv.gz?..."], "query": {"datasourceIds": ["DZFUM1742", "DZQSX5311", ... ], "endTime": "2025-11-01T00:00:00.000Z", "outputFrequency": "minute", "startTime": "2025-10-01T00:00:00.000Z", "format": "csv-wide", "metricLabelStyle": "canonical", "qcAssessment": true, "qcFlags": true}, "urlsExpireAt": "2025-11-19T18:40:20.249Z"}
        log.debug(self.reportsUrl)
        r = requests.get(url=self.reportsUrl.format(report_id=report_id), headers=self.headers)
        r.raise_for_status()
        return r.json()


    # Poll until report is processed, then return final metadata
    def historical_get_report_request_poll(self, report_id, interval=10, maxRetries=50):
        # Loop until report is finished processing, then return the report
        log.info(f'Polling for report status: {report_id}')
        retries = 0
        report_processing = None
        while report_processing is None or report_processing['reportStatus'] == 'in-progress' and report_processing[
            'message'] == 'Processing':
            report_processing = self.historical_get_report_request_fetch(report_id=report_id)
            log.info(f'Poll status ({retries}/{maxRetries}): {report_processing}')

            # Wait for report to include URLs
            if 'urls' not in report_processing or len(report_processing['urls']) == 0:
                log.debug(f'Report not yet ready - waiting {interval} seconds to retry...')
                sleep(interval)
                retries += 1
                if retries > maxRetries:
                    log.error(f'ERROR: Report not yet ready after max retries={retries}')
                    raise TimeoutError(f'ERROR: Report not yet ready after max retries={retries}')
                continue
            elif report_processing['reportStatus'] == 'succeeded' and report_processing['message'] == 'Ready':
                break

        return report_processing

