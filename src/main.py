import argparse
import os
import subprocess
import sys

from config import log, CLARITY_HOSTNAME, CLARITY_USE_CONTINUATION_TOKEN, LOCAL_OUTPUT_DIR
from config import HISTORICAL_START_TIME, HISTORICAL_END_TIME
from config import CONTINUATION_TOKEN_OUTPUT_PATH
from config import S3_BUCKET_NAME, INDEX_OUTPUT_PATH
from config import HISTORICAL_DATA_OUTPUT_PATH, LATEST_DATA_OUTPUT_PATH
from historical import HistoricalMeasurements
from recent import RecentMeasurements
from s3 import S3API
from utils import write_txt


def main(args):
    if args.index:
        s3api = S3API()
        latest_timestamp = s3api.generate_index_file()
        log.info(f'Generated index.json with latest_timestamp={latest_timestamp}')
        sys.exit(0)


    if not args.clean and not args.historical and not args.recent and not args.push:
        log.error('You must specify either -c (--clean), -H (--historical), -r (--recent), or -p (--push)')
        sys.exit(200)


    # Fetch recent measurements from the clarity API
    # Write raw metrics (uncleaned) into the output folder
    if args.recent:
        log.info(f'Fetching recent measurement data from: {CLARITY_HOSTNAME}')
        clarity = RecentMeasurements()
        csv_contents = clarity.recent_fetch_metrics()

        output_path = os.path.join(LOCAL_OUTPUT_DIR, 'raw-measurements-recent.csv')
        write_txt(local_path=output_path, data=csv_contents)

        log.info(f'Data fetched successfully: {output_path}')

        if not args.clean:
            sys.exit(0)


    # Fetch historical measurements from the clarity API
    # Write raw metrics (uncleaned) into the output folder
    if args.historical:
        log.info(f'Fetching historical measurement data from: {CLARITY_HOSTNAME}')
        log.info(f'    Start time: {HISTORICAL_START_TIME}')
        log.info(f'    End time  : {HISTORICAL_END_TIME}')
        clarity = HistoricalMeasurements()
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

        if not args.push:
            sys.exit(0)


    # Push select files from the output folder to the proper destinations in S3
    if args.push:
        s3api = S3API()
        latest_timestamp = s3api.generate_index_file()

        # TODO: Fetch existing parquet file, if one exists in S3
        # TODO: Create a new parquet file if one doesn't exist
        # TODO: Merge new data into parquet file
        # TODO: Yearly process to move existing parquet data into a folder labeled with the year.
        #     This should prevent each file from getting too large as the years stretch on.

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
    parser.add_argument('-r', '--recent', action='store_true',
                        help="Compute recent measurements data between startTime and now")
    parser.add_argument('-H', '--historical', action='store_true',
                        help="Compute historical measurements between startTime and endTime (may take awhile)")
    parser.add_argument('-c', '--clean', action='store_true',
                        help="Clean the measurement data by running the related R script, compute daily/hourly averages")
    parser.add_argument('-p', '--push', action='store_true',
                        help='Generate a new index (implies -i), then upload resulting output data to S3 (MinIO or AWS S3)')

    main(args=parser.parse_args())
