import argparse
import json
import sys
from decimal import Decimal

from config import log, CLARITY_HOSTNAME, CLARITY_USE_CONTINUATION_TOKEN
from config import OUTPUT_LOCATIONS_AS_JSON, LOCATIONS_OUTPUT_PATH
from config import CONTINUATION_TOKEN_OUTPUT_PATH, QUERY_OUTPUT_PATH
from config import RAW_DATA_OUTPUT_PATH, CLEANED_DATA_OUTPUT_PATH
from config import S3_BUCKET_NAME, S3_UPLOAD_PATH, INDEX_OUTPUT_PATH

from utils import write_txt, write_json_dict, write_csv, run_postprocessing, read_json_dict

from clarity import ClarityAPI
from s3 import S3API


def main(args):
    if not args.fetch and not args.push:
        log.error('You must specify either -f (--fetch) or -p (--push)')
        sys.exit(200)

    # Fetch metrics from the clarity API
    # Write raw metrics (uncleaned) into the output folder
    if args.fetch:
        log.debug(f'Fetching sensor data from {CLARITY_HOSTNAME}')
        clarity = ClarityAPI()
        data, locations, token, query = clarity.fetch_sensor_data()

        # Store metadata about this request: query.json & token.txt
        if CLARITY_USE_CONTINUATION_TOKEN:
            log.debug(f'Saving continuation token to file: {CONTINUATION_TOKEN_OUTPUT_PATH}')
            write_txt(CONTINUATION_TOKEN_OUTPUT_PATH, token)

        log.info(f'Writing query to file: {QUERY_OUTPUT_PATH}')
        write_json_dict(QUERY_OUTPUT_PATH, query)

        # Since we're using continuation tokens, this
        # won't be the full list of locations every time
        if OUTPUT_LOCATIONS_AS_JSON:
            # Scrub locations before uploading
            log.info(f'Fuzzing locations to 4 decimal places of precision...')
            for loc in locations:
                loc["lat"] = Decimal(loc["lat"]).quantize(Decimal('0.0000'))
                loc["lon"] = Decimal(loc["lon"]).quantize(Decimal('0.0000'))
            log.info(f'Writing locations to file: {LOCATIONS_OUTPUT_PATH}')
            write_json_dict(LOCATIONS_OUTPUT_PATH, locations)

        # Output metrics and run post-processing
        log.info(f'Writing data to file: {RAW_DATA_OUTPUT_PATH}')
        write_csv(RAW_DATA_OUTPUT_PATH, data)

        # TODO: adjust after cleaning process is codified
        log.info(f'Running post-processing: {RAW_DATA_OUTPUT_PATH} -> {CLEANED_DATA_OUTPUT_PATH}')
        run_postprocessing(RAW_DATA_OUTPUT_PATH, CLEANED_DATA_OUTPUT_PATH)

        log.info('Data fetched successfully!')


    # Push select files from the output folder to the proper destinations in S3
    if args.push:
        s3api = S3API()

        # Grab top-level objects (folder, etc) from S3
        # Also, append the folder that we are currently uploading :)
        tlos = s3api.list_folders() + [f'{S3_BUCKET_NAME}/{S3_UPLOAD_PATH}']
        with open(INDEX_OUTPUT_PATH, 'w') as f:
            json.dump(tlos, f)

        # Mapping of local file source path -> destination path within S3
        outfile_mapping: dict[str, list[str]] = {
            # Uncomment this line if we want to preserve raw (uncleaned) metrics from Clarity in S3
            #f'{RAW_DATA_OUTPUT_PATH}': [f'{S3_BUCKET_NAME}/{S3_UPLOAD_PATH}/raw.csv'],

            # Uncomment this line if we want to preserve returned locations data from each request
            # NOTE: using continuation tokens means we may not get back the full list every time
            # NOTE: we NEED TO fuzz the precision here - use 4 decimal places instead of 5
            #f'{LOCATIONS_OUTPUT_PATH}': [f'{S3_BUCKET_NAME}/locations.json'],

            # Uncomment this line if we want to preserve the query that was sent with each request
            #f'{QUERY_OUTPUT_PATH}': [f'{S3_BUCKET_NAME}/{S3_UPLOAD_PATH}/query.json'],
            f'{CLEANED_DATA_OUTPUT_PATH}': [
                f'{S3_BUCKET_NAME}/{S3_UPLOAD_PATH}.json',
                f'{S3_BUCKET_NAME}/latest.json'
            ],

            # Include a list of the available files within the bucket
            f'{INDEX_OUTPUT_PATH}': [f'{S3_BUCKET_NAME}/index.json']
        }

        if CLARITY_USE_CONTINUATION_TOKEN:
            outfile_mapping[CONTINUATION_TOKEN_OUTPUT_PATH] = [f'{S3_BUCKET_NAME}/token.txt']

        for key in outfile_mapping:
            for dest in outfile_mapping[key]:
                s3api.push_to_s3(local_path=key, remote_path=dest)

        log.info('Data pushed successfully!')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Chicago Air Quality Sensor Grid')
    parser.add_argument('-f', '--fetch', action='store_true', help='Fetch sensor metrics from Clarity REST API v2')
    #parser.add_argument('-l', '--locations',  action='store_false' if OUTPUT_LOCATIONS_AS_JSON else 'store_true', help='Also output locations from Clarity REST API v2')
    # Data cleanup will take place in between these two independent steps
    parser.add_argument('-p', '--push',  action='store_true',  help='Upload resulting output data to S3 (Minio or AWS S3)')

    main(args=parser.parse_args())