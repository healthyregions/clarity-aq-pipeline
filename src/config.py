# All environment variables that control behavior of the clarity-aq-pipeline are defined here

from datetime import datetime, UTC, timedelta
import logging
import os


# Shorthand helper functon for setting or defaulting a variable value
def set_or_default(value, default):
    return value if value else default

# Configure logging based on user input
LOGLEVEL = set_or_default(
    value=os.getenv('LOGLEVEL', 'DEBUG').upper(),
    default='INFO'
)
level = logging.getLevelName(LOGLEVEL)
logging.basicConfig(level=level)
log = logging.getLogger(__name__)

# Connection details for Clarity API
CLARITY_ORG_NAME = os.getenv('CLARITY_ORG_NAME', 'cityof58A9')
CLARITY_API_KEY = os.getenv('CLARITY_API_KEY')
CLARITY_USE_CONTINUATION_TOKEN = os.getenv('CLARITY_USE_CONTINUATION_TOKEN', 'falso').lower() in ('true', '1', 't')
CLARITY_HOSTNAME = 'https://clarity-data-api.clarity.io/v2'

# For monthly report, allow user to specify a previous report
CLARITY_REPORT_ID = os.getenv('CLARITY_REPORT_ID', '')

# S3 Endpoint URL: use default for AWS S3 or override for MinIO (e.g. http://localhost:9000)
S3_ENDPOINT_URL = os.getenv('S3_ENDPOINT_URL', None)

# if using AWS S3, you should specify a region (e.g. us-east-2)
S3_REGION = os.getenv('S3_REGION', 'us-east-2')

# The S3 bucket where the files should be uploaded
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME', 'chicago-aq')

# Credentials for S3
S3_ACCESS_KEY = os.getenv('S3_ACCESS_KEY', None)
S3_SECRET_KEY = os.getenv('S3_SECRET_KEY', None)
IS_MINIO = True if S3_ENDPOINT_URL else False

# one of 'STANDARD' | 'REDUCED_REDUNDANCY' | 'STANDARD_IA' | 'ONEZONE_IA'
#   'INTELLIGENT_TIERING' | 'GLACIER' | 'DEEP_ARCHIVE' | 'OUTPOSTS' | 'GLACIER_IR'
S3_STORAGE_CLASS = os.getenv('S3_STORAGE_CLASS', 'INTELLIGENT_TIERING')

# Directory within the bucket (debug / testing only)
# Use S3_UPLOAD_PATH if given, otherwise use timestamp: 2025-10-02@16:41:25
S3_UPLOAD_PATH = set_or_default(
    value=os.getenv('S3_UPLOAD_PATH', None),
    # Python's datetime does not support military timezone suffixes like 'Z' suffix for UTC
    # see https://stackoverflow.com/a/42777551
    # The following simple string replacement does the trick:
    default=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    #
    # Javascript can parse ISO format as a date string and localize to a timezone:
    #
    #   // Convert ISO to user's current locale
    #   // NOTE: Z means we are assuming the input date is in UTC
    #   timestamp = new Date('2025-11-03T22:00:00Z')
    #    >  Mon Nov 03 2025 16:00:00 GMT-0600 (Central Standard Time)
    #
    #   // Convert/display this date in en-US locale for the America/Chicago time zone
    #   timestamp.toLocaleString('en-US', { timeZone: 'America/Chicago' })
    #    >  '11/3/2025, 4:00:00 PM'
)

# Compute today's timestamp, use that to find first microsecond of the current month
now = datetime.now(UTC)
first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
# subtract 1 microsecond to get the end_date from the previous month
last_of_prev_month = first_of_this_month - timedelta(microseconds=1)
# repeat this process to get first microsecond of previous month to get the start_date
first_of_prev_month = last_of_prev_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

# Allow user to override start_date + end_date
# This allows us to run the script a few times with different envvars to get a full history

# A date in ISO 8601 format, e.g. "2023-01-02T03:45:67.899Z".
# Measurements returned are on or after this time.
HISTORICAL_START_TIME = set_or_default(
    value=os.getenv('HISTORICAL_START_TIME', None),
    default=first_of_prev_month.strftime('%Y-%m-%dT%H:%M:%S.000Z')
)
# A date in ISO 8601 format, e.g. "2023-01-02T03:45:67.899Z".
# Measurements returned are before this time.
HISTORICAL_END_TIME = set_or_default(
    value=os.getenv('HISTORICAL_END_TIME', None),
    default=first_of_this_month.strftime('%Y-%m-%dT%H:%M:%S.000Z')
)

# Local directory that will be uploaded to the bucket
LOCAL_OUTPUT_DIR = os.getenv('LOCAL_OUTPUT_DIR', '/home/rstudio/data')
RAW_DATA_OUTPUT_PATH = os.getenv('RAW_DATA_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/raw.csv')
CLEANED_DATA_OUTPUT_PATH = os.getenv('CLEANED_DATA_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/cleaned.json')
HISTORICAL_DATA_OUTPUT_PATH = os.getenv('HISTORICAL_DATA_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/historical.geojson')
LATEST_DATA_OUTPUT_PATH = os.getenv('LATEST_DATA_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/latest.geojson')
MONTHLY_DATA_OUTPUT_PATH = os.getenv('MONTHLY_DATA_OUTPUT_PATH', LOCAL_OUTPUT_DIR + '/monthly-raw-{index}.csv')
INDEX_OUTPUT_PATH = os.getenv('INDEX_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/index.json')

CONTINUATION_TOKEN_OUTPUT_PATH = os.getenv('CONTINUATION_TOKEN_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/token.txt')
QUERY_OUTPUT_PATH = os.getenv('QUERY_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/query.json')

OUTPUT_LOCATIONS_AS_JSON = os.getenv('OUTPUT_LOCATIONS_AS_JSON', 'False').lower() in ('true', '1', 't')
LOCATIONS_OUTPUT_PATH = os.getenv('LOCATIONS_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/locations.json')
