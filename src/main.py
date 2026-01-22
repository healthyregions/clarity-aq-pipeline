import argparse
import logging
import os
import subprocess
import sys

import pandas as pd
import pyarrow as pa

from config import log, CLARITY_HOSTNAME, LOCAL_OUTPUT_DIR
from config import HISTORICAL_START_TIME, HISTORICAL_END_TIME
from historical import HistoricalMeasurements
from recent import RecentMeasurements
from s3 import S3API


logging.getLogger('asyncio').setLevel(level=logging.INFO)
logging.getLogger('fsspec').setLevel(level=logging.DEBUG)
logging.getLogger('s3fs').setLevel(level=logging.INFO)
logging.getLogger('aiobotocore.regions').setLevel(level=logging.INFO)
logging.getLogger('botocore').setLevel(level=logging.INFO)
logging.getLogger('botocore.hooks').setLevel(level=logging.INFO)

def main(args):
    s3api = S3API()

    if args.archive:
        log.error('Performing yearly archive process.')
        s3api.archive_current_dataset()
        log.error('Yearly archive process complete!')
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
        start_time = args.startTime if args.startTime else None
        log.info(f'Fetching recent measurement data from: {CLARITY_HOSTNAME}')
        clarity = RecentMeasurements(s3api=s3api)
        recent_measurements_df, locations_df, token = clarity.recent_fetch_metrics(start_time=start_time)

        # Merge locations_df into S3 dataset

        # Save to CSV for cleaning
        output_path = os.path.join(LOCAL_OUTPUT_DIR, 'raw-measurements-recent.csv')
        recent_measurements_df.to_csv(output_path)

        log.info(f'Data fetched successfully: {output_path}')

        if not args.clean:
            log.warn('Data cleaning was skipping, so data cannot be merged. Aborting.')
            sys.exit(22)


    # Fetch historical measurements from the clarity API
    # Write raw metrics (uncleaned) into the output folder
    if args.fetch and args.historical:
        start_time = args.startTime if args.startTime else HISTORICAL_START_TIME
        end_time = args.endTime if args.endTime else HISTORICAL_END_TIME

        log.info(f'Fetching historical measurement data from: {CLARITY_HOSTNAME}')
        log.info(f'    Start time: {start_time}')
        log.info(f'    End time  : {end_time}')
        clarity = HistoricalMeasurements(s3api=s3api)
        report_processed = clarity.historical_fetch_metrics(start_time=start_time, end_time=end_time)

        # Output metrics and run post-processing
        output_path = os.path.join(LOCAL_OUTPUT_DIR, 'raw-measurements-historical.csv')
        historical_report_df, locations = clarity.download_report_contents(report_processed=report_processed, output_path=output_path)
        historical_report_df.to_csv(output_path)

        log.info(f'Historical data fetched successfully: {start_time} - {end_time} -> {output_path}')

        if not args.clean:
            log.warn('Data cleaning was skipping, so data cannot be merged. Aborting.')
            sys.exit(22)


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
            log.warn('DRY RUN: --merge was not specified, so this will be treated as a dry run. Aborting.')
            sys.exit(44)

    # Download existing parquet files from S3, merge latest data, and output the merged parquet file
    # TODO: ignore duplicates? or overwrite? need to pick a conflict-resolution strategy
    if args.merge:
        # Ensure column consistency: n_valid, type, date, is_valid, mean_pm25
        log.info(f'Compiling hourly sensor data...')
        hourly = pd.read_csv('data/summary-hourly.csv').rename(columns={
            'n_obs': 'n_valid',
            'is_valid_hour': 'is_valid',
            'hour': 'date',
            'mean_pm25': 'mean_pm25',
            # .. define new metrics here, use consistent column names for hourly + daily ... #
        })
        hourly['type'] = 'hour'

        # Ensure column consistency: n_valid, type, date, is_valid, mean_pm2
        log.info(f'Compiling daily sensor data...')
        daily = pd.read_csv('data/summary-daily.csv').rename(columns={
            'n_valid_hours': 'n_valid',
            'is_valid_day': 'is_valid',
            'date': 'date',
            'daily_mean_pm25': 'mean_pm25',
            # .. define new metrics here, use consistent column names for hourly + daily ... #
        })
        daily['type'] = 'day'

        # Merge daily + hourly into a single pivoted dataframe
        merged_sensor_df = pd.concat([daily, hourly])
        log.debug(merged_sensor_df)

        # Repeat this process process each
        # TODO: Support other metrics?
        for metric in ['mean_pm25']:
            log.info(f'Pivoting data for {metric}.parquet')
            new_sensor_df = merged_sensor_df.pivot(index=['type', 'date'], columns=['datasourceId'], values=metric)
            new_sensor_df['full_network'] = new_sensor_df.mean(axis=1, skipna=True, numeric_only=True)
            new_sensor_df = new_sensor_df.rename(columns={'datasourceId': ''}).reset_index()
            log.debug(new_sensor_df)

            # Merge latest data into existing dataframe, write as parquet file
            log.info(f'Merging with existing {metric}.parquet file...')
            s3api.update_measurements_df(
                metric_name=metric,
                new_measurements_df=new_sensor_df,
            )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Chicago Air Quality Sensor Grid')
    parser.add_argument('-f', '--fetch', action='store_true',
                        help="Fetch new measurements historical measurements between startTime and endTime (may take awhile)")
    parser.add_argument('-r', '--recent', action='store_true',
                        help="Compute recent measurements data between startTime and now")
    parser.add_argument('startTime', nargs='?', default=None,
                        help="Start time for historical or recent measurement requests. Defaults to None for --recent or previous month start for --historical")
    parser.add_argument('endTime',  nargs='?', default=None,
                        help="End time for historical measurement requests. Defaults to previous month end for --historical, but is ignored when using --recent")
    parser.add_argument('-H', '--historical', action='store_true',
                        help="Request a report of historical measurements between startTime and endTime (may take awhile)")
    parser.add_argument('-c', '--clean', action='store_true',
                        help="Clean the measurement data by running the related R script, compute daily/hourly averages")
    parser.add_argument('-m', '--merge', action='store_true',
                        help="Merge new date into existing parquet dataset in S3 (MinIO or AWS S3)")
    parser.add_argument('-a', '--archive', action='store_true',
                        help="Archive existing metrics.parquet files to per-year folders and start with a fresh /current/ folder in S3")

    main(args=parser.parse_args())

