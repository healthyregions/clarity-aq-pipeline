library(dplyr)
library(lubridate)
library(tidyr)
library(purrr)
library(slider)
library(arrow)

# Read configuration from environment variables

log_level <- Sys.getenv("LOGLEVEL", unset = "info")
raw_minute_output_dir <- Sys.getenv("CLEANED_OUTPUT_DIR", unset = "./data/")

# Read OUTPUT_FORMAT envvar - possible values are json, csv, parquet (default)
output_format <- tolower(Sys.getenv("OUTPUT_FORMAT", unset = "csv"))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) == 0) {
    cat("ERROR: Missing required argument.\n", sep = "")
    cat("   Please specify input CSV as first script argument\n", sep = "")
    quit(status=-1)
}

raw_input_path <- args[2]
output_path <- args[3]

# Hourly data
# Keep the hours only if it has >=3 valid minute recordings
# Meet the completeness criterion (completeness >= 75%)
min_obs_per_hour <- as.integer(args[4])

cat("Reading from input file: ",  raw_input_path,"\n", sep = "")

# Script expects per-minute data in csv-wide format
df <- readr::read_csv(raw_input_path)

# Step 1
# PM2.5 measurements
df1 <- df %>%
  filter(
    (pm2_5ConcMassIndividual.raw >= 0 & pm2_5ConcMassIndividual.raw <= 500) |
      is.na(pm2_5ConcMassIndividual.raw)
  )

# Relative humidity (RH)
df2 <- df1 %>%
  filter(
    (relHumidInternalIndividual.raw >= 0 & relHumidInternalIndividual.raw <= 100) |
      is.na(relHumidInternalIndividual.raw)
  )

# Temperature
df3 <- df2 %>%
  filter(
    (temperatureInternalIndividual.raw >= -20 & temperatureInternalIndividual.raw <= 140) |
      is.na(temperatureInternalIndividual.raw)
  )

# Standard Deviation
# package: slider
df4 <- df3 %>%
  arrange(datasourceId, sourceId, time) %>%
  group_by(datasourceId, sourceId) %>%
  mutate(
    rolling_sd = slide_dbl(
      pm2_5ConcMassIndividual.raw,
      ~ sd(.x, na.rm = TRUE),
      .before = 5, .after = 5,
      .complete = FALSE
    )
  ) %>%
  ungroup() %>%
  filter(is.na(rolling_sd) | rolling_sd != 0) %>%
  select(-rolling_sd)

# Single-point missing value imputation
# Compare before/after average vs. 10 point average
fill_single_na <- function(x, diff_thr = 0.05) {
  n <- length(x)
  idx <- which(is.na(x))
  if (length(idx) == 0) return(x)

  for (i in idx) {
    # Both neighbors exist and are non-NA
    if (i - 1 >= 1 && i + 1 <= n && !is.na(x[i - 1]) && !is.na(x[i + 1])) {
      ba_avg <- mean(c(x[i - 1], x[i + 1]))  # Before/after average

      # 10-point window (5 before + current + 5 after)
      left_idx  <- max(1, i - 5)
      right_idx <- min(n, i + 5)
      win_vals  <- x[left_idx:right_idx]
      win_avg   <- mean(win_vals, na.rm = TRUE)

      # Impute if the difference is smaller than threshold
      if (is.finite(ba_avg) && is.finite(win_avg) && win_avg != 0) {
        rel_diff <- abs(ba_avg - win_avg) / abs(win_avg)
        if (rel_diff <= diff_thr) {
          x[i] <- ba_avg
        }
      }
    }
  }
  x
}

# Haversine distance
haversine_km <- function(lat1, lon1, lat2, lon2, R = 6371) {
  to_rad <- pi / 180
  dlat <- (lat2 - lat1) * to_rad
  dlon <- (lon2 - lon1) * to_rad
  a <- sin(dlat/2)^2 + cos(lat1*to_rad) * cos(lat2*to_rad) * sin(dlon/2)^2
  c <- 2 * atan2(sqrt(a), sqrt(1 - a))
  R * c
}

# Precompute nearest neighbor clusters for each sensor (default k = 3)
build_nearest_neighbors <- function(df, k = 3) {
  # Extract unique sensor locations
  sensors <- df %>%
    distinct(datasourceId, sourceId, locationLatitude, locationLongitude) %>%
    mutate(key = paste(datasourceId, sourceId, sep = "||"))

  # Compute pairwise distances between sensors
  pairs <- sensors %>%
    rename(lat1 = locationLatitude, lon1 = locationLongitude, key1 = key) %>%
    select(key1, lat1, lon1) %>%
    crossing(
      sensors %>%
        rename(lat2 = locationLatitude, lon2 = locationLongitude, key2 = key) %>%
        select(key2, lat2, lon2)
    ) %>%
    filter(key1 != key2) %>%
    mutate(dist_km = haversine_km(lat1, lon1, lat2, lon2))

  # Select k nearest neighbors for each sensor
  nn <- pairs %>%
    group_by(key1) %>%
    slice_min(dist_km, n = k, with_ties = FALSE) %>%
    summarise(neighbors = list(key2), .groups = "drop")

  nn
}

# Fill missing chunks using neighbor sensors (mean at same timestamps)
fill_chunks_with_neighbors <- function(gdf, nn_tbl, df_all, diff_thr = 0.05) {

  key <- paste(first(gdf$datasourceId), first(gdf$sourceId), sep = "||")
  vals <- gdf$pm2_5ConcMassIndividual.raw
  n    <- length(vals)
  is_na <- is.na(vals)
  if (!any(is_na)) return(gdf)

  r <- rle(is_na)
  ends <- cumsum(r$lengths)
  starts <- ends - r$lengths + 1

  # Get neighbor list for this sensor
  neighbors <- nn_tbl$neighbors[nn_tbl$key1 == key][[1]]
  if (length(neighbors) == 0) return(gdf)

  for (k in seq_along(r$values)) {
    # Missing chunks (length of NA >= 2)
    if (isTRUE(r$values[k]) && r$lengths[k] >= 2) {
      s <- starts[k]; e <- ends[k]
      ts_seq <- gdf$time[s:e]

      # Neighbor mean at the same timestamps
      nb_means <- map_dbl(ts_seq, ~ {
        df_all %>%
          mutate(key = paste(datasourceId, sourceId, sep = "||")) %>%
          filter(key %in% neighbors, time == .x) %>%
          summarise(m = mean(pm2_5ConcMassIndividual.raw, na.rm = TRUE)) %>%
          pull(m) %>%
          { if (length(.) == 0) NA_real_ else . }
      })

      # Compare nearest valid edge with neighbor mean
      edge_before <- if (s - 1 >= 1) vals[s - 1] else NA_real_
      edge_after  <- if (e + 1 <= n) vals[e + 1] else NA_real_
      edge_value  <- if (!is.na(edge_before)) edge_before else edge_after

      nb_ref <- mean(nb_means, na.rm = TRUE)
      ok <- is.finite(edge_value) && is.finite(nb_ref) && nb_ref != 0 &&
        (abs(edge_value - nb_ref) / abs(nb_ref) <= diff_thr)

      if (ok) {
        # Fill missing values only when neighbor means are available
        for (j in seq_along(ts_seq)) {
          if (is.na(vals[s + j - 1]) && is.finite(nb_means[j])) {
            vals[s + j - 1] <- nb_means[j]
          }
        }
      }
    }
  }
  gdf$pm2_5ConcMassIndividual.raw <- vals
  gdf
}

# QA/QC for PM2.5 data
qa_qc_pm25 <- function(df, k_neighbors = 3, diff_thr = 0.05) {
  stopifnot(all(c("datasourceId","sourceId","time",
                  "locationLatitude","locationLongitude",
                  "pm2_5ConcMassIndividual.raw") %in% names(df)))

  # Overall missing rate
  overall_miss_rate <- mean(is.na(df$pm2_5ConcMassIndividual.raw))
  message(sprintf("Overall missing rate = %.2f%%", 100 * overall_miss_rate))

  # Missing rate < 5%: remove missing values directly
  if (overall_miss_rate < 0.05) {
    out <- df %>% filter(!is.na(pm2_5ConcMassIndividual.raw)) %>%
      mutate(impute_flag = "drop_na_lt5")
    return(out)
  }

  # Missing rate >=20%: no imputation
  if (overall_miss_rate >= 0.20) {
    out <- df %>% mutate(impute_flag = "no_impute_ge20")
    return(out)
  }

  # Missing rate: 5–20%: build nearest neighbor clusters
  nn_tbl <- build_nearest_neighbors(df, k = k_neighbors) %>%
    rename(key1 = key1)

  # Sort by sensor and time
  df_sorted <- df %>%
    arrange(datasourceId, sourceId, time)

  # Fill single missing points (before/after mean vs. 10-point window mean)
  step1 <- df_sorted %>%
    group_by(datasourceId, sourceId) %>%
    mutate(pm2_5ConcMassIndividual.raw =
             fill_single_na(pm2_5ConcMassIndividual.raw, diff_thr = diff_thr)) %>%
    ungroup()

  # Fill missing chunks using neighbor cluster means
  df_all <- step1 %>% select(datasourceId, sourceId, time,
                             pm2_5ConcMassIndividual.raw)

  step2 <- step1 %>%
    group_by(datasourceId, sourceId) %>%
    group_modify(~ fill_chunks_with_neighbors(.x, nn_tbl = nn_tbl,
                                              df_all = df_all,
                                              diff_thr = diff_thr)) %>%
    ungroup() %>%
    mutate(impute_flag = ifelse(is.na(impute_flag), "impute_5to20", impute_flag))

  step2
}

# QA/QC/imputation
df5 <- qa_qc_pm25(df4)
na_rate_before <- mean(is.na(df$pm2_5ConcMassIndividual.raw))
na_rate_before
na_rate_after <- mean(is.na(df5$pm2_5ConcMassIndividual.raw))
na_rate_after
table(df5$impute_flag)

# Step 2
# Calibrated by Relative Humidity (RH)
# Create the calibrated Relative Humidity (RH): pm2_5ConcMassIndividual.calibrated
# This dataset does not include ambient RH entry, so we use the internal individual raw data
df6 <- df5 %>%
  mutate(
    pm2_5ConcMassIndividual.calibrated = case_when(
      pm2_5ConcMassIndividual.raw < 343 ~
        0.524 * pm2_5ConcMassIndividual.raw -
        0.0862 * relHumidInternalIndividual.raw + 5.75,
      pm2_5ConcMassIndividual.raw >= 343 ~
        0.46 * pm2_5ConcMassIndividual.raw +
        0.000393 * (pm2_5ConcMassIndividual.raw)^2 + 2.97
    )
  )

# Step 3
df0 <- df6 %>%
  mutate(time = ymd_hms(time, quiet = TRUE)) %>%
  arrange(sourceId, time)

# Choose pm2.5ConcMass Individual raw data
pm_col <- "pm2_5ConcMassIndividual.raw"

# Hourly data
# Keep the hours only if it has >=3 valid minute recordings
# Meet the completeness criterion (completeness >= 75%)
min_obs_per_hour <- 3

# Compute hourly stats for ALL hours
# Date Format:  2026-01-01 00:00:00, 2026-01-01 01:00:00, etc
hourly <- df0 %>%
  mutate(date = floor_date(time, unit = "hour")) %>%
  group_by(datasourceId, sourceId, date) %>%
  summarise(
    n_valid = sum(!is.na(.data[[pm_col]])),
    type = "hour",
    mean_pm25 = mean(.data[[pm_col]], na.rm = TRUE),
    .groups = "drop"
  ) %>%
  mutate(is_valid = n_valid >= min_obs_per_hour)

# Keep only valid hour rows
# Keep hours with n_obs ≥ 3 (completeness ≥ 75%)
cat("Hourly rows (all): ", nrow(hourly), "\n")
df_hourly <- hourly %>%
  filter(is_valid) %>%
  select(datasourceId, sourceId, date, mean_pm25, n_valid, is_valid) %>%
  mutate(type = "hour")
cat("Valid hourly rows (>=75% completeness): ", nrow(df_hourly), "\n")
print(df_hourly)


# Daily data
# Daily aggregation data using only valid hours
daily <- df_hourly %>%
  # Date Format:  2026-01-01, 2026-01-02, etc
  mutate(date = as_date(date)) %>%
  group_by(datasourceId, sourceId, date) %>%
  summarise(
    n_valid = n(),                     # Count of valid hours
    type = "day",
    mean_pm25 = mean(mean_pm25, na.rm = TRUE),  # Mean of valid hourly means
    .groups = "drop"
  ) %>%
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
    mean_pm25 = mean(mean_pm25, na.rm = TRUE),  # Mean of valid daily means
    .groups = "drop"
  ) %>%
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
    mean_pm25 = mean(mean_pm25, na.rm = TRUE),  # Mean of valid daily means
    .groups = "drop"
  ) %>%
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
    mean_pm25 = mean(mean_pm25, na.rm = TRUE),  # Mean of valid daily means
    .groups = "drop"
  ) %>%
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
    mean_pm25 = mean(mean_pm25, na.rm = TRUE),  # Mean of valid daily means
    .groups = "drop"
  ) %>%
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
summary_completeness_file <- paste(raw_minute_output_dir, "summary-completeness.", output_format, sep = "")
summary_combined_file <- output_path

# Merge all rows into a single dataframe
df_combined_final <- rbind(df_yearly, df_seasonal)
df_combined_final <- rbind(df_combined_final, df_monthly)
df_combined_final <- rbind(df_combined_final, df_weekly)
df_combined_final <- rbind(df_combined_final, df_daily)
df_combined_final <- rbind(df_combined_final, df_hourly)


# Write the chosen format to disk
cat("Writing as OUTPUT_FORMAT=", output_format, " format...\n", sep = "")
if (output_format == "parquet") {
    #write_parquet(x = sensor_hourly_comp, sink = summary_completeness_file)
    write_parquet(x = df_combined_final, sink = summary_combined_file)
} else if (output_format == "csv") {
    #write.csv(sensor_hourly_comp, summary_completeness_file, row.names = FALSE)
    write.csv(df_combined_final, summary_combined_file, row.names = FALSE)
} else if (output_format == "json") {
    #jsonlite::write_json(sensor_hourly_comp, summary_completeness_file, pretty = FALSE)
    jsonlite::write_json(df_combined_final, summary_combined_file, pretty = FALSE)
} else {
    cat("Skipping writing unknown file format: \"", output_format, "\"\n", sep = "")
}

cat("Data cleanup finished successfully!\n")