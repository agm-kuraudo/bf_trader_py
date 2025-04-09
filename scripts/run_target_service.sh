#!/bin/bash

# Load variables from /etc/environment
if [ -f /etc/environment ]; then
    set -o allexport
    source /etc/environment
    set +o allexport
else
    echo "Error: /etc/environment file not found."
    exit 1
fi

# Get the script's directory
scriptDir=$(dirname "$(readlink -f "$0")")

# Define the relative path to the folder one level up
localFolder=$(realpath "$scriptDir/..")

# Define other variables
imageName="agm-karaudo/betfair_app_01:latest"
command="python /app/target_service.py"

# Run the Docker image, map the local folder, and execute the command
containerId=$(docker run -d -v "${localFolder}:/app" $imageName /bin/sh -c "$command")

# Wait for a few seconds to ensure the container is fully started
sleep 5

# Tail the logs of the container
docker logs -f $containerId
