#!/bin/bash
set -e

# Create data directory if it doesn't exist
# We do this manually here so that the folder is owned by the correct user
# NOTE: if Docker daemon creates this folder automatically, you can see file permission issues
#   Workaround is to delete the ./data folder and create it manually using mkdir
mkdir -p ./data/

# Starting MinIO container
echo "Ensuring MinIO container is running..."
docker compose --profile minio up -d --remove-orphans

# Clear out stale data from previous runs before running again
# TODO: make this an option?
echo -n "Clearing any previous run data."
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

