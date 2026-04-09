import json
import os
import subprocess
import sys
import traceback
from decimal import Decimal

# timedelta for microseconds/days/hours, relativedelta for months/years
from datetime import datetime, UTC, timedelta
from typing import Any

from dateutil.relativedelta import relativedelta

import duckdb
import pandas as pd
from pandas import DataFrame
from pathlib import Path

from config import log, LOCAL_OUTPUT_DIR

# For Pandas > 3.0.0, DType==string is not yet well-supported
pd.options.future.infer_string = False

def isoformat(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')

# Use now's date to find start hour 3+ hours ago and the end of the current hour
#   Used for --recent
def get_current_3_hours_dates():
    start_of_current_hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    end_of_previous_hour = start_of_current_hour - timedelta(microseconds=1)

    start_of_three_hours_ago = start_of_current_hour - timedelta(hours=3)
    return isoformat(start_of_three_hours_ago), isoformat(end_of_previous_hour)

# TODO: is this needed??
# Use now's date to find start and end of previous week
#   Used for manual debugging and data verification
def get_previous_3_hours_dates():
    start_of_hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start_of_three_hours_ago = start_of_hour - timedelta(hours=3)
    end_of_three_hours_ago = start_of_three_hours_ago - timedelta(microseconds=1)
    start_of_six_hours_ago = start_of_three_hours_ago - timedelta(hours=3)

    return isoformat(start_of_six_hours_ago), isoformat(end_of_three_hours_ago)

# Use today's date to find start and end of previous week
#   Used for --recent --daily
def get_current_day_dates():
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1) - timedelta(microseconds=1)

    return isoformat(today_start), isoformat(today_end)

# Use today's date to find start and end of previous week
#   Used for --historical --daily
def get_previous_day_dates():
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today - timedelta(days=1)
    yesterday_end = today - timedelta(microseconds=1)

    return isoformat(yesterday_start), isoformat(yesterday_end)

# Use today's date to find start and end of previous week
#   Used for --recent --weekly
def get_current_week_dates():
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    current_week_start = today - timedelta(days=today.weekday())
    current_week_end = current_week_start + timedelta(days=7) - timedelta(microseconds=1)

    return isoformat(current_week_start), isoformat(current_week_end)


# Use today's date to find start and end of previous week
#   Used for --historical --weekly
def get_previous_week_dates():
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    previous_week_start = today - timedelta(days=7+today.weekday())
    previous_week_end = previous_week_start + timedelta(days=7) - timedelta(microseconds=1)

    return isoformat(previous_week_start), isoformat(previous_week_end)


# Use today's date to find start and end of previous week
#   Used for --recent --monthly
def get_current_month_dates():
    current_month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    current_month_end = current_month_start + relativedelta(months=1)

    return isoformat(current_month_start), isoformat(current_month_end)


# Use today's date to find start and end of previous week
#   Used for --historical --monthly
def get_previous_month_dates():
    current_month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_month_start = current_month_start - relativedelta(months=1)
    previous_month_end = current_month_start - timedelta(microseconds=1)

    return isoformat(previous_month_start), isoformat(previous_month_end)


# For simplicity, "season" is currently defined as a set of months
#   S1 => Spring => March / April / May
#   S2 => Summer => June / July / August
#   S3 => Fall => September / October / November
#   S4 => Winter => December / January / February
def get_seasonal_boundaries(date=datetime.now(UTC)):
    month_start = date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month = month_start.month
    year = month_start.year

    return {
        'spring': (datetime(year, 3, 1, tzinfo=UTC), datetime(year, 6, 1, tzinfo=UTC) - timedelta(microseconds=1)),
        'summer': (datetime(year, 6, 1, tzinfo=UTC), datetime(year, 9, 1, tzinfo=UTC) - timedelta(microseconds=1)),
        'autumn': (datetime(year, 9, 1, tzinfo=UTC), datetime(year, 12, 1, tzinfo=UTC) - timedelta(microseconds=1)),

        # Winter may use last year or next year
        #   month == 12 and day_of_month >= 21  =>  we are early in the winter, it will last until next year
        #   month != 12 =>  we are late in the winter, it has gone on since last year
        'winter': (
            datetime(year if month == 12 else year - 1, 12, 1, tzinfo=UTC),
            datetime(year + 1 if month == 12 else year, 3, 1, tzinfo=UTC) - timedelta(microseconds=1)
        ),
    }


# Given a date, return the name of the season that it falls into
def get_season(date=datetime.now(UTC)):
    seasonal_bounds = get_seasonal_boundaries(date=date)
    for season in seasonal_bounds:
        (season_start, season_end) = seasonal_bounds[season]
        if season_start <= date <= season_end:
            return season

    return None


# Compute today's timestamp, use that to find first microsecond of the previous season
def get_previous_season_dates():
    # Compute all seasonal boundaries
    seasonal_bounds = get_seasonal_boundaries()
    current_season = get_season()
    (current_season_start, current_season_end) = seasonal_bounds[current_season]

    # Arbitrarily subtract 2 days (any number > 1 will do)
    a_day_last_season = current_season_start - timedelta(days=2)
    previous_season = get_season(date=a_day_last_season)
    (previous_season_start, previous_season_end) = seasonal_bounds[previous_season]

    return isoformat(previous_season_start), isoformat(previous_season_end)


# Compute today's timestamp, use that to find first microsecond of the current season
def get_current_season_dates():
    current_month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    seasonal_bounds = get_seasonal_boundaries(date=current_month_start)
    current_season = get_season(date=current_month_start)
    (current_season_start, current_season_end) = seasonal_bounds[current_season]

    return isoformat(current_season_start), isoformat(current_season_end)


# Compute today's timestamp, use that to find first microsecond of the previous year
def get_previous_year_dates():
    current_year_start = datetime.now(UTC).replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_year_start  = current_year_start - relativedelta(years=1)
    previous_year_end  = current_year_start - timedelta(microseconds=1)

    return isoformat(previous_year_start), isoformat(previous_year_end)

# Compute today's timestamp, use that to find first microsecond of the current year
def get_current_year_dates():
    current_year_start = datetime.now(UTC).replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    current_year_end  = current_year_start + relativedelta(years=1) - timedelta(microseconds=1)

    return isoformat(current_year_start), isoformat(current_year_end)


def truncate(full_str: str|list[any], limit = 20):
    return f'{full_str[:limit]}...' if len(full_str) > limit else full_str

def run_r_script(scriptPath: str, inputFile: str, metricName: str, minObsPerHour: int):
    try:
        output = subprocess.check_output([
            'Rscript',
            scriptPath,
            metricName,
            inputFile,
            minObsPerHour
        ], universal_newlines=True, stderr=subprocess.PIPE)
        print(output.strip())

    except subprocess.CalledProcessError as ex:
        log.error('R script failed. Error:', ex.stderr)
        traceback.print_exc()
        sys.exit(500)
    except FileNotFoundError as ex:
        log.error('ERROR: File not found.', ex)
        traceback.print_exc()
        sys.exit(404)
    except Exception as ex:
        log.error('ERROR: Rscript encountered an unknown exception: ', ex)
        traceback.print_exc()
        sys.exit(501)


def merge_temporal_averages_to_df(metric_name, op_defn):
    # TODO: hard-coded paths? is this ok?
    renameColumns = op_defn['renameColumns'] if 'renameColumns' in op_defn else {}


    # Ensure column consistency: n_valid, type, date, is_valid, mean_pm25
    log.info(f'Compiling hourly sensor data...')
    hourly = pd.read_csv(f'data/{metric_name}-summary-hourly.csv').rename(columns=renameColumns)
    hourly['type'] = 'hour'

    # TODO: Use ISO 8601 Format indicating UTC timezone? browser needs special handling otherwise
    # Currently using something more akin to ISO 9705
    # hourly['date'] = hourly['date'].map(lambda d: dateutil.parser.isoparse(d).isoformat() + 'Z')

    # Ensure column consistency: n_valid, type, date, is_valid, mean_pm2
    log.info(f'Compiling daily sensor data...')
    daily = pd.read_csv(f'data/{metric_name}-summary-daily.csv').rename(columns=renameColumns)
    daily['type'] = 'day'

    # Ensure column consistency: n_valid, type, date, is_valid, mean_pm2
    log.info(f'Compiling weekly sensor data...')
    weekly = pd.read_csv(f'data/{metric_name}-summary-weekly.csv').rename(columns=renameColumns)
    weekly['type'] = 'week'

    # Ensure date in the correct format - 2025-W10, 2025-W09, etc
    weekly['date'] = weekly['date'].map(lambda d: d.split('-')[0] + '-W' + d.split('-')[1][1:].zfill(2))

    # Ensure column consistency: n_valid, type, date, is_valid, mean_pm2
    log.info(f'Compiling monthly sensor data...')
    monthly = pd.read_csv(f'data/{metric_name}-summary-monthly.csv').rename(columns=renameColumns)
    monthly['type'] = 'month'

    # Ensure date in the correct format - 2025-10, 2025-09, etc
    monthly['date'] = monthly['date'].map(lambda d: d.split('-')[0] + '-' + d.split('-')[1].zfill(2))

    # Ensure column consistency: n_valid, type, date, is_valid, mean_pm2
    log.info(f'Compiling seasonal sensor data...')
    seasonal = pd.read_csv(f'data/{metric_name}-summary-seasonal.csv').rename(columns=renameColumns)
    seasonal['type'] = 'season'

    # Ensure date in the correct format - 2025-winter, 2025-spring, etc
    #seasonal['date'] = seasonal['date'].map(lambda d: d.split('-')[0] + '-' + d.split('-')[1][1:].zfill(2))

    # Ensure column consistency: n_valid, type, date, is_valid, mean_pm2
    log.info(f'Compiling yearly sensor data...')
    yearly = pd.read_csv(f'data/{metric_name}-summary-yearly.csv').rename(columns=renameColumns)
    yearly['type'] = 'year'

    # Concatenate all rows of different types into single dataframe
    return pd.concat([yearly, seasonal, monthly, weekly, daily, hourly])


def redact(redactable: Any, key_name: str = '', limit = 20):
    if isinstance(redactable, dict):
        # Create a deep copy of input object
        if key_name == '':
            raise ValueError(
                'ERROR: redacting dictionary requires a key_name to redact. Provide a key_name to redact the value.')
        elif key_name in redactable:
            # key was found - redact the value
            return {**redactable, f'{key_name}': truncate(full_str=redactable[key_name], limit=limit)}
        else:
            return redactable  # key was not found - return noop
    if isinstance(redactable, str):
        # key_name ignored, since value is a string
        return truncate(full_str=redactable, limit=limit)
    else:
        raise TypeError(f'ERROR: unrecognized type - {type(redactable).__name__}')


def combine_dataset_rows(existing_df, data_to_merge):
    # Connect to an in-memory DuckDB database
    with duckdb.connect(database=':memory:') as con:
        con.execute("""
            CREATE OR REPLACE TABLE combined_metrics AS
            SELECT * FROM existing_df
            UNION BY NAME
            SELECT * FROM data_to_merge
        """)

        return con.execute("SELECT * FROM combined_metrics").fetchdf()



def merge_new_data(existing_df, data_to_merge):
    # Determine UNION of columns
    left_columns = [col for col in existing_df.columns.tolist() if col not in ['type','date']]
    right_columns = [col for col in data_to_merge.columns.tolist() if col not in ['type','date']]

    # Convert to set for uniqueness
    all_sensor_ids = list(set(left_columns + right_columns))
    all_sensor_ids.sort()

    # Loop over columns to build up our clauses
    # Coalesce all column values where possible
    sensor_col_names = ""
    coalesce_columns_clause = ""
    for id in all_sensor_ids:
        if id in left_columns and id in right_columns:
            sensor_col_names += f"COALESCE(r.{id}, l.{id}), "
            coalesce_columns_clause += f"{id} = COALESCE(s.{id}, t.{id}), "
        if id in left_columns and id not in right_columns:
            sensor_col_names += f"l.{id}, "
            coalesce_columns_clause += f"{id} = t.{id}, "
        if id not in left_columns and id in right_columns:
            sensor_col_names += f"r.{id}, "
            coalesce_columns_clause += f"{id} = s.{id}, "

    # Patch existing data with new data; returns union of both
    keys = ['type', 'date']
    existing_df = existing_df.set_index(keys)
    data_to_merge = data_to_merge.set_index(keys)
    merged_df = data_to_merge.combine_first(existing_df).reset_index()

    # Define custom categories for the "type" column, maintain this order
    custom_order = ['year', 'season', 'month', 'week', 'day', 'hour']  # order by least to most rows
    merged_df['type'] = pd.Categorical(merged_df['type'], categories=custom_order, ordered=True)
    merged_df['date'] = merged_df['date'].astype('str')

    # Sort by type, then reverse sort by date for optimal retrieval of latest metrics
    merged_df.sort_values(by=['type','date'], ascending=[False, False], inplace=True)
    return merged_df


def calculate_average(existing_df, start_date, end_date):
    # Connect to an in-memory DuckDB database
    with duckdb.connect(database=':memory:') as con:
        all_days = con.execute("SELECT * FROM existing_df where type='day'").fetchdf()

        return all_days


# Data cleanup process will be written in R
# It is likely that these cleanup scripts will expect CSV format
def read_csv(local_path: str, index_col=None):
    if index_col is not None:
        return pd.read_csv(local_path, index_col=index_col)
    return pd.read_csv(local_path)

def write_csv(local_path: str, df: DataFrame):
    df.to_csv(local_path)

def write_txt(local_path: str, data: str):
    with open(local_path, 'w') as f:
        f.write(data)


# Final pipeline output is likely to be JSON
# This ensures that final data is easily consumable by the frontend dashboard
def from_json(data: dict):
    return DataFrame(data)

def to_json(data: DataFrame):
    return data.to_json(orient='records')

def read_json_dict(path: str):
    with open(path, 'r') as f:
        return json.load(f)

def write_json_dict(path: str, data: dict):
    write_txt(path, data=json.dumps(data, indent=4, default=decimal_encoder))


# Likely needed to convert the cleaned data back to JSON
def convert_csv_to_json(path: str):
    base_path = Path(path).stem
    df = pd.read_csv(f'{base_path}.csv')
    df.to_json(f'{base_path}.json')


# Likely needed to convert the cleaned data back to JSON
# TODO: convert to 4-digit precision for latlong
def run_postprocessing(input_path: str, output_path: str):
    df = read_csv(input_path, index_col=0)
    return json.loads(to_json(df))


# Using a custom default function for json.dumps
def decimal_encoder(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")

def csv_to_geojson(fetch_time):
    df = pd.read_csv(f'{fetch_time}.csv')
    df.to_json(f'{fetch_time}.json')

def to_geojson(locations, properties, fetch_time):
    return {
        'type': 'FeatureCollection',
        # ISO Timestamp of when we fetched the data from Clarity
        'timestamp': fetch_time,
        'features': [
            {
                'type': 'Feature',
                'properties': properties[loc['datasourceId']],
                'geometry': {
                    'type': 'Point',
                    'coordinates': [ loc['lon'], loc['lat'] ]
                }
            } for loc in locations
        ]
    }


def to_geojson_simple(data, locations, fetch_time):
    # Read all metrics from each datasource into a map of properties
    properties = {}

    for d in data:
        # The datasourceId for this sensor in Clarity's system
        datasourceId = d["datasourceId"]

        # ISO Timestamp of when Clarity fetched the metric from the sensor
        # Assumption: all metrics are collected at the same time
        collection_time = d['time']

        # Multiple lines will share a datasourceId for different metrics at different timestamps
        metric_name, metric_value = d['metric'], d['value']

        # Initialize this entry if it's not already present
        datasource_properties = properties[datasourceId] if datasourceId in properties else {
                "time": collection_time,
                "datasourceId": datasourceId,

                # Initialize empty values to ensure consistent geojson features
                # each of these will be the "value" from entry where "metric" == key
                "pm10ConcMassNowcast": None,
                "pm10ConcMassNowcastUsEpaAqi": None,
                "pm2_5ConcMassNowcast": None,
                "pm2_5ConcMassNowcastUsEpaAqi": None
            }

        # Assumption: data will always be ordered newest -> oldest
        if datasource_properties[metric_name] is None and collection_time > datasource_properties['time']:
            # Overwrite particular metric from this line if we haven't seen a value, or if this value is later
            # No need to preserve/store "raw" or "status"
            datasource_properties['time'] = collection_time
            datasource_properties[metric_name] = metric_value

        properties[datasourceId] = datasource_properties

    # iterate over locations to fill in latlong coordinates
    return to_geojson(locations, properties, fetch_time)


def to_geojson_historical(data, locations, fetch_time):
    # Read all metrics from each datasource into a map of properties
    properties = {}
    for d in data:
        # The datasourceId for this sensor in Clarity's system
        datasourceId = d["datasourceId"]

        # ISO Timestamp corresponds to when Clarity fetched the metric from the sensor
        collection_time = d['time']

        # Multiple lines will share a datasourceId for different metrics at different timestamps
        metric_name, metric_value = d['metric'], d['value']

        # Initialize this entry if it's not already present
        datasource_properties = properties[datasourceId] if datasourceId in properties else {
                "datasourceId": datasourceId,

                # Initialize empty values to ensure consistent geojson features
                # each line of CSV becomes a key-value in one of these maps
                # each entry maps an ISO timestamp to the "value" of the metric collected at that timestamp
                "pm10ConcMassNowcast": {},
                "pm10ConcMassNowcastUsEpaAqi": {},
                "pm2_5ConcMassNowcast": {},
                "pm2_5ConcMassNowcastUsEpaAqi": {}
            }

        # Overwrite particular metric value from this line
        # No need to preserve/store "raw" or "status"
        datasource_properties[metric_name][collection_time] = metric_value

        # Every new line yields new data, so we always save
        properties[datasourceId] = datasource_properties

    # iterate over locations to fill in latlong coordinates
    return to_geojson(locations, properties, fetch_time)


