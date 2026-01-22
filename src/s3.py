import json
import os
import sys
from datetime import datetime, UTC, timedelta
from time import sleep

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
#from pyarrow.fs import S3FileSystem
import s3fs

import traceback

from config import log, IS_MINIO, S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY
from config import INDEX_OUTPUT_PATH, S3_UPLOAD_PATH
from config import S3_REGION, S3_STORAGE_CLASS, S3_BUCKET_NAME
from config import LATEST_DATA_OUTPUT_PATH, HISTORICAL_DATA_OUTPUT_PATH




# globals: S3_ENDPOINT_URL, IS_MINIO, S3_REGION, s3_client
class S3API(object):
    def __init__(self):
        # Build an S3 client using the given credentials + endpoint
        self.client = s3fs.S3FileSystem(
            anon=False,
            key=S3_ACCESS_KEY,
            secret=S3_SECRET_KEY,
            client_kwargs={
                # Use custom endpoint URL for self-hosted (e.g. MinIO)
                'endpoint_url': S3_ENDPOINT_URL,
                # NOTE: StorageClass & Region are ignored when using MinIO
            }
        ) if IS_MINIO else s3fs.S3FileSystem(
            anon=False,
            key=S3_ACCESS_KEY,
            secret=S3_SECRET_KEY,
            s3_additional_kwargs={
                # provide a StorageClass to use for uploads (default: INTELLIGENT_TIERING)
                'StorageClass': S3_STORAGE_CLASS
            }, client_kwargs={
                # For default endpoint, assume AWS S3
                # for this case, we will want to specify a Region & StorageClass
                'region_name': S3_REGION
            }
        )


    # Acquire a lock for writing to the dataset
    # Use this to prevent multiple processes from writing to the dataset simultaneously
    # And don't forget to call unlock when you're done! :)
    def lock(self, wait=False):
        # Check if lockfile exists
        log.debug('Acquiring lockfile')
        lockfile_path = f'{S3_BUCKET_NAME}/.lock'
        lockfile = self.read_file(path=lockfile_path, format='raw')
        if lockfile and not wait:
            log.error('Failed to acquire lock - lockfile already exists')
            return False

        # Create an empty lockfile to claim exclusive data write access
        # This prevents collisions from 2 processes editing the data t the same time
        with self.client.open(lockfile_path, "wb") as f:
            f.write(b'')
        log.debug('Acquired lockfile!')

        return True


    # Release the lock for writing to the dataset
    # You MUST call this after acquiring the lock
    # If this is not called, other processes will be blocked from writing to the dataset
    def unlock(self):
        # Check if lockfile exists
        log.debug('Releasing lockfile')
        lockfile_path = f'{S3_BUCKET_NAME}/.lock'
        lockfile = self.read_file(path=lockfile_path, format='raw')
        if not lockfile:
            log.warn('Failed to release lock - no lockfile found')
            return True    # technically a noop, but log a warning

        # Delete existing lockfile to release our lock
        # This will allow others to claim the lock and merge in their data
        self.client.rm(lockfile_path)
        log.debug('Released lockfile!')
        return True


    # Given a metric name and a dataframe of new sensor data for that metric,
    #   - Acquire the lock to write to the data
    #   - Fetch our existing parquet dataset, create a new one if it doesn't exist
    #   - Merge existing data into the parquet dataset
    #   - Save merged dataset to S3
    #   - Release the lock to end data writing
    def update_current_dataset(self, sensor_ids, new_metric_df, metric_name = 'mean_pm25'):
        # Acquire lockfile
        while not self.lock():
            log.warn('Waiting to acquire lockfile - retrying in 10s')
            sleep(10)

        try:
            # Get current dataset, or create a new one if it doesn't exist
            existing_dataset_path = f'{S3_BUCKET_NAME}/current/{metric_name}.parquet'
            #existing_dataset = self.read_file(path=existing_dataset_path, format='raw')
            existing_dataset = None
            if self.client.exists(existing_dataset_path):
                with self.client.open(existing_dataset_path, 'rb') as f:
                    log.debug(f'Reading existing parquet file from S3: {existing_dataset_path}')
                    existing_dataset = pd.read_parquet(f, engine='pyarrow')
                    log.debug(f'Successfully read parquet file from S3: {existing_dataset_path}')

            if existing_dataset is None:
                log.warn(f'No current dataset found - creating a new / empty dataset')
                existing_dataset = pd.DataFrame(columns=['type','date'], index=['type','date'])

            # Merge new sensor readings into existing dataset, write to S3
            merged_metric_df = existing_dataset.fillna(new_metric_df)    # pd.concat([new_metric_df, existing_dataset]).drop_duplicates(subset=['type','date'])
            print(merged_metric_df)

            # Convert to a PyArrow Table (this preserves the schema)
            table = pa.Table.from_pandas(merged_metric_df)
            with self.client.open(existing_dataset_path, "wb") as f:
                log.debug(f'Writing parquet file to S3: {existing_dataset_path}')
                pq.write_table(table, f)
                log.debug(f'Successfully updated parquet file in S3: {existing_dataset_path}')

            return merged_metric_df
        except Exception as e:
            log.error(f'Failed to update Parquet dataset in S3: {e}')
            log.error(traceback.format_exc())
        finally:
            # Release lockfile
            self.unlock()


    # TODO: Yearly process to store all parquet dataset in a folder
    def archive_current_dataset(self):
        # Acquire lockfile
        while not self.lock():
            log.warn('Waiting to acquire lockfile - retrying in 10s')
            sleep(10)

        try:
            # Detect current year using
            # Compute today's timestamp, use that to find first microsecond of the current month
            now = datetime.now(UTC)
            last_year = now.date.today().year - 1
            self.client.mv(f'{S3_BUCKET_NAME}/current/*.parquet', f'{S3_BUCKET_NAME}/{last_year}/*.parquet')
        except Exception as e:
            log.error(f'Failed to archive Parquet dataset in S3: {e}')
            log.error(traceback.format_exc())
        finally:
            # Release lockfile
            self.unlock()


    def list_folders(self):
        return self.client.ls(path=f'{S3_BUCKET_NAME}/')

    def read_file(self, path, format='json'):
        if not self.client.exists(path):
            return None

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
        elif format == 'json':
            # JSON file does not exist locally - read from S3 bucket as dict
            with self.client.open(path, 'r') as f:
                json_contents = json.load(f)
                return json_contents, self.client.stat(path)['size']
        else:
            # file does not exist locally - read raw contents from S3 bucket
            with self.client.open(path, 'r') as f:
                return f.read(), self.client.stat(path)['size']

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
            self.client.put(local_path, remote_path)

            # Notify user that upload is about has completed
            log.info(f'Uploaded successfully to {s3_label} ({s3_extra}): {local_path} -> {remote_path}')
        except Exception as ex:
            formatted = f'{local_path} -> {remote_path}'
            log.error(f'Failed to upload file to {s3_label}: {formatted} - {str(ex)}')
            sys.exit(100)
