import json
import os
import sys
from datetime import datetime

from config import log, IS_MINIO, S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY, LOCAL_OUTPUT_DIR
from config import INDEX_OUTPUT_PATH, S3_UPLOAD_PATH, CLEANED_DATA_OUTPUT_PATH
from config import S3_REGION, S3_STORAGE_CLASS, S3_BUCKET_NAME

from config import LATEST_DATA_OUTPUT_PATH, HISTORICAL_DATA_OUTPUT_PATH

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
        if 'latest.geojson' in path:
            # historical.geojson a currently-queued upload - file only exists locally
            with open(LATEST_DATA_OUTPUT_PATH, 'r') as f:
                json_contents = json.load(f)
                return json_contents, os.stat(LATEST_DATA_OUTPUT_PATH).st_size
        elif S3_UPLOAD_PATH in path or 'historicalHourly24h.geojson' in path:
            # historical.geojson a currently-queued upload - file only exists locally
            with open(HISTORICAL_DATA_OUTPUT_PATH, 'r') as f:
                json_contents = json.load(f)
                return json_contents, os.stat(HISTORICAL_DATA_OUTPUT_PATH).st_size
        else:
            # file does not exist locally - read from S3 bucket
            with s3_client.open(path, 'r') as f:
                json_contents = json.load(f)
                return json_contents, s3_client.stat(path)['size']

    # Given a list of ISO/UTC timestamps, return earliest/latest dates
    def find_earliest_latest_timestamps_from_list(self, timestamps):
        # ["2025-10-23T20:00:00.000Z","2025-10-23T21:00:00.000Z","2025-10-23T22:00:00.000Z"]
        timestamps = [datetime.fromisoformat(t) for t in timestamps]
        return min(timestamps), max(timestamps)

    # Given a data response from Clarity (array of metrics a la CSV), return earliest/latest dates
    def find_earliest_latest_timestamps(self, content):
        # {"datasourceId":"DJHFB1439","time":"2025-10-23T20:00:00.000Z","metric":"no2Conc1HourMean","raw":-0.08,"value":7.73,"status":"calibrated-ready"},
        timestamps = [f['time'] for f in content]
        return self.find_earliest_latest_timestamps_from_list(timestamps)

    # Given our "simple" GeoJSON format, return earliest/latest dates
    def find_earliest_latest_timestamps_geojson_simple(self, content):
        timestamps = [f['properties']['time'] for f in content['features']]
        return self.find_earliest_latest_timestamps_from_list(timestamps)

    # Given our "historical" GeoJSON format, return earliest/latest dates
    def find_earliest_latest_timestamps_geojson_historical(self, content):
        timestamps = content['features'][0]['properties']['pm2_5ConcMassNowcast'].keys()
        return self.find_earliest_latest_timestamps_from_list(timestamps)

    def generate_index_file(self):
        # Grab top-level objects (folder, etc) from S3
        # Also, append the folder that we are currently uploading :)
        tlos = self.list_folders() + [f'{S3_BUCKET_NAME}/{S3_UPLOAD_PATH}.geojson']

        log.info(f'Generating {INDEX_OUTPUT_PATH}...')
        metadata = []
        latest_timestamp = None
        for tlo in tlos:
            if 'index.json' in tlo or 'token.txt' in tlo or 'locations.json' in tlo:
                log.info (f'Skipping {tlo}...')
                continue
            log.info(f'Analyzing {tlo}...')
            if tlo.endswith('.json'):
                log.info (f'Parsing {tlo} as JSON...')
                content, size = self.read_file(tlo)
                earliest, latest = self.find_earliest_latest_timestamps(content=content)
            elif tlo.endswith('.geojson'):
                log.info (f'Parsing {tlo} as GeoJSON...')
                content, size = self.read_file(tlo)
                if 'time' in content['features'][0]['properties']:
                    earliest, latest = self.find_earliest_latest_timestamps_geojson_simple(content=content)
                else:
                    earliest, latest = self.find_earliest_latest_timestamps_geojson_historical(content=content)
            else:
                log.info(f'Unknown file/type: Skipping {tlo}...')
                continue

            latest_timestamp = latest if latest_timestamp is None or latest > latest_timestamp else latest_timestamp
            metadata.append({
                'path': tlo,
                'size': size,
                'startTime': earliest.isoformat(),
                'endTime': latest.isoformat(),
            })
        with open(INDEX_OUTPUT_PATH, 'w') as f:
            json.dump(metadata, f, indent=4)

        return latest_timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')



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
