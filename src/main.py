import argparse
import logging
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from config import log, CLARITY_HOSTNAME, CLARITY_USE_CONTINUATION_TOKEN, LOCAL_OUTPUT_DIR
from config import HISTORICAL_START_TIME, HISTORICAL_END_TIME
from config import CONTINUATION_TOKEN_OUTPUT_PATH
from config import S3_BUCKET_NAME, INDEX_OUTPUT_PATH
from config import HISTORICAL_DATA_OUTPUT_PATH, LATEST_DATA_OUTPUT_PATH
from historical import HistoricalMeasurements
from recent import RecentMeasurements
from s3 import S3API
from utils import write_txt


logging.getLogger('s3fs').setLevel(level=logging.INFO)
logging.getLogger('aiobotocore.regions').setLevel(level=logging.INFO)
logging.getLogger('botocore').setLevel(level=logging.INFO)
logging.getLogger('botocore.hooks').setLevel(level=logging.INFO)

def main(args):
    s3api = S3API()

    if args.index:
        s3api = S3API()
        latest_timestamp = s3api.generate_index_file()
        log.info(f'Generated index.json with latest_timestamp={latest_timestamp}')
        sys.exit(0)


    # Sanity check - make sure that fetch was provided either --recent or --historical
    if args.fetch and not args.historical and not args.recent:
        log.error('Fetch (-f) requires either --historical (-H) or --recent (-r)')
        sys.exit(200)

    # Sanity check - make sure user hasn't specified both --recent and --historical
    if  args.historical and args.recent:
        log.error('--recent (-r) and --historical (-H) are mutually exclusive - please choose only one of these flags')
        sys.exit(300)


    # Fetch recent measurements from the clarity API
    # Write raw metrics (uncleaned) into the output folder
    if args.fetch and args.recent:
        log.info(f'Fetching recent measurement data from: {CLARITY_HOSTNAME}')
        clarity = RecentMeasurements(s3api=s3api)
        csv_contents = clarity.recent_fetch_metrics()

        output_path = os.path.join(LOCAL_OUTPUT_DIR, 'raw-measurements-recent.csv')
        write_txt(local_path=output_path, data=csv_contents)

        log.info(f'Data fetched successfully: {output_path}')

        if not args.clean:
            sys.exit(0)


    # Fetch historical measurements from the clarity API
    # Write raw metrics (uncleaned) into the output folder
    if args.fetch and args.historical:
        log.info(f'Fetching historical measurement data from: {CLARITY_HOSTNAME}')
        log.info(f'    Start time: {HISTORICAL_START_TIME}')
        log.info(f'    End time  : {HISTORICAL_END_TIME}')
        clarity = HistoricalMeasurements(s3api=s3api)
        report_processed = clarity.historical_fetch_metrics(start_time=HISTORICAL_START_TIME, end_time=HISTORICAL_END_TIME)

        # Output metrics and run post-processing
        output_path = os.path.join(LOCAL_OUTPUT_DIR, 'raw-measurements-historical-{index}.csv')
        clarity.download_report_contents(report_processed=report_processed, output_path=output_path)

        log.info(f'Historical data fetched successfully: {HISTORICAL_START_TIME} - {HISTORICAL_END_TIME} -> {output_path}')

        if not args.clean:
            sys.exit(0)


    # Clean fetched measurements with R script
    # Write raw metrics (uncleaned) into the output folder
    if args.clean:
        log.info(f'Cleaning up measurement data with R...')

        try:
            output = subprocess.check_output([
                'Rscript',
                'clarity_qa_qc.R',
                '--historical' if args.historical else '--recent'
            ], universal_newlines=True, stderr=subprocess.PIPE)
            print('Result:', output.strip())
        except subprocess.CalledProcessError as e:
            print('R script failed. Error:', e.stderr)
        except FileNotFoundError:
            print('Rscript not found.')

        if not args.merge:
            sys.exit(0)

    # Download existing parquet files from S3, merge latest data, and output the merged parquet file
    # TODO: ignore duplicates? or overwrite? need to pick a conflict-resolution strategy
    if args.merge:
        log.info(f'Running args.merge test...')

        # Ensure column consistency: n_valid, type, date, is_valid, mean_pm25
        log.info(f'Generating latest parquet file: noop')
        hourly = pd.read_csv('data/summary-hourly-0.csv')
        hourly['type'] = 'hour'
        hourly.rename(inplace=True, columns={
            'n_obs': 'n_valid',
            'is_valid_hour': 'is_valid',
            'hour': 'date'
        })
        #print(hourly)

        # Ensure column consistency: n_valid, type, date, is_valid, mean_pm2
        daily = pd.read_csv('data/summary-daily-0.csv')
        daily['type'] = 'day'
        daily.rename(inplace=True, columns={
            'n_valid_hours': 'n_valid',
            'is_valid_day': 'is_valid',
            'daily_mean_pm25': 'mean_pm25',
        })
        #print(daily)


        # Merge daily + hourly into a single pivoted dataframe
        merged_sensor_df = pd.concat([daily, hourly])
        unique_sensor_df = merged_sensor_df.drop_duplicates(subset=['type','date'])
        sensor_ids = [id for id in unique_sensor_df['datasourceId']]
        new_sensor_df = merged_sensor_df.pivot(index=['type','date'], columns=['datasourceId'], values='mean_pm25')
        new_sensor_df['full_network'] = new_sensor_df.mean(axis=1)
        print('Before reset_index')
        print(new_sensor_df)
        new_sensor_df = new_sensor_df.rename(columns={
            'datasourceId': '',
        }).reset_index()
        print('After reset_index')
        print(new_sensor_df)

        # TODO: Fetch existing parquet file, if one exists in S3, read into dataframe
        merged_yearly_data_df = s3api.update_current_dataset(
            metric_name='mean_pm25',
            sensor_ids=sensor_ids,
            new_metric_df=new_sensor_df,
        )

        # Merge latest data into existing dataframe, write as parquet file
        log.info(f'Merging with existing parquet file from CSV: noop')
        #prev_df = pd.read_parquet('data/example.parquet')
        #merged_df = pd.concat([prev_df, new_df], ignore_index=True)
        #table = pa.Table.from_pandas(merged_df)
        #pq.write_table(table, 'data/merged.parquet')
        #merged_df.to_csv('data/merged.csv')

        # TODO: Yearly process to move existing parquet data into a folder labeled with the year.
        #     This should prevent each file from getting too large as the years stretch on.

        if not args.push:
            sys.exit(0)


    # Push select files from the output folder to the proper destinations in S3
    if args.push:
        latest_timestamp = s3api.generate_index_file()

        # Mapping of local file source path -> destination path within S3
        outfile_mapping: dict[str, list[str]] = {
            # Upload historical (hourly past 24h) GeoJSON format
            f'{HISTORICAL_DATA_OUTPUT_PATH}': [
                f'{S3_BUCKET_NAME}/{latest_timestamp}.geojson',
                f'{S3_BUCKET_NAME}/historicalHourly24h.geojson',
            ],

            # Upload simple / latest GeoJSON format
            f'{LATEST_DATA_OUTPUT_PATH}': [f'{S3_BUCKET_NAME}/latest.geojson'],

            # Include a list of the available files within the bucket
            f'{INDEX_OUTPUT_PATH}': [f'{S3_BUCKET_NAME}/index.json'],
        }

        if CLARITY_USE_CONTINUATION_TOKEN:
            outfile_mapping[CONTINUATION_TOKEN_OUTPUT_PATH] = [f'{S3_BUCKET_NAME}/token.txt']

        for key in outfile_mapping:
            for dest in outfile_mapping[key]:
                s3api.push_to_s3(local_path=key, remote_path=dest)

        log.info('Data pushed successfully!')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Chicago Air Quality Sensor Grid')
    parser.add_argument('-i', '--index', action='store_true',
                        help="DEBUG ONLY: generate index.json file, but don't fetch or push new data")
    parser.add_argument('-m', '--merge', action='store_true',
                        help="DEBUG ONLY: download and merge with existing parquet data")
    parser.add_argument('-f', '--fetch', action='store_true',
                        help="Fetch new measurements historical measurements between startTime and endTime (may take awhile)")
    parser.add_argument('-r', '--recent', action='store_true',
                        help="Compute recent measurements data between startTime and now")
    parser.add_argument('-H', '--historical', action='store_true',
                        help="Compute historical measurements between startTime and endTime (may take awhile)")
    parser.add_argument('-c', '--clean', action='store_true',
                        help="Clean the measurement data by running the related R script, compute daily/hourly averages")
    parser.add_argument('-p', '--push', action='store_true',
                        help='Generate a new index (implies -i and -m), then upload resulting output data to S3 (MinIO or AWS S3)')

    main(args=parser.parse_args())
