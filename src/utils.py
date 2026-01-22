import json
from decimal import Decimal

import pandas as pd
from pandas import DataFrame
from pathlib import Path

# TODO: Currently unused, but one idea for the near future
from enum import Enum
class OperationPeriod(Enum):
    YEARLY = 1
    MONTHLY = 2
    WEEKLY = 3
    DAILY = 4
    HOURLY = 5

    SEASONAL = 6
    # Add new periods here
    # THIRTY_MIN = 6
    # FIFTEEN_MIN = 7

def truncate(full_str: str, limit = 20):
    return f'{full_str[:limit]}...' if len(full_str) > limit else full_str

def redact(redactable: any, key_name: str = '', limit = 20):
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


