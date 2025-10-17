import json
import requests

from config import log, CLARITY_HOSTNAME, CLARITY_ORG_NAME, CLARITY_API_KEY, CLARITY_USE_CONTINUATION_TOKEN
from config import CONTINUATION_TOKEN_OUTPUT_PATH, S3_BUCKET_NAME
from utils import from_json

from s3 import s3_client

import sys

# globals: S3_BUCKET_NAME, CLARITY_API_KEY, CLARITY_ORG_NAME, CONTINUATION_TOKEN_OUTPUT_PATH, s3_client
class ClarityAPI(object):
    def __init__(self):
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
            body['replyWithContinuationToken'] = CLARITY_USE_CONTINUATION_TOKEN
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
            if CLARITY_USE_CONTINUATION_TOKEN:
                token = r.headers['x-clarity-continuation-token']
                log.debug(f'New continuation token: {token}')
            else:
                token = None

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
