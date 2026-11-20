# Fetches hourly observations from US EPA regulatory monitors via the AirNow API,
# and reshapes them to look like Clarity's csv-wide hourly output so that the rest of
# the pipeline (R cleaning script -> temporal averages -> pivot -> Parquet merge) is shared.
#
# Raw output columns (one row per monitoring site per hour):
#   datasourceId, sourceId, sourceType, outputFrequency, startOfPeriod, endOfPeriod,
#   locationLatitude, locationLongitude, <colName>
#
# Time convention: AirNow's `UTC` field is the BEGINNING of the 1-hour averaging period.
# Clarity labels each hour by its END (endOfPeriod), and the R script floors endOfPeriod
# to the hour, so we emit endOfPeriod = UTC + 1h to keep hour labels aligned between sources.

import sys
from datetime import datetime, timedelta, UTC
from time import sleep

import numpy as np
import pandas as pd
import requests

from config import log, AIRNOW_API_KEY, AIRNOW_HOSTNAME, AIRNOW_BBOX, AIRNOW_CITY_ONLY, AIRNOW_MAX_HOURS_PER_REQUEST
from s3 import S3API
from utils import assign_spatial_groups

SOURCE_TYPE = 'AIRNOW'

# Pollutants we request in each call. All AirNow operations share one fetch per time range.
AIRNOW_PARAMETERS = 'PM25,NO2'

# AirNow reports missing data using negative sentinel values (e.g. -999)
MISSING_SENTINEL_THRESHOLD = -900

LOCATION_COLUMNS = ['datasourceId', 'sourceId', 'currentSourceId', 'sourceType', 'locationLatitude',
                    'locationLongitude', 'name', 'group', 'tags', 'community', 'comarea', 'ward', 'zip']


def _parse_time(value) -> datetime:
    ts = pd.Timestamp(value)
    ts = ts.tz_localize(UTC) if ts.tzinfo is None else ts.tz_convert(UTC)
    return ts.to_pydatetime()


def _floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _ceil_hour(dt: datetime) -> datetime:
    floored = _floor_hour(dt)
    return floored if floored == dt else floored + timedelta(hours=1)


def _normalize_parameter(name: str) -> str:
    # 'PM2.5' -> 'PM25', 'no2' -> 'NO2'
    return str(name).replace('.', '').replace('_', '').upper()


class AirNowAPI(object):
    # Observations are cached per (start, end) so that running several AirNow operations
    # in one invocation (e.g. airnow_aqi airnow_pm25 airnow_no2) only hits the API once
    _observations_cache: dict = {}
    _locations_updated: set = set()

    def __init__(self, s3api: S3API):
        self.s3api = s3api
        self.dataUrl = f'{AIRNOW_HOSTNAME}/aq/data/'

    # Returns the range of hour-beginning timestamps whose endOfPeriod falls in [start_time, end_time)
    @staticmethod
    def hour_range(start_time, end_time):
        start = _parse_time(start_time)
        end = _parse_time(end_time) if end_time else datetime.now(UTC)
        first_begin = _ceil_hour(start - timedelta(hours=1))
        last_begin = _floor_hour(end - timedelta(hours=1) - timedelta(microseconds=1))
        return first_begin, last_begin

    def _get(self, params: dict, max_retries=5):
        redacted = {k: v for k, v in params.items() if k != 'API_KEY'}
        log.info(f'Submitting AirNow query: {redacted}')
        for attempt in range(max_retries + 1):
            r = requests.get(self.dataUrl, params=params, timeout=120)
            if r.status_code == 429 or r.status_code >= 500:
                wait = min(60, 5 * 2 ** attempt)
                log.warning(f'AirNow API returned HTTP {r.status_code} - retrying in {wait}s ({attempt + 1}/{max_retries})')
                sleep(wait)
                continue
            r.raise_for_status()
            body = r.json()
            # Errors are sometimes returned as an HTTP 200 with an error object instead of a list
            if isinstance(body, dict):
                raise requests.exceptions.HTTPError(f'AirNow API error: {body}', response=r)
            return body
        r.raise_for_status()
        raise requests.exceptions.HTTPError(f'AirNow API failed after {max_retries} retries', response=r)

    # Fetch all hourly observations (long format) for monitors in AIRNOW_BBOX
    def fetch_observations(self, start_time, end_time=None) -> pd.DataFrame:
        if not AIRNOW_API_KEY:
            raise ValueError('AIRNOW_API_KEY is not set - cannot fetch from the AirNow API')

        first_begin, last_begin = self.hour_range(start_time, end_time)
        cache_key = (first_begin, last_begin)
        if cache_key in self._observations_cache:
            log.info(f'Using cached AirNow observations: {first_begin} - {last_begin}')
            return self._observations_cache[cache_key]

        if last_begin < first_begin:
            log.warning(f'No complete hours between {start_time} and {end_time} - nothing to fetch from AirNow')
            return pd.DataFrame()

        records = []
        chunk_start = first_begin
        while chunk_start <= last_begin:
            chunk_end = min(last_begin, chunk_start + timedelta(hours=AIRNOW_MAX_HOURS_PER_REQUEST - 1))
            records += self._get({
                'startDate': chunk_start.strftime('%Y-%m-%dT%H'),
                'endDate': chunk_end.strftime('%Y-%m-%dT%H'),
                'parameters': AIRNOW_PARAMETERS,
                'BBOX': AIRNOW_BBOX,
                'dataType': 'B',            # both AQI and concentrations
                'format': 'application/json',
                'verbose': 1,               # include SiteName, AgencyName, AQS codes
                'monitorType': 0,           # permanent (regulatory) monitors only
                'includerawconcentrations': 1,
                'API_KEY': AIRNOW_API_KEY,
            })
            chunk_start = chunk_end + timedelta(hours=1)

        observations = pd.DataFrame.from_records(records)
        log.info(f'Fetched {len(observations)} AirNow observations: {first_begin} - {last_begin}')
        self._observations_cache[cache_key] = observations
        return observations

    # Build a locations dataframe (same columns as Clarity's locations) for each AirNow site
    def gather_locations(self, observations: pd.DataFrame) -> pd.DataFrame:
        sites = observations[['datasourceId', 'sourceId', 'locationLatitude', 'locationLongitude', 'SiteName']] \
            .drop_duplicates(subset=['datasourceId', 'sourceId']) \
            .rename(columns={'SiteName': 'name'})
        sites['currentSourceId'] = sites['sourceId']
        sites['sourceType'] = SOURCE_TYPE
        sites['group'] = None
        sites['tags'] = [['AirNow'] for _ in range(len(sites))]

        sites = assign_spatial_groups(sites)
        return sites.sort_values(by=['datasourceId', 'sourceId']).reset_index(drop=True)[LOCATION_COLUMNS]

    # Returns a "raw measurements" DataFrame shaped like Clarity's hourly csv-wide output
    #   parameter: AirNow pollutant ('PM25' or 'NO2')
    #   fields: AirNow value fields to use, in order of preference (e.g. ['RawConcentration', 'Value'])
    #   col_name: name of the output value column (must match `colName` in operations.yml)
    def fetch_metrics(self, start_time, end_time, parameter: str, fields: list[str], col_name: str):
        try:
            observations = self.fetch_observations(start_time=start_time, end_time=end_time)
        except requests.exceptions.ConnectionError as ex:
            log.error(f'Connection Error: Could not connect to the AirNow API. Details: {ex}')
            sys.exit(3)
        except requests.exceptions.Timeout as ex:
            log.error(f'Timeout Error: The AirNow request timed out. Details: {ex}')
            sys.exit(4)
        except requests.exceptions.HTTPError as ex:
            log.error(f'HTTP Error: A bad HTTP status code was received from AirNow. Details: {ex}')
            sys.exit(5)
        except requests.exceptions.RequestException as ex:
            log.error(f'An unexpected Requests error occurred while querying AirNow. Details: {ex}')
            sys.exit(6)

        empty = pd.DataFrame(columns=['datasourceId', 'sourceId', 'sourceType', 'outputFrequency', 'startOfPeriod',
                                      'endOfPeriod', 'locationLatitude', 'locationLongitude', col_name])
        if observations.empty:
            return empty, pd.DataFrame(columns=LOCATION_COLUMNS)

        obs = observations.copy()

        # Prefer the full AQS code (unique per site), fall back to the international code
        aqs = obs['FullAQSCode'] if 'FullAQSCode' in obs else pd.Series(index=obs.index, dtype=object)
        if 'IntlAQSCode' in obs:
            aqs = aqs.fillna(obs['IntlAQSCode'])
        obs['sourceId'] = aqs.astype(str)
        obs['datasourceId'] = SOURCE_TYPE + '_' + obs['sourceId']
        obs['locationLatitude'] = pd.to_numeric(obs['Latitude']).round(4)
        obs['locationLongitude'] = pd.to_numeric(obs['Longitude']).round(4)
        if 'SiteName' not in obs:
            obs['SiteName'] = obs['sourceId']

        # Assign ward / community / zip to every site (all pollutants), optionally limit to the City of Chicago
        locations_df = self.gather_locations(obs)
        if AIRNOW_CITY_ONLY:
            outside = locations_df[locations_df['community'].isna()]
            if len(outside) > 0:
                log.info(f'Discarding {len(outside)} AirNow site(s) outside the City of Chicago: {outside["name"].tolist()}')
            locations_df = locations_df[locations_df['community'].notna()].reset_index(drop=True)
            obs = obs[obs['datasourceId'].isin(locations_df['datasourceId'])]

        # Merge AirNow sites into the shared locations dataset (once per set of sites per run)
        site_key = tuple(sorted(locations_df['datasourceId'].tolist()))
        if len(locations_df) > 0 and site_key not in self._locations_updated:
            self.s3api.update_locations_df(new_locations_df=locations_df)
            self._locations_updated.add(site_key)

        # Keep only the requested pollutant
        obs = obs[obs['Parameter'].map(_normalize_parameter) == _normalize_parameter(parameter)].copy()

        # Pick the first available field, treating AirNow's negative sentinels as missing
        value = pd.Series(np.nan, index=obs.index, dtype=float)
        for field in fields:
            if field not in obs:
                continue
            candidate = pd.to_numeric(obs[field], errors='coerce')
            candidate = candidate.where(candidate > MISSING_SENTINEL_THRESHOLD)
            if field == 'AQI':
                candidate = candidate.where(candidate >= 0)
            value = value.fillna(candidate)
        obs[col_name] = value

        # Convert AirNow hour-beginning timestamps to Clarity-style start/end of period
        start = pd.to_datetime(obs['UTC'], utc=True)
        obs['startOfPeriod'] = start.dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')
        obs['endOfPeriod'] = (start + pd.Timedelta(hours=1)).dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')
        obs['sourceType'] = SOURCE_TYPE
        obs['outputFrequency'] = 'hour'

        raw_df = obs[empty.columns.tolist()] \
            .dropna(subset=[col_name]) \
            .drop_duplicates(subset=['datasourceId', 'endOfPeriod'], keep='last') \
            .sort_values(by=['endOfPeriod', 'datasourceId']) \
            .reset_index(drop=True)
        log.info(f'AirNow {parameter} ({"/".join(fields)}): {len(raw_df)} hourly values from {raw_df["datasourceId"].nunique()} site(s)')
        return raw_df, locations_df
