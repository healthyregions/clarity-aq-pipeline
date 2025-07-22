import os
import pandas
import sys


# Read configuration from environment variables
LP_MASTER_PATH = os.getenv('LP_MASTER_PATH', 'data/cleaned/LightPostMaster_Cleaned.csv')
GRID_PATH = os.getenv('GRID_PATH', './data/cleaned/GridMaster.csv')
CANDIDATE_SENSORS_PATH = os.getenv('CANDIDATE_SENSORS_PATH', './data/cleaned/CandidateSensorswUID.csv')
INPUT_DATA_PATH = os.getenv('INPUT_DATA_PATH', './data/no-community-input.xlsx')

OUTPUT_PATH = os.getenv('OUTPUT_PATH', './data/merged-no-community-input.xlsx')

# Read all input files from disk
lp_data = pandas.read_csv(LP_MASTER_PATH)
#grid_data = pandas.read_csv(GRID_PATH)
#candidate_data = pandas.read_csv(CANDIDATE_SENSORS_PATH)
input_data = pandas.read_excel(INPUT_DATA_PATH)




# Given a pandas dataframe, search for the row matching the predicate
#    -If col_name is given, return the row cell value for this column
#    -If no col_name is given, the entire row is returned
def lookup_value(df, predicate, src_col_name, target_col_name=None):
    for row in df:
        match_value = row[src_col_name]

        # If col_name given, return the cell
        # If no col_name given, return the whole row
        if predicate(row=row, col_name=src_col_name, match_value=match_value):
            return row[target_col_name] if target_col_name is not None else row

    return None


# Given a row, this function will return True if the chosen column in that row matches the value
def col_val_match(row, col_name, match_value):
    return row[col_name] == match_value


def main():
    lookup_value(input_data, col_val_match, 'col_name')

    # Fill in missing rows from other spreadsheets
    for row in input_data:
        # Read attributes from this row
        sensor_id = row['EPA Grid Point ID']
        zip_code = row['zip']
        comm_area_no = row['commArea']
        ward_no = row['wardNo']

        # Use attributes to look up priority 1-5 FEATURE_ID. geocode/latlong, and address
        for priority in range(1, 5):
            # Build label names for each priority
            feature_id_col = f'Priority {priority} CDOT Light Pole ID'
            geocode_col = f'Priority {priority} CDOT Light Pole Location Geocode'
            address_col = f'Priority {priority} CDOT Light Pole Address'

            # Look up these values in LP_MASTER
            match_row = None
            for r in lp_data:
                # Use Candidate == p to locate matching priority sensor
                if (col_val_match(r, 'sensorID', sensor_id) and
                        col_val_match(r, 'Candidate', priority) and
                        col_val_match(r, 'commArea', comm_area_no) and
                        col_val_match(r, 'wardNo', ward_no) and
                        col_val_match(r, 'zip', zip_code)):
                    match_row = r
                    break

            if match_row is None:
                sys.exit(1)

            # We have found match_row, our matching row from lp_data
            row[feature_id_col] = match_row['FEATURE_ID']
            row[geocode_col] = match_row['latlong']
            row[address_col] = match_row['address']

    # Write merged spreadsheet out as a new file
    input_data.to_excel(OUTPUT_PATH)

    return


if __name__ == "__main__":
    main()

