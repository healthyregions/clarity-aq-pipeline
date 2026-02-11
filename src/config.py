# All environment variables that control behavior of the clarity-aq-pipeline are defined here

from datetime import datetime, UTC, timedelta
import logging
import os


# Shorthand helper functon for setting or defaulting a variable value
def set_or_default(value, default):
    return value if value else default

# Configure logging based on user input
default_log_level = 'INFO'
LOGLEVEL = set_or_default(
    value=os.getenv('LOGLEVEL', default_log_level).upper(),
    default=default_log_level
)
level = logging.getLevelName(LOGLEVEL)
logging.basicConfig(level=level)
log = logging.getLogger(__name__)
log.setLevel(LOGLEVEL)

# Connection details for Clarity API
CLARITY_ORG_NAME = os.getenv('CLARITY_ORG_NAME', 'cityof58A9')
CLARITY_API_KEY = os.getenv('CLARITY_API_KEY')
CLARITY_USE_CONTINUATION_TOKEN = os.getenv('CLARITY_USE_CONTINUATION_TOKEN', 'false').lower() in ('true', '1', 't')
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

