#!/bin/env python3

import datetime
import logging
import s3fs
import os

# Endpoint URL: use default for AWS  S3 or override for MinIO (e.g. http://localhost:9000)
S3_ENDPOINT_URL = os.getenv('S3_ENDPOINT_URL', None)

# if using AWS S3, you should specify a region (e.g. us-east-2)
S3_REGION = os.getenv('S3_REGION', 'us-east-2')

# Credentials for S3
S3_ACCESS_KEY = os.getenv('S3_ACCESS_KEY', None)
S3_SECRET_KEY = os.getenv('S3_SECRET_KEY', None)

# Build an S3 client using the given credentials + endpoint
s3_client = s3fs.S3FileSystem(anon=False, key=S3_ACCESS_KEY, secret=S3_SECRET_KEY, client_kwargs={
    # Use custom endpoint URL for self-hosted (e.g. MinIO)
    'endpoint_url': S3_ENDPOINT_URL
}) if S3_ENDPOINT_URL else s3fs.S3FileSystem(anon=False, key=S3_ACCESS_KEY, secret=S3_SECRET_KEY, client_kwargs={
    # For default endpoint, assume AWS S3
    # for this case, we will want to specify a region
    'region_name': S3_REGION
})

# Local directory that will be uploaded to the bucket
LOCAL_DIR_TO_UPLOAD = os.getenv('LOCAL_DIR_TO_UPLOAD', '/usr/app/data/')

# The S3 bucket where the files should be uploaded
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME', 'chicago-aq')

# Directory within the bucket
# Use UPLOAD_PATH if given, otherwise use timestamp: 2025-10-02@16:41:25
S3_UPLOAD_PATH = os.getenv('S3_UPLOAD_PATH', None)
if S3_UPLOAD_PATH is None:
    S3_UPLOAD_PATH = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d@%H:%M:%S")

# Configure logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def main():
    try:
        # Determine destination within S3
        s3_path = f"{S3_BUCKET_NAME}/{S3_UPLOAD_PATH}"

        # Upload local directory to S3
        log.info(f'Uploading to S3: {LOCAL_DIR_TO_UPLOAD}')
        s3_client.put(LOCAL_DIR_TO_UPLOAD, s3_path, recursive=True)
        log.info(f'Uploaded successfully to S3: {s3_path}')
    except Exception as ex:
        formatted = f'{LOCAL_DIR_TO_UPLOAD} -> {S3_BUCKET_NAME}/{S3_UPLOAD_PATH}'
        log.error(f'Failed to upload files to S3: {formatted} - {str(ex)}')


if __name__ == "__main__":
    main()
