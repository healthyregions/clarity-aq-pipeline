import datetime
import logging
import os

from utils import set_or_default

# Configure logging based on user input
LOGLEVEL = set_or_default(
    value=os.getenv('LOGLEVEL', 'INFO').upper(),
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
    default=datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
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


# Local directory that will be uploaded to the bucket
LOCAL_OUTPUT_DIR = os.getenv('LOCAL_OUTPUT_DIR', '/usr/app/data')
RAW_DATA_OUTPUT_PATH = os.getenv('RAW_DATA_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/raw.csv')
CLEANED_DATA_OUTPUT_PATH = os.getenv('CLEANED_DATA_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/cleaned.json')
HISTORICAL_DATA_OUTPUT_PATH = os.getenv('HISTORICAL_DATA_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/historical.geojson')
LATEST_DATA_OUTPUT_PATH = os.getenv('LATEST_DATA_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/latest.geojson')
INDEX_OUTPUT_PATH = os.getenv('INDEX_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/index.json')

CONTINUATION_TOKEN_OUTPUT_PATH = os.getenv('CONTINUATION_TOKEN_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/token.txt')
QUERY_OUTPUT_PATH = os.getenv('QUERY_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/query.json')

OUTPUT_LOCATIONS_AS_JSON = os.getenv('OUTPUT_LOCATIONS_AS_JSON', 'False').lower() in ('true', '1', 't')
LOCATIONS_OUTPUT_PATH = os.getenv('LOCATIONS_OUTPUT_PATH', f'{LOCAL_OUTPUT_DIR}/locations.json')
