import json
from datetime import datetime, UTC
from time import sleep

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import s3fs

import traceback

from config import log, IS_MINIO, S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY
from config import S3_REGION, S3_STORAGE_CLASS, S3_BUCKET_NAME

import duckdb

from utils import merge_new_data

# Connect to an in-memory DuckDB database
con = duckdb.connect(database=':memory:')



# globals: S3_ENDPOINT_URL, IS_MINIO, S3_REGION, s3_client
class S3API(object):
    def __init__(self):
        # Build an S3 client using the given credentials + endpoint
        self.valid_file_formats = ["json", "parquet", "raw"]
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


    # Fetch existing continuation token from S3 bucket
    def fetch_continuation_token(self):
        return self.read_file(path=f'{S3_BUCKET_NAME}/token.txt', file_format='raw')


    # Overwrite continuation token in S3
    def update_continuation_token(self, token):
        self.write_file(path=f'{S3_BUCKET_NAME}/token.txt', contents=token, file_format='raw', overwrite=True)


    # Acquire a lock for writing to the dataset
    # Use this to prevent multiple processes from writing to the dataset simultaneously
    # And don't forget to call unlock when you're done! :)
    def lock(self, wait=False):
        # Check if lockfile exists
        log.debug('Acquiring lockfile...')
        lockfile_path = f'{S3_BUCKET_NAME}/.lock'
        lockfile = self.read_file(path=lockfile_path, file_format='raw')
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
        log.debug('Releasing lockfile...')
        lockfile_path = f'{S3_BUCKET_NAME}/.lock'
        lockfile = self.read_file(path=lockfile_path, file_format='raw')
        if not lockfile:
            log.warn('Failed to release lock - no lockfile found')
            return True    # technically a noop, but log a warning

        # Delete existing lockfile to release our lock
        # This will allow others to claim the lock and merge in their data
        self.client.rm(lockfile_path)
        log.debug('Released lockfile!')
        return True


    # Given a metric name and a dataframe of new sensor data for that metric,
    #   - Acquire the lock to claim exclusive write access to the data
    #   - Merge with our existing parquet dataset, create a new one if it doesn't exist
    #   - Overwrite this file in S3
    #   - Release the lock to end data writing
    def update_measurements_df(self, new_measurements_df, metric_name):
        # Merge latest data into existing dataframe, write as parquet file
        log.info(f'Merging with existing {metric_name}.parquet file...')

        # Define custom categories for the "type" column, maintain this order
        custom_order = ['year', 'season', 'month', 'week', 'day', 'hour']  # order by least to most rows
        new_measurements_df['type'] = pd.Categorical(new_measurements_df['type'], categories=custom_order, ordered=True)
        new_measurements_df['date'] = new_measurements_df['date'].astype('str')

        new_measurements_df.info()
        print(new_measurements_df)

        # Always acquire lockfile to ensure no one is writing
        while not self.lock():
            log.warn('Waiting to acquire lockfile - retrying in 10s')
            sleep(10)

        try:
            # Merge with current dataset, or create a new one if it doesn't exist
            df_path = f'{S3_BUCKET_NAME}/current/{metric_name}.parquet'
            existing_df = self.read_file(path=df_path, file_format='parquet', binary=True)
            if existing_df is not None:
                log.info(f'Updating existing dataset with current metrics: {df_path} ')
                merged_df = merge_new_data(existing_df=existing_df, data_to_merge=new_measurements_df)
            else:
                log.warn(f'No current dataset found - creating a new dataset from current metrics: {df_path} ')
                merged_df = merge_new_data(existing_df=pd.DataFrame(), data_to_merge=new_measurements_df)

            self.write_file(path=df_path, contents=merged_df, file_format='parquet', binary=True, overwrite=True)
            log.info(f'Successfully updated {metric_name} dataset!')
            print(merged_df)

        finally:
            # Release lockfile when done writing
            self.unlock()


    # Merged lat/lon coordinates into our list of known sensorIds
    #   - Acquire the lock to claim exclusive write access to the data
    #   - Merge with our existing parquet dataset, create a new one if it doesn't exist
    #   - Overwrite this file in S3
    #   - Release the lock to end data writing
    def update_locations_df(self, new_locations_df):
        # Always acquire lockfile to ensure no one is writing
        while not self.lock():
            log.warn('Waiting to acquire lockfile - retrying in 10s')
            sleep(10)

        # Merge with current dataset, or create a new one if it doesn't exist
        columns = ['datasourceId','sourceId']
        locations_df_path = f'{S3_BUCKET_NAME}/current/locations.parquet'
        log.info(f'Merging new location details into {locations_df_path}...')

        try:
            existing_df = self.read_dataset(df_path=locations_df_path, columns=columns)
            merged_df = new_locations_df.combine_first(existing_df)
            merged_df.drop_duplicates(inplace=True, subset=columns)
            merged_df.dropna(inplace=True, subset=columns, how='all')
            merged_df = merged_df.set_index(columns).sort_values(by=columns, ascending=True).reset_index()
            self.write_file(path=locations_df_path, contents=merged_df, file_format='parquet', binary=True, overwrite=True)
            log.info('Successfully updated locations dataset!')
            print(merged_df)
        except Exception as e:
            log.error(f'Failed to merge with locations.parquet: {e}')
            log.error(traceback.format_exc())
        finally:
            # Release lockfile when done writing
            self.unlock()


    # Create a copy of the existing
    def backup_current_dataset(self, folder_name):
        # Always acquire lockfile to ensure no one is writing
        while not self.lock():
            log.warn('Waiting to acquire lockfile - retrying in 10s')
            sleep(10)

        try:
            # Detect current year using
            # Compute today's timestamp, use that to find first microsecond of the current month
            destination_folder = f'{S3_BUCKET_NAME}/backups/{folder_name}'
            self.client.cp(f'{S3_BUCKET_NAME}/current', destination_folder, recursive=True)
        except Exception as e:
            log.error(f'Failed to backup Parquet datasets in S3 ({folder_name}): {e}')
            log.error(traceback.format_exc())
        finally:
            # Release lockfile when done writing
            self.unlock()


    def get_existing_dataset(self, df_path, columns, dtypes=None):
        existing_df = self.read_file(path=df_path, file_format='parquet', binary=True)
        if existing_df is None:
            log.warn(f'No current dataset found - creating a new / empty dataset:{df_path} ')
            existing_df = pd.DataFrame(columns=columns)
            if dtypes is not None:
                if isinstance(dtypes, str.__class__):
                    # single type given, apply to all columns
                    for col in columns:
                        existing_df[col] = existing_df[col].astype(dtypes)

                if isinstance(dtypes, list.__class__):
                    # multiple types given, apply piecewise
                    # assume some number as index columns
                    if len(columns) != len(dtypes):
                        log.warn('WARNING: column / dtype mismatch when calling get_existing_dataset. Columns={columns}, ')
                    for col in columns:
                        idx = columns.index(col)
                        existing_df[col] = existing_df[col].astype(dtypes[idx])

        return None

    def read_dataset(self, df_path, columns):
        # Get current dataset, or create a new one if it doesn't exist
        existing_df = self.read_file(path=df_path, file_format='parquet', binary=True)
        if existing_df is None:
            log.warn(f'No current dataset found - creating a new / empty dataset:{df_path} ')
            existing_df = pd.DataFrame(columns=columns)

        return existing_df


    # List all files and folders in an S3 directory
    def list_folders(self):
        return self.client.ls(path=f'{S3_BUCKET_NAME}/')


    # Writes raw text file contents to a path in S3
    # Returns the number of bytes written
    def write_file(self, path, contents, file_format='raw', binary=False, overwrite=False):
        if self.client.exists(path) and not overwrite:
            raise FileExistsError(f'File {path} already exists in S3, and overwrite=False.')

        mode = 'w' if not binary else 'wb'
        # Overwrite the file and returns the number of bytes written
        if file_format == 'json':
            # Write dictionary or list to S3 bucket as JSON
            with self.client.open(path, mode) as f:
                json.dumps(f)
            return self.client.stat(path)['size']
        if file_format == 'parquet':
            log.debug(f'Converting dataframe to parquet format...')

            # Convert to a PyArrow Table (to preserve the schema)
            table = pa.Table.from_pandas(contents)
            #log.debug(table)
            with self.client.open(path, mode) as f:
                log.debug(f'Writing parquet file to S3: {path}')
                pq.write_table(table, f)
                log.info(f'Successfully updated parquet file in S3: {path}')
            return self.client.stat(path)['size']
        if file_format == 'raw':
            with self.client.open(path, mode) as f:
                f.write(contents)
            return self.client.stat(path)['size']
        log.warn(f'WARNING: Unrecognized format for writing to S3: {file_format}. No file was written. \
            Please choose one of {self.valid_file_formats}')
        return 0


    def read_file(self, path, file_format='raw', binary=False):
        if not self.client.exists(path):
            return None

        mode = 'r' if not binary else 'rb'
        if file_format == 'json':
            # JSON file does not exist locally - read from S3 bucket as dict
            with self.client.open(path, mode) as f:
                return json.load(f), self.client.stat(path)['size']
        elif file_format == 'parquet':
            existing_dataset = None
            if self.client.exists(path):
                with self.client.open(path, mode) as f:
                    log.debug(f'Reading existing parquet file from S3: {path}')
                    table = pq.read_table(f, use_pandas_metadata=True)
                    existing_dataset = table.to_pandas()
                    log.debug(f'Successfully read parquet file from S3: {path}')
            return existing_dataset
        elif file_format == 'raw':
            # file does not exist locally - read raw contents from S3 bucket
            with self.client.open(path, mode) as f:
                return f.read(), self.client.stat(path)['size']

        log.warn(f'WARNING: Unrecognized format for reading from S3: {file_format}. Please choose one of {self.valid_file_formats}')
        return None, 0

