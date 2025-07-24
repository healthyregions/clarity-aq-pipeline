import os
import pandas as pd
import sys


# Read configuration from environment variables
LP_MASTER_PATH = os.getenv('LP_MASTER_PATH', 'data/cleaned/LightPostMaster_Cleaned.csv')
# GRID_PATH = os.getenv('GRID_PATH', './data/cleaned/GridMaster.csv')
# CANDIDATE_SENSORS_PATH = os.getenv('CANDIDATE_SENSORS_PATH', './data/cleaned/CandidateSensorswUID.csv')

INPUT_DATA_PATH = os.getenv('INPUT_PATH', './data/no-community-input.xlsx')
OUTPUT_PATH = os.getenv('OUTPUT_PATH', './data/merged-priority-lightpost-data.xlsx')


# Given a row, this function will return True if the chosen column in that row matches the value
def col_val_match(row, col_name, match_value):
    return row[col_name] == match_value


# Read input CSV / Excel files as dataframe
def read_input_data():
    # Read relevant input files from disk
    lp_data_df = pd.read_csv(LP_MASTER_PATH)
    input_data_df = pd.read_excel(INPUT_DATA_PATH)
    # grid_data = pd.read_csv(GRID_PATH)
    # candidate_data = pd.read_csv(CANDIDATE_SENSORS_PATH)

    return input_data_df, lp_data_df


def main():
    print('Parsing input data...')
    input_data_df, lp_data_df = read_input_data()

    print('Input data parsed:')
    print(input_data_df)

    merged_data_df = pd.DataFrame().reindex_like(input_data_df)

    # Fill in missing rows from other spreadsheets
    for index, row in input_data_df.iterrows():
        # Read attributes from this row
        sensor_id = row['EPA Grid Point ID']
        zip_code = row['Zipcode']
        comm_area_no = row['Community Area Number']
        ward_no = row['Ward Number']

        # Use attributes to look up priority 1-5 FEATURE_ID. geocode/latlong, and address
        for priority in range(1, 6):
            # Build label names for each priority
            feature_id_col = f'Priority {priority} CDOT Light Pole ID'
            geocode_col = f'Priority {priority} CDOT Light Pole Location Geocode'
            address_col = f'Priority {priority} CDOT Light Pole Address'

            # Look up these values in LP_MASTER
            match_row = None
            for _, r in lp_data_df.iterrows():
                # Use Candidate == p to locate matching priority sensor
                if (col_val_match(r, 'sensorID', sensor_id) and
                        col_val_match(r, 'Candidate', priority) and
                        col_val_match(r, 'ComArea', comm_area_no) and
                        col_val_match(r, 'Ward', ward_no) and
                        col_val_match(r, 'Zip', zip_code)):
                    match_row = r
                    break

            if match_row is None:
                print(f'ERROR: No match found for sensorID={sensor_id} Candidate={priority} CommArea={comm_area_no} Ward={ward_no} Zip={zip_code}')
                print('Verify column names before re-running:')
                print(lp_data_df)
                print('Aborting...')
                sys.exit(1)

            # We have found match_row, our matching row from lp_data
            row[feature_id_col] = match_row['FEATURE_ID']
            row[address_col] = match_row['Address']

            # XXX: SensorID == 800 does not have priority latlong, and other latlong are same for all priority 1-5
            # instead, build up latlong for each from Latitude/Longitude columns, which differ for 1-5 as expected
            row[geocode_col] = f'{match_row["Latitude"]},{match_row["Longitude"]}'

        print(f'Row data has been filled out for {sensor_id}:')
        print(row)

        # Add this row back to the merged_data dataframe
        merged_data_df.loc[index] = row

    print(f'Merged data has been compiled!')
    print(merged_data_df)

    # Write merged spreadsheet out as a new file
    print(f'Writing merged data to {OUTPUT_PATH}')
    merged_data_df.to_excel(OUTPUT_PATH, index=False)

    return


if __name__ == "__main__":
    main()

