import argparse
import logging
import os
import subprocess
import sys
import traceback

import dateutil
import pandas as pd
import pyarrow as pa


from datetime import datetime, UTC
from config import log, CLARITY_HOSTNAME, LOCAL_OUTPUT_DIR, LOGLEVEL, HISTORICAL_START_TIME, HISTORICAL_END_TIME
from historical import HistoricalMeasurements
from recent import RecentMeasurements
from s3 import S3API

from utils import get_previous_week_dates, combine_dataset_rows, merge_new_data, get_current_3_hours_dates, get_previous_day_dates, get_previous_month_dates, get_previous_season_dates, get_previous_year_dates


logging.getLogger('config').setLevel(level=logging.getLevelName(LOGLEVEL))
logging.getLogger('asyncio').setLevel(level=logging.INFO)
logging.getLogger('fsspec').setLevel(level=logging.DEBUG)
logging.getLogger('s3fs').setLevel(level=logging.INFO)
logging.getLogger('aiobotocore.regions').setLevel(level=logging.INFO)
logging.getLogger('botocore').setLevel(level=logging.INFO)
logging.getLogger('botocore.hooks').setLevel(level=logging.INFO)

# Workaround(?) for _duckdb.NotImplementedException: Not implemented Error: Data type 'str' not recognized
pd.options.future.infer_string = False


def main(args):
    global HISTORICAL_START_TIME, HISTORICAL_END_TIME
    s3api = S3API()

    if args.backup:
        # Use current UTC timestamp as folder_name
        folder_name = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        log.error(f'Backing up Parquet datasets: {folder_name}')
        destination_folder = s3api.backup_current_dataset(folder_name=folder_name)
        log.error(f'Parquet backup process complete: {destination_folder}')
        sys.exit(0)

    if args.locations:
        log.info(f'Merging updated location data from: {args.locations}')
        new_locations_df = pd.read_parquet(args.locations)
        print(new_locations_df)

        log.info(f'Result of merge:')
        merged_df = s3api.update_locations_df(new_locations_df)
        print(merged_df[['datasourceId','name','community','zip']][merged_df['community'] == 'ASHBURN'])

        sys.exit(0)

    if (args.startTime or args.endTime) and (args.weekly or args.monthly or args.weekly or args.yearly):
        log.error('Cannot use startTime or endTime with time-averaging. Specify either startTime/endTime OR --weekly / --monthly / --seasonal / --yearly')
        sys.exit(100)

    # Sanity check - make sure that fetch was provided either --recent or --historical
    if (args.fetch or args.clean) and not args.historical and not args.recent:
        log.error('ERROR: When using --fetch (-f) or --clean, onr of --historical (-H) or --recent (-r) is required')
        sys.exit(200)

    # Sanity check - make sure user hasn't specified both --recent and --historical
    if args.historical and args.recent:
        log.error('ERROR: please choose only one of  --recent (-r) or --historical (-H)')
        sys.exit(300)

    # if args.weekly:
    #     prev_week_start, prev_week_end = get_previous_week_dates()
    #     log.info(f'Fetching weekly historical measurement data from: {CLARITY_HOSTNAME}')
    #     log.info(f'    Start Time: {prev_week_start}')
    #     log.info(f'    End time  : {prev_week_end}')
    #
    #     sys.exit(0)

    # Fetch recent measurements from the clarity API
    # Write raw metrics (uncleaned) into the output folder
    if args.fetch and args.recent:
        start_of_3_hours, _ = get_current_3_hours_dates()
        start_time = args.startTime if args.startTime else start_of_3_hours
        log.info(f'Fetching recent measurement data from: {CLARITY_HOSTNAME}')
        log.info(f'    Start time: {start_time}')
        clarity = RecentMeasurements(s3api=s3api)
        recent_measurements_df, locations_df, token = clarity.recent_fetch_metrics(start_time=start_time)

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

        # If any helper date ranges provided, override other input methods
        # Use largest range provided, should encompass the others
        if args.hourly:
            start_time, end_time = get_current_3_hours_dates()
        if args.daily:
            start_time, end_time = get_previous_day_dates()
        if args.weekly:
            start_time, end_time = get_previous_week_dates()
        if args.monthly:
            start_time, end_time = get_previous_month_dates()
        if args.seasonal:
            start_time, end_time = get_previous_season_dates()
        if args.yearly:
            start_time, end_time = get_previous_year_dates()

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
            print(output.strip())
        except subprocess.CalledProcessError as ex:
            log.error('R script failed. Error:', ex.stderr)
            traceback.print_exc()
            sys.exit(500)
        except FileNotFoundError as ex:
            log.error('ERROR: Rscript not found.', ex)
            traceback.print_exc()
            sys.exit(404)
        except Exception as ex:
            log.error('ERROR: Rscript encountered an unknown exception: ', ex)
            traceback.print_exc()
            sys.exit(501)

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

        # TODO: Use ISO 8601 Format indicating UTC timezone? browser needs special handling otherwise
        # Currently using something more akin to ISO 9705
        #hourly['date'] = hourly['date'].map(lambda d: dateutil.parser.isoparse(d).isoformat() + 'Z')

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

        # Ensure column consistency: n_valid, type, date, is_valid, mean_pm2
        log.info(f'Compiling weekly sensor data...')
        weekly = pd.read_csv('data/summary-weekly.csv').rename(columns={
            'n_valid_days': 'n_valid',
            'is_valid_week': 'is_valid',
            'week': 'date',
            'weekly_mean_pm25': 'mean_pm25',
            # .. define new metrics here, use consistent column names for hourly + daily ... #
        })
        weekly['type'] = 'week'

        # Ensure date in the correct format - 2025-W10, 2025-W09, etc
        weekly['date'] = weekly['date'].map(lambda d: d.split('-')[0]+'-W'+d.split('-')[1][1:].zfill(2))

        # Ensure column consistency: n_valid, type, date, is_valid, mean_pm2
        log.info(f'Compiling monthly sensor data...')
        monthly = pd.read_csv('data/summary-monthly.csv').rename(columns={
            'n_valid_days': 'n_valid',
            'is_valid_month': 'is_valid',
            'month': 'date',
            'monthly_mean_pm25': 'mean_pm25',
            # .. define new metrics here, use consistent column names for hourly + daily ... #
        })
        monthly['type'] = 'month'

        # Ensure date in the correct format - 2025-10, 2025-09, etc
        monthly['date'] = monthly['date'].map(lambda d: d.split('-')[0]+'-'+d.split('-')[1].zfill(2))


        # Ensure column consistency: n_valid, type, date, is_valid, mean_pm2
        log.info(f'Compiling seasonal sensor data...')
        seasonal = pd.read_csv('data/summary-seasonal.csv').rename(columns={
            'n_valid_days': 'n_valid',
            'is_valid_season': 'is_valid',
            'season': 'date',
            'seasonal_mean_pm25': 'mean_pm25',
            # .. define new metrics here, use consistent column names for hourly + daily ... #
        })
        seasonal['type'] = 'season'

        # Ensure column consistency: n_valid, type, date, is_valid, mean_pm2
        log.info(f'Compiling yearly sensor data...')
        yearly = pd.read_csv('data/summary-yearly.csv').rename(columns={
            'n_valid_days': 'n_valid',
            'is_valid_season': 'is_valid',
            'year': 'date',
            'yearly_mean_pm25': 'mean_pm25',
            # .. define new metrics here, use consistent column names for hourly + daily ... #
        })
        yearly['type'] = 'year'

        # Concatenate all rows of different types into single dataframe
        merged_sensor_df = pd.concat([yearly, seasonal, monthly, weekly, daily, hourly])

        # Repeat this process process each
        # TODO: Support other metrics?
        for metric in ['mean_pm25']:
            log.info(f'Pivoting data for {metric}.parquet')
            new_sensor_df = pd.pivot_table(data=merged_sensor_df, values=metric, index=['type', 'date'], columns=['datasourceId'], aggfunc='last', dropna=True)
            new_sensor_df = new_sensor_df.rename(columns={'datasourceId': ''}).reset_index()

            s3api.update_measurements_df(
                metric_name=metric,
                new_measurements_df=new_sensor_df,
            )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Chicago Air Quality Sensor Grid')

    # Choose whether to process Recent Measurements or Historical Measurements
    #     Historical Measurements => startTime to endTime
    #     Recent Measurements => startTime to now
    parser.add_argument('-H', '--historical', action='store_true',
                        help="Request a report of historical measurements between startTime and endTime (may take awhile). Defaults to previous month. WARNING: Historical Measurement requests are expensive for the Clarity API, and are limited to ~30 requests every ~24 hours.")
    parser.add_argument('-r', '--recent', action='store_true',
                        help="Compute recent measurements data between startTime and now. Defaults to 1 hour prior to time of request.")

    # For Recent or Historical Measurements, you can optionally provide a startTime.
    # Only measurements taken after startTime will be returned
    parser.add_argument('startTime', nargs='?', default=None,
                        help="Start time for historical or recent measurement requests. Defaults to None for --recent or previous month start for --historical")

    # For Historical Measurements ONLY, you can optionally provide an endTime.
    # Only measurements before endTime will be returned.
    parser.add_argument('endTime',  nargs='?', default=None,
                        help="End time for historical measurement requests. Defaults to previous month end for --historical, but is ignored when using --recent")

    # Actions to perform on the given set of measurements
    parser.add_argument('-f', '--fetch', action='store_true',
                        help="Fetch new measurement values from Clarity REST API V2 (may take awhile)")
    parser.add_argument('-c', '--clean', action='store_true',
                        help="Clean the measurement data by running the related R script, compute daily/hourly averages")
    parser.add_argument('-m', '--merge', action='store_true',
                        help="Merge new date into existing parquet dataset in S3 (MinIO or AWS S3)")

    # Admin / Debug commands
    parser.add_argument('-b', '--backup', action='store_true',
                        help="Backup existing parquet files in S3")
    parser.add_argument('--hourly', action='store_true',
                        help="Calculate hourly average based on last 3 full hours. This is not called automatically by the pipeline, and --recent is used instead. This command is for repair / testing / DEBUG purposes only.")

    # Manually merge in new columns to the locations dataset (e.g. community, zip, ward, etc)
    # TODO: Hopefully in the future the can be returned by Clarity's API to keep it updated and consistent
    parser.add_argument('-L', '--locations', nargs='?', default=None,
                        help="Provide a path to a locations.parquet to merge in new columns to the locations dataset.")

    # Time-averaging functions - shorthand functions for processes that run once per week / month / season / year
    # For our purposes, a season is defined 3 months of the year
    parser.add_argument('--daily', action='store_true',
                        help="Calculate daily average based on last full day")
    parser.add_argument('--weekly', action='store_true',
                        help="Calculate weekly average based on previous full week (Monday - Sunday)")
    parser.add_argument('--monthly', action='store_true',
                        help="Calculate monthly average calculation based on previous full month")
    parser.add_argument('--seasonal', action='store_true',
                        help="Calculate seasonal average calculation based on previous full season (3 months)")
    parser.add_argument('--yearly', action='store_true',
                        help="Calculate yearly average calculation based on previous full year (12 months)")

    main(args=parser.parse_args())

