import json
import requests
import sys

from clarity import ClarityAPI
from config import log, CLARITY_USE_CONTINUATION_TOKEN
from utils import redact


# globals: S3_BUCKET_NAME, CLARITY_API_KEY, CLARITY_ORG_NAME, CONTINUATION_TOKEN_OUTPUT_PATH, s3_client
class RecentMeasurements(ClarityAPI):

    def recent_post_measurements_query(self, start_time):
        token = self.read_continuation_token()
        body: dict = { 'org': self.orgName, 'continuationToken': token } \
            if CLARITY_USE_CONTINUATION_TOKEN and token else \
            {
                'org': self.orgName,
                'allDatasources': True,
                'replyWithContinuationToken': CLARITY_USE_CONTINUATION_TOKEN,
                'outputFrequency': 'minute',
                'locationRounding': 4,
                'format': 'csv-wide',
                'startTime': start_time if start_time else None,
                'metricSelect': self.metricSelect,
            }

        url = self.continuationUrl if token else self.measurementsUrl
        redacted = redact(redactable=body, key_name='continuationToken')
        log.info(f'Submitting query: {redacted}')

        return requests.post(url=url, headers=self.headers, data=json.dumps(body))


    # Fetch existing continuation token from S3 bucket
    def read_continuation_token(self):
        if CLARITY_USE_CONTINUATION_TOKEN:
            return self.s3api.fetch_continuation_token()
        return None


    # Write continuation token to local output directory
    # This will be written to S3 with the rest of the uploaded output files
    def write_continuation_token(self, token):
        # store latest continuation token locally
        if CLARITY_USE_CONTINUATION_TOKEN and token:
            return self.s3api.update_continuation_token(token=token)
        elif CLARITY_USE_CONTINUATION_TOKEN:
            log.warn('WARNING: no continuation_token to write, but CLARITY_USE_CONTINUATION_TOKEN. Query will not continue the next time unless the token is saved.')


    # Example: curl https://clarity-data-api.clarity.io/v2/recent-datasource-measurements-query -H 'Content-Type: application/json' -H 'x-api-key: ${CLARITY_API_KEY}' -H 'Accept: application/json' -d '{'org':'cityof58A9','allDatasources':true,'replyWithContinuationToken':true,'outputFrequency':'hour','format':'csv-wide'}' -vvvv
    # Fetch per-hour metrics from the past 24 hours in JSON format
    def recent_fetch_metrics(self, start_time):
        try:
            r = self.recent_post_measurements_query(start_time=start_time)
            recent_measurements_df = self.parse_results_csv_wide(r=r)
            log.debug('Gathering locations...')
            locations_df = self.gather_locations(measurements_df=recent_measurements_df)

            log.debug(locations_df)
            self.s3api.update_locations_df(new_locations_df=locations_df)

            token = r.headers.get('x-clarity-continuation-token', None)
            return recent_measurements_df, locations_df, token

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

