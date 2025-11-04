import argparse
import sys
from decimal import Decimal

from config import log, CLARITY_HOSTNAME, CLARITY_USE_CONTINUATION_TOKEN
from config import OUTPUT_LOCATIONS_AS_JSON, LOCATIONS_OUTPUT_PATH
from config import CONTINUATION_TOKEN_OUTPUT_PATH, QUERY_OUTPUT_PATH
from config import RAW_DATA_OUTPUT_PATH, CLEANED_DATA_OUTPUT_PATH
from config import S3_BUCKET_NAME, S3_UPLOAD_PATH, INDEX_OUTPUT_PATH
from config import HISTORICAL_DATA_OUTPUT_PATH, LATEST_DATA_OUTPUT_PATH

from utils import write_txt, write_json_dict, write_csv
from utils import run_postprocessing, to_geojson_simple, to_geojson_historical

from clarity import ClarityAPI
from s3 import S3API


def main(args):
    if args.index:
        s3api = S3API()
        latest_timestamp = s3api.generate_index_file()
        log.info(f'Generated index.json with latest_timestamp={latest_timestamp}')
        sys.exit(0)

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
        json_data = run_postprocessing(RAW_DATA_OUTPUT_PATH, CLEANED_DATA_OUTPUT_PATH)

        # TODO: convert to geojson format
        log.info(f'Bundling historical GeoJSON (24h hourly): {HISTORICAL_DATA_OUTPUT_PATH}')
        geojson_historical = to_geojson_historical(json_data, locations, S3_UPLOAD_PATH)
        write_json_dict(HISTORICAL_DATA_OUTPUT_PATH, geojson_historical)

        log.info(f'Bundling latest values as GeoJSON: {LATEST_DATA_OUTPUT_PATH}')
        geojson_simple = to_geojson_simple(json_data, locations, S3_UPLOAD_PATH)
        write_json_dict(LATEST_DATA_OUTPUT_PATH, geojson_simple)

        log.info('Data fetched successfully!')


    # Push select files from the output folder to the proper destinations in S3
    if args.push:
        s3api = S3API()
        latest_timestamp = s3api.generate_index_file()

        # Mapping of local file source path -> destination path within S3
        outfile_mapping: dict[str, list[str]] = {
            # Upload historical (hourly past 24h) GeoJSON format
            f'{HISTORICAL_DATA_OUTPUT_PATH}': [
                f'{S3_BUCKET_NAME}/{latest_timestamp}.geojson',
                f'{S3_BUCKET_NAME}/historicalHourly24h.geojson'
            ],

            # Upload simple / latest GeoJSON format
            f'{LATEST_DATA_OUTPUT_PATH}': [f'{S3_BUCKET_NAME}/latest.geojson'],

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
    parser.add_argument('-i', '--index', action='store_true',
                        help="Only generate index.json file. don't fetch or push")

    main(args=parser.parse_args())