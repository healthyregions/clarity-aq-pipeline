library(dplyr)
library(lubridate)
library(tidyr)
library(purrr)
library(slider)
library(arrow)

# Read configuration from environment variables

log_level <- Sys.getenv("LOGLEVEL", unset = "info")
raw_minute_output_dir <- Sys.getenv("CLEANED_OUTPUT_DIR", unset = "./data/")

# Read OUTPUT_FORMAT envvar - possible values are json, csv (default), parquet
output_format <- tolower(Sys.getenv("OUTPUT_FORMAT", unset = "csv"))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) {
    cat("ERROR: Missing required argument.\n", sep = "")
    cat("   Please specify input CSV as first script argument\n", sep = "")
    quit(status=-1)
}

metric_name <- args[1]
input_path <- args[2]
output_path <- args[3]
min_obs_per_hour <- as.integer(args[4])

col_name <- "pm2_5ConcMassNowcastUsEpaAqi.value"

# Compute hourly stats for ALL hours
# Date Format:  2026-01-01 00:00:00, 2026-01-01 01:00:00, etc
cat("Reading from input file: ",  input_path,"\n", sep = "")
hourly <- readr::read_csv(input_path) %>%
  mutate(date = floor_date(endOfPeriod, unit = "hour")) %>%
  group_by(datasourceId, sourceId, date) %>%
  summarise(
    n_valid = sum(!is.na(.data[[col_name]])),
    type = "hour",
    metric = mean(.data[[col_name]], na.rm = TRUE),
    .groups = "drop"
  ) %>%
  mutate(is_valid = n_valid >= min_obs_per_hour)

# Keep only valid hour rows
# Keep hours with n_obs ≥ 3 (completeness ≥ 75%)
cat("Hourly rows (all): ", nrow(hourly), "\n")
df_hourly <- hourly %>%
  filter(is_valid) %>%
  select(datasourceId, sourceId, date, metric, n_valid, is_valid) %>%
  mutate(type = "hour") %>%
  rename_at("metric", ~ metric_name)
cat("Valid hourly rows (>=75% completeness): ", nrow(df_hourly), "\n")
print(df_hourly)

# Daily data
# Daily aggregation data using only valid hours
daily <- df_hourly %>%
  # Date Format:  2026-01-01, 2026-01-02, etc
  group_by(datasourceId, sourceId, date) %>%
  mutate(date = floor_date(date, unit = "day")) %>%
  summarise(
    n_valid = n(),                     # Count of valid hours
    type = "day",
    metric = mean(get(metric_name), na.rm = TRUE),  # Mean of valid hourly means
    .groups = "drop"
  ) %>%
  rename_at("metric", ~ metric_name) %>%
  mutate(is_valid = n_valid > 20) # >20 hours

# Keep only valid days
cat("Daily rows (all): ", nrow(daily), "\n")
df_daily <- daily %>% mutate(type = "day") %>% filter(is_valid)
cat("Valid daily rows (>20 valid hours): ", nrow(df_daily), "\n")
if (nrow(df_daily) > 0) {
    print(df_daily)
}


# TODO: get real average code from UIC
# stuff below is pretty much just made up


# Weekly aggregation data using only valid days
weekly <- df_daily %>%
  # Date Format: 2026-W01, 2026-W02, etc
  mutate(date = paste(lubridate::isoyear(date), sprintf("%02d", lubridate::isoweek(date)),sep = "-W")) %>%
  group_by(datasourceId, sourceId, date) %>%
  summarise(
    n_valid = n(),                     # Count of valid days
    type = "week",
    metric = mean(get(metric_name), na.rm = TRUE),  # Mean of valid daily means
    .groups = "drop"
  ) %>%
  rename_at("metric", ~ metric_name) %>%
  mutate(is_valid = n_valid > 5) # >5 days

# Keep only valid weeks
cat("Weekly rows (all): ", nrow(weekly), "\n")
df_weekly <- weekly %>% mutate(type = "week") %>% filter(is_valid)
cat("Valid weekly rows (>5 valid days):" , nrow(df_weekly), "\n")
if (nrow(df_weekly) > 0) {
    print(df_weekly)
}


# Monthly aggregation data using only valid days
# Date Format:  2026-01, 2026-02, etc
monthly <- df_daily %>%
  mutate(date = paste(lubridate::year(date), lubridate::month(date), sep = "-")) %>%
  group_by(datasourceId, sourceId, date) %>%
  summarise(
    n_valid = n(),                     # Count of valid days
    type = "month",
    metric = mean(get(metric_name), na.rm = TRUE),  # Mean of valid daily means
    .groups = "drop"
  ) %>%
  rename_at("metric", ~ metric_name) %>%
  mutate(is_valid = n_valid > 21) # >21 days

# Keep only valid months
cat("Monthly rows (all): ", nrow(monthly), "\n")
df_monthly <- monthly %>% mutate(type = "month") %>% filter(is_valid)
cat("Valid monthly rows (>21 valid days): ", nrow(df_monthly), "\n")
if (nrow(df_monthly) > 0) {
    print(df_monthly)
}

# Seasonal aggregation data using only valid days
# Date Format:  2026-summer, 2026-spring, etc
seasonal <- df_daily %>%
  mutate(date = case_match(month(floor_date(date, "month")),
     1 ~ paste(lubridate::year(date), "winter", sep = "-"),
     2 ~ paste(lubridate::year(date), "winter", sep = "-"),
     3 ~ paste(lubridate::year(date), "spring", sep = "-"),
     4 ~ paste(lubridate::year(date), "spring", sep = "-"),
     5 ~ paste(lubridate::year(date), "spring", sep = "-"),
     6 ~ paste(lubridate::year(date), "summer", sep = "-"),
     7 ~ paste(lubridate::year(date), "summer", sep = "-"),
     8 ~ paste(lubridate::year(date), "summer", sep = "-"),
     9 ~ paste(lubridate::year(date), "autumn", sep = "-"),
     10 ~ paste(lubridate::year(date), "autumn", sep = "-"),
     11 ~ paste(lubridate::year(date), "autumn", sep = "-"),
     12 ~ paste(lubridate::year(date)+1, "winter", sep = "-"))
  ) %>% group_by(datasourceId, sourceId, date) %>%
  summarise(
    n_valid = n(),                     # Count of valid days
    type = "season",
    metric = mean(get(metric_name), na.rm = TRUE),  # Mean of valid daily means
    .groups = "drop"
  ) %>%
  rename_at("metric", ~ metric_name) %>%
  mutate(is_valid = n_valid > 60) # >60 days

# Keep only valid seasons
cat("Seasonal rows (all): ", nrow(seasonal), "\n")
df_seasonal <- seasonal %>% mutate(type = "season") %>% filter(is_valid)
cat("Valid seasonal rows (>60 valid days): ", nrow(df_seasonal), "\n")
if (nrow(df_seasonal) > 0) {
    print(df_seasonal)
}

# Yearly aggregation data using only valid days
# Date Format:  2026, 2025, etc
yearly <- df_daily %>%
  mutate(date = lubridate::year(floor_date(date))) %>%
  group_by(datasourceId, sourceId, date) %>%
  summarise(
    n_valid = n(),                     # Count of valid days
    type = "year",
    metric = mean(get(metric_name), na.rm = TRUE),  # Mean of valid daily means
    .groups = "drop"
  ) %>%
  rename_at("metric", ~ metric_name) %>%
  mutate(is_valid = n_valid > 250) # >250 days

# Keep only valid years
cat("Yearly rows (all): ", nrow(yearly), "\n")
df_yearly <- yearly %>% mutate(type = "year") %>% filter(is_valid)
cat("Valid yearly rows (>220 valid days): ", nrow(df_yearly), "\n")
if (nrow(df_yearly) > 0) {
    print(df_yearly)
}

# Per-sensor completeness summary
# For each sensor
# hours_total: total number of hourly records (including invalid hours)
# hours_valid: number of hours that meet the completeness criterion (i.e., n_obs >= min_obs_per_hour,so is_valid_hour == TRUE)
# pct_valid: percentage of valid hours out of total hours
sensor_hourly_comp <- hourly %>%
  group_by(datasourceId, sourceId) %>%
  summarise(
    hours_total = n(),
    hours_valid = sum(is_valid),
    pct_valid   = 100 * hours_valid / pmax(hours_total, 1),
    .groups = "drop"
  )

cat("Sensor completeness summary:", nrow(sensor_hourly_comp), "sensors\n")
print(head(sensor_hourly_comp))

# Build up file name based on dataframe name + output_format (default=csv)
# summary_completeness_file <- "summary-completeness.csv"

# Merge all rows into a single dataframe
# df_combined_final <- rbind(df_yearly, df_seasonal)
# df_combined_final <- rbind(df_combined_final, df_monthly)
# df_combined_final <- rbind(df_combined_final, df_weekly)
# df_combined_final <- rbind(df_combined_final, df_daily)
# df_combined_final <- rbind(df_combined_final, df_hourly)
df_combined_final <- rbind(df_daily, df_hourly)

# Write the chosen format to disk
cat("Writing as OUTPUT_FORMAT=", output_format, " format: ", output_path, "\n", sep = "")
if (output_format == "parquet") {
    write_parquet(x = df_combined_final, sink = output_path)
} else if (output_format == "csv") {
    write.csv(df_combined_final, output_path, row.names = FALSE)
} else if (output_format == "json") {
    jsonlite::write_json(df_combined_final, output_path, pretty = FALSE)
} else {
    cat("Skipping writing unknown file format: \"", output_format, "\"\n", sep = "")
}

cat("Data cleanup finished successfully!\n")