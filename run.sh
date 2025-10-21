#!/bin/bash
set -e

# Clear out stale data from previous runs before running again
# TODO: make this an option?
echo -n "Clearing previous run data."
sleep 1s
echo -n "."
rm -rf ./data/*
sleep 1s
echo ". Done!"

# Build container
echo "Building container image(s)..."
docker compose --profile pipeline build

# Fetch raw sensor data from Clarity REST API v2
echo "Running clarityfetch container from built image..."
docker compose run --remove-orphans -it clarityfetch

# TODO: Process raw sensor metrics into final cleaned data
echo "this is when data cleanup would normally run..."
echo "so we're just going to copy the raw sensor output instead"
echo "Continuing in 5 seconds..."
echo -n "5 "
sleep 1s
echo -n "4 "
sleep 1s
echo -n "3 "
sleep 1s
echo -n "2 "
sleep 1s
echo "1"
sleep 1s

# Push cleaned result data to S3
echo "Running s3push container from built image..."
docker compose run --remove-orphans -it s3push

