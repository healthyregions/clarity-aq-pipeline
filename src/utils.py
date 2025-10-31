import json
from decimal import Decimal

import pandas as pd
from pandas import DataFrame, value_counts
from pathlib import Path


# Shorthand helper functon for setting or defaulting a variable value
def set_or_default(value, default):
    return value if value else default


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
        return str(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def to_geojson(data, locations, fetch_time):
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
        if datasource_properties[metric_name] is None:
            # Overwrite particular metric from this line if we haven't seen a value 
            # No need to preserve/store "raw" or "status"
            datasource_properties['time'] = collection_time
            datasource_properties[metric_name] = metric_value

        properties[datasourceId] = datasource_properties

    # iterate over locations to fill in latlong coordinates
    return {
        'type': 'FeatureCollection',
        # ISO Timestamp of when we fetched the data from Clarity
        'timestamp': fetch_time,
        'features': [
            {
                'type': 'Feature',
                'properties': properties[location['datasourceId']],
                'geometry': {
                    'type': 'Point',
                    'coordinates': [
                        Decimal(location['lon']).quantize(Decimal('0.0000')),
                        Decimal(location['lat']).quantize(Decimal('0.0000'))
                    ]
                }
            } for location in locations
        ]
    }

