import argparse
import logging
import os
import sys

import pandas as pd

import yaml


from datetime import datetime, UTC
from config import log, CLARITY_HOSTNAME, LOCAL_OUTPUT_DIR, LOGLEVEL, HISTORICAL_START_TIME, HISTORICAL_END_TIME
from historical import HistoricalMeasurements
from recent import RecentMeasurements
from s3 import S3API

from utils import merge_temporal_averages_to_df, run_r_script, get_previous_week_dates, combine_dataset_rows, merge_new_data, get_current_3_hours_dates, get_previous_day_dates, get_previous_month_dates, get_previous_season_dates, get_previous_year_dates


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

    if args.ops:
        ops_defn_path = './operations.yml'
        all_operations = ['mean_pm25', 'nowcast_aqi']
        ops_to_run = all_operations if args.ops == '*' else args.ops
        with open(ops_defn_path) as f:
            operations = yaml.safe_load(f)

        print('All known ops: ' + str(all_operations))
        print('Now running: ' + str(ops_to_run))
        print('Operations definitions: ' + str(operations))

        for metric_name in ops_to_run:
            if metric_name not in operations:
                print(f'ERROR: {metric_name} not defined in operations.yml')
                continue
            print('Running operation: ' + metric_name)
            op_defn = operations[metric_name]
            print('Definition: ' + str(op_defn))
            metricSelect = op_defn['metricSelect']
            outputFrequency = op_defn['outputFrequency']
            qc = op_defn['qc'] if 'qc' in op_defn else False

            fetched_data_path = os.path.join(LOCAL_OUTPUT_DIR, metric_name + '-raw-measurements.csv')
            final_data_path = os.path.join(LOCAL_OUTPUT_DIR, metric_name + '-measurements.csv')


            #measurements_df =  None

            # Fetch recent measurements from the clarity API
            # Write raw metrics (uncleaned) into the output folder
            if not args.historical:
                start_of_3_hours, _ = get_current_3_hours_dates()
                start_time = args.startTime if args.startTime else start_of_3_hours
                log.info(f'Fetching recent measurement data from: {CLARITY_HOSTNAME}')
                log.info(f'    Start time: {start_time}')

                # Fetch recent measurements and save locations from them
                clarity = RecentMeasurements(s3api=s3api)
                recent_measurements_df, locations_df, token = clarity.recent_fetch_metrics(start_time=start_time, metricSelect=metricSelect, outputFrequency=outputFrequency, qc=qc)
                recent_measurements_df.to_csv(fetched_data_path)
                log.info(f'Data fetched successfully: {fetched_data_path}')
                #measurements_df = recent_measurements_df
                #measurements_df = pd.read_csv(fetched_data_path) # None

            # Fetch recent or historical measurements from the clarity API
            # Write raw metrics (uncleaned) into the output folder
            else:
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

                # Fetch/poll for historical measurements and save locations from them
                clarity = HistoricalMeasurements(s3api=s3api)
                report_processed = clarity.historical_fetch_metrics(start_time=start_time, end_time=end_time, metricSelect=metricSelect, outputFrequency=outputFrequency, qc=qc)
                historical_report_df, locations = clarity.download_report_contents(report_processed=report_processed)
                historical_report_df.to_csv(fetched_data_path)
                log.info(f'Historical data fetched successfully: {start_time} - {end_time} -> {fetched_data_path}')
                #measurements_df = historical_report_df
                #measurements_df = pd.read_csv(fetched_data_path) # None

            # Clean fetched measurements with R script (if provided)
            # Write raw metrics (uncleaned) into the output folder
            log.info(f'Cleaning up measurement data with R script: {op_defn["cleaningScript"]}...')
            run_r_script(
                scriptPath=op_defn['cleaningScript'],
                inputFile=fetched_data_path,
                metricName=metric_name,
                minObsPerHour=op_defn['minObsPerHour'] if 'minObsPerHour' in op_defn else '1',
            )

            measurements_df = merge_temporal_averages_to_df(metric_name=metric_name, op_defn=op_defn)

            # Aggregate per-minute data to hourly average?
            # if outputFrequency == 'minute':
            #     log.info(f'Computing hourly averages from per-minute data...')
            #     measurements_df = run_r_script(
            #         scriptPath='./scripts/scratch.R',
            #         inputFile=temp_data_path,
            #         outputFile=temp_data_path,
            #         metricName=metric_name,
            #         minObsPerHour=op_defn['minObsPerHour'] if 'minObsPerHour' in op_defn else '1',
            #     )

            if 'postprocessing' in op_defn:
                log.info(f'Running postprocessing for: {metric_name}...')
                if 'renameColumns' in op_defn['postprocessing']:
                    renameColumns = op_defn['postprocessing']['renameColumns']
                    measurements_df.rename(columns=renameColumns, inplace=True)

            log.info(f'Post-processing complete: {final_data_path}')
            #log.info(f'Computing temporal averages...')
            measurements_df.to_csv(final_data_path)

            # Compute temporal averages based on hourly data
            # measurements_df = measurements_df[['type', 'date', 'datasourceId', 'sourceId', 'nowcast_aqi']]
            # dt_index = pd.to_datetime(measurements_df['date'])
            # measurements_df.date.index = dt_index.map(lambda x: x.isoformat())
            # measurements_df.set_index(['type', 'date', 'datasourceId', 'sourceId'], inplace=True)

            print(measurements_df)
            #
            # daily = (measurements_df
            #              .groupby(['type', 'datasourceId', 'sourceId'])
            #              .resample('D', level='date').mean())
            # weekly = (measurements_df
            #              .groupby(['type', 'datasourceId', 'sourceId'])
            #              .resample('W', level='date').mean())
            # monthly = (measurements_df
            #              .groupby(['type', 'datasourceId', 'sourceId'])
            #              .resample('ME', level='date').mean())
            # yearly = (measurements_df
            #              .groupby(['type', 'datasourceId', 'sourceId'])
            #              .resample('YE', level='date').mean())
            # print('Daily:', daily)
            # print('Weekly:', weekly)
            # print('Monthly:', monthly)
            # print('Yearly:', yearly)

            # Repeat this process for each metric
            log.info(f'Pivoting data for {metric_name}.parquet')
            new_sensor_df = pd.pivot_table(data=measurements_df, values=metric_name, index=['type', 'date'], columns=['datasourceId'], aggfunc='last', dropna=True)
            new_sensor_df = new_sensor_df.rename(columns={'datasourceId': ''}).reset_index()


            s3api.update_measurements_df(
                metric_name=metric_name,
                new_measurements_df=new_sensor_df,
            )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Chicago Air Quality Sensor Grid')

    # Choose whether to process Recent Measurements or Historical Measurements
    #     Historical Measurements => startTime to endTime
    #     Recent Measurements => startTime to now
    parser.add_argument('-H', '--historical', action='store_true',
                        help="Request a report of historical measurements between startTime and endTime (may take awhile). Defaults to previous month.")
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
                        help="Fetch new measurement values from Clarity REAST API V2 (may take awhile)")
    parser.add_argument('-c', '--clean', action='store_true',
                        help="Clean the measurement data by running the related R script, compute daily/hourly averages")
    parser.add_argument('-m', '--merge', action='store_true',
                        help="Merge new date into existing parquet dataset in S3 (MinIO or AWS S3)")

    # Admin / Debug
    parser.add_argument('-o', '--ops', nargs='*', default=None, help="Run operations as defined in operations.yml")
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

