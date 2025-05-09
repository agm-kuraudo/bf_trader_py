#!/bin/bash

# Build the Docker image
docker build -f betfair_app.dockerfile -t agm-karaudo/betfair_app_01 .

# Get the directory of the script
scriptDir="$(dirname "$(readlink -f "$0")")"

# Define the relative path to the folder one level up
localFolder="$(realpath "$scriptDir/..")"

# Define other variables
imageName="agm-karaudo/betfair_app_01:latest"
target_service_command="python /app/target_service.py"
monitor_service_command="python /app/monitor_service.py"

docker rm -f bf_target_service
docker rm -f bf_monitor_service

# Run the Docker image, map the local folder, and execute the command
docker run -d --name bf_target_service --network my_trading_network --ip 172.19.0.5 -v "${localFolder}:/app" "${imageName}" /bin/sh -c "${target_service_command}"

docker run -d --name bf_monitor_service --network my_trading_network --ip 172.19.0.6 -v "${localFolder}:/app" "${imageName}" /bin/sh -c "${monitor_service_command}"
