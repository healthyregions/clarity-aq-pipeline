from io import StringIO

import pandas as pd
import requests
from requests import RequestException, Response
import sys

from config import log, CLARITY_HOSTNAME, CLARITY_ORG_NAME, CLARITY_API_KEY, CLARITY_USE_CONTINUATION_TOKEN
from s3 import S3API
from utils import from_json


# globals: S3_BUCKET_NAME, CLARITY_API_KEY, CLARITY_ORG_NAME, CONTINUATION_TOKEN_OUTPUT_PATH, s3_client
class ClarityAPI(object):
    def __init__(self, s3api: S3API):
        self.s3api = s3api

        # Current R data cleanup script expects the following:
        #    - pm2_5ConcMassIndividual
        #    - relHumidInternalIndividual
        #    - temperatureInternalIndividual
        # Future: NO2 / BlackCarbon needs R script support
        #    -  + :no2 + :blackcarbon
        # Future: AQI needs R script support to replace NowCast
        self.metricSelect = 'only + :pm25 + :internal'

        # Endpoint URLs / default headers for Clarity's API
        self.orgName = CLARITY_ORG_NAME
        self.measurementsUrl = f'{CLARITY_HOSTNAME}/recent-datasource-measurements-query'
        self.continuationUrl = f'{CLARITY_HOSTNAME}/recent-datasource-measurements-continuation'
        self.historicalUrl = f'{CLARITY_HOSTNAME}/report-requests'
        self.datasourcesUrl = f'{CLARITY_HOSTNAME}/datasources'
        self.reportsUrl = CLARITY_HOSTNAME + '/report-request/{report_id}'  # /{report_id} must be appended
        self.headers = {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'Accept-Encoding': 'gzip',
          'x-api-key': CLARITY_API_KEY
        }

    def gather_datasources(self):
        try:
            log.info('Gathering datasources...')
            response = requests.get(self.datasourcesUrl, { 'org': self.orgName }, headers=self.headers).json()
            return response['datasources']
        except requests.exceptions.ConnectionError as ex:
            self.log_exception(ex, 'Connection Error: Could not connect to the server')
            sys.exit(3)
        except requests.exceptions.Timeout as ex:
            self.log_exception(ex, 'Timeout Error: The request timed out')
            sys.exit(4)
        except requests.exceptions.HTTPError as ex:
            self.log_exception(ex, 'HTTP Error: A bad HTTP status code was received')
            sys.exit(5)
        except requests.exceptions.RequestException as ex:
            self.log_exception(ex, 'An unexpected Requests error occurred')
            sys.exit(6)
        except Exception as ex:
            self.log_exception(ex, 'An unexpected Requests error occurred')
            sys.exit(10)


    def log_exception(self, ex: RequestException|Exception, message: str):
        log.error(f'{message}. Details: {ex}')
        try:
            if isinstance(ex, RequestException):
                log.error(ex.response.text)
            else:
                log.error(ex)
        except Exception as ex2:
            log.fatal(f'FATAL: {str(ex2)}')
            log.fatal('Encountered a failure while logging response error. Shutting down....')
            sys.exit(99)

    def assign_groups(self, df: pd.DataFrame):
        log.info('Assigning groups...')
        import geopandas as gpd
        import pandas as pd
        from shapely.geometry import Point

        # Step 1: Convert the pandas DataFrame into a GeoDataFrame
        # GeoJSON uses longitude first (X), then latitude (Y)
        geometry = [Point(xy) for xy in zip(df["locationLongitude"], df["locationLatitude"])]
        gdf_locations = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")

        # Step 2: Load the boundary GeoJSON files
        # Use same boundaries as the frontend, we can fetch these directly from github
        path_prefix = 'https://raw.githubusercontent.com/healthyregions/chi-air/refs/heads/main/public/geojson/'
        gdf_wards = gpd.read_file(f"{path_prefix}/boundaries_wards_2023_.geojson")
        gdf_communities = gpd.read_file(f"{path_prefix}/community_areas.geojson")
        gdf_zips = gpd.read_file(f"{path_prefix}/chiZipCodes.geojson")

        # Step 3: Map your specific GeoJSON property names to target column names
        # Ensure these match the exact keys inside your files' "properties" objects
        gdf_wards = gdf_wards[["ward", "geometry"]]  # e.g., property is "ward"
        gdf_communities = gdf_communities[["community", "geometry"]]  # e.g., property is "community"
        gdf_zips = gdf_zips[["zip", "geometry"]]  # e.g., property is "zip"

        # Step 4: Perform sequential spatial joins (Point-in-Polygon)
        # "left" ensures you keep all locations even if they fall outside a boundary
        final_gdf = gpd.sjoin(gdf_locations, gdf_wards, how="left", predicate="within")
        final_gdf = final_gdf.drop(columns=["index_right"])  # Drop index artifact from first join

        final_gdf = gpd.sjoin(final_gdf, gdf_communities, how="left", predicate="within")
        final_gdf = final_gdf.drop(columns=["index_right"])

        final_gdf = gpd.sjoin(final_gdf, gdf_zips, how="left", predicate="within")
        final_gdf = final_gdf.drop(columns=["index_right"])

        # Step 5: Clean up and save back to Parquet
        # Drop the spatial geometry column to revert to a standard Pandas DataFrame
        return pd.DataFrame(final_gdf).drop(columns=["geometry"])
        #final_df = pd.DataFrame(final_gdf).drop(columns=["geometry"])
        #final_df.to_parquet("locations_with_boundaries.parquet")


    def gather_locations(self, measurements_df):
        log.info('Gathering locations...')
        locations_df_columns = ['datasourceId', 'sourceId', 'sourceType', 'locationLatitude', 'locationLongitude']
        locations_df = measurements_df[locations_df_columns] \
                .drop_duplicates(subset=['datasourceId', 'sourceId']) \
                .round(decimals=4)

        # TODO: datasources.orgAnnotations.name
        datasources = self.gather_datasources()
        #datasources_df = pd.DataFrame(datasources)[['datasourceId', 'currentSourceId', 'sourceType']]
        datasources_df = pd.json_normalize(datasources).rename(columns={
            'orgAnnotations.name': 'name',
            'orgAnnotations.group': 'group',
            'orgAnnotations.tags': 'tags',
        })

        # Zip up locations + datasources using datasourceID as key, flatten + include org annotations (name/group/tags)
        subset_columns = ['datasourceId', 'currentSubscriptionId', 'currentSourceId', 'name', 'group', 'tags']
        locations_df = pd.merge(locations_df, datasources_df[subset_columns], on='datasourceId', how='left')

        log.info('Merged locations + datasources!')
        log.info('Assigning groups: community, zip, ward')
        locations_df = self.assign_groups(locations_df)

        log.info('Spatial groups have been assigned!')
        log.info('Writing resulting locations dataset to S3...')

        sorted_df = locations_df.set_index(['datasourceId', 'sourceId']).sort_values(axis='rows', by=['datasourceId', 'sourceId']).reset_index()
        return sorted_df[['datasourceId', 'sourceId', 'currentSourceId', 'sourceType', 'locationLatitude', 'locationLongitude', 'name', 'group', 'tags', 'community', 'ward', 'zip']]


    # Parse csv-wide Response and return CSV contents as a pandas Dataframe
    def parse_results_csv_wide(self, r: Response):
        r.raise_for_status()
        data_buffer = StringIO(r.text)
        return pd.read_csv(data_buffer)


    # DEPRECATED: Not currently used, but helpful as a reference
    # Parse json-long Response and return a 4-tuple
    #    data => sensor data for each requested datasource
    #    locations => a separate list of the lat/long coordinates for each related datasource
    #    query => the initial request submitted for this report
    #    token => continuation token, if requested, otherwise returns None
    def parse_results_json_long(self, r: Response):
        r.raise_for_status()
        response = r.json()

        query = response['request']
        log.debug(f'Submitted query: {query}')
        data = from_json(response['data'])
        log.debug(f'Fetched data: {data}')
        if 'locations' not in response:
            log.warning(f'Warning: no metric updates detected since last run - skipping pushing empty data file')
            sys.exit(25)

        locations = response['locations']
        log.debug(f'Fetched locations: {locations}')
        if CLARITY_USE_CONTINUATION_TOKEN:
            token = r.headers['x-clarity-continuation-token']
            log.debug(f'New continuation token: {token}')
        else:
            token = None

        return data, locations, token, query

