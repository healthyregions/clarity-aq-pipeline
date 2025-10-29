import json
import os
import sys
from datetime import datetime

from config import log, IS_MINIO, S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY, LOCAL_OUTPUT_DIR
from config import INDEX_OUTPUT_PATH, S3_UPLOAD_PATH, CLEANED_DATA_OUTPUT_PATH
from config import S3_REGION, S3_STORAGE_CLASS, S3_BUCKET_NAME

import s3fs


# Build an S3 client using the given credentials + endpoint
s3_client = s3fs.S3FileSystem(anon=False, key=S3_ACCESS_KEY, secret=S3_SECRET_KEY, client_kwargs={
    # Use custom endpoint URL for self-hosted (e.g. MinIO)
    'endpoint_url': S3_ENDPOINT_URL,
    # NOTE: StorageClass & Region are ignored when using MinIO
}) if IS_MINIO else s3fs.S3FileSystem(anon=False, key=S3_ACCESS_KEY, secret=S3_SECRET_KEY, s3_additional_kwargs={
    # provide a StorageClass to use for uploads (default: INTELLIGENT_TIERING)
    'StorageClass': S3_STORAGE_CLASS
}, client_kwargs={
    # For default endpoint, assume AWS S3
    # for this case, we will want to specify a Region & StorageClass
    'region_name': S3_REGION,
})


# globals: S3_ENDPOINT_URL, IS_MINIO, S3_REGION, s3_client
class S3API(object):
    def list_folders(self):
        return s3_client.ls(path=f'{S3_BUCKET_NAME}/')

    def read_file(self, path):
        # WARNING: filename collisions would be bad here
        if S3_UPLOAD_PATH in path:
            # this is the currently-queued upload - file only exists locally
            with open(CLEANED_DATA_OUTPUT_PATH, 'r') as f:
                json_contents = json.load(f)
                return json_contents, os.stat(CLEANED_DATA_OUTPUT_PATH).st_size
        else:
            # file does not exist locally - read from S3 bucket
            with s3_client.open(path, 'r') as f:
                json_contents = json.load(f)
                return json_contents, s3_client.stat(path)['size']

    def find_earliest_latest_timestamps(self, content):
        # {"datasourceId":"DJHFB1439","time":"2025-10-23T20:00:00.000Z","metric":"no2Conc1HourMean","raw":-0.08,"value":7.73,"status":"calibrated-ready"},
        timestamps = [datetime.fromisoformat(val['time']) for val in content]
        return min(timestamps), max(timestamps)

    def generate_index_file(self):
        # Grab top-level objects (folder, etc) from S3
        # Also, append the folder that we are currently uploading :)
        tlos = self.list_folders() + [f'{S3_BUCKET_NAME}/{S3_UPLOAD_PATH}']

        metadata = []
        for tlo in tlos:
            if 'index.json' in tlo or 'token.txt' in tlo or 'locations.json' in tlo:
                print (f'Skipping {tlo}...')
                continue
            print (f'Analyzing {tlo}...')
            content, size = self.read_file(tlo)
            redacted = f'{json.loads(json.dumps(content))[:40]}...'
            print(f'content = {redacted}')
            print(f'size = {size}')
            earliest, latest = self.find_earliest_latest_timestamps(content=content)
            print(f'earliest = {earliest}')
            print(f'latest = {latest}')
            metadata.append({
                'path': tlo,
                'size': size,
                'startTime': earliest.isoformat(),
                'endTime': latest.isoformat(),
            })

        with open(INDEX_OUTPUT_PATH, 'w') as f:
            json.dump(metadata, f)

    def push_to_s3(self, local_path: str, remote_path: str):
        # Determine destination within S3
        s3_label = 'MinIO' if IS_MINIO else 'AWS S3'
        s3_extra = S3_ENDPOINT_URL if IS_MINIO else S3_REGION

        try:
            # Notify user that upload is about to start
            log.debug(f'Uploading to {s3_label} ({s3_extra}): {local_path}')

            # Upload the local file to S3
            s3_client.put(local_path, remote_path)

            # Notify user that upload is about has completed
            log.info(f'Uploaded successfully to {s3_label} ({s3_extra}): {local_path} -> {remote_path}')
        except Exception as ex:
            formatted = f'{local_path} -> {remote_path}'
            log.error(f'Failed to upload file to {s3_label}: {formatted} - {str(ex)}')
            sys.exit(100)
