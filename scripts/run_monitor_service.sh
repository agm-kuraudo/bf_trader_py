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

localFolder="/usr/local/bf_trader_py"

# Define other variables
imageName="agm-karaudo/betfair_app_01:latest"
command="python /app/monitor_service.py"

# Run the Docker image, map the local folder, and execute the command
containerId=$(docker run -d -v "${localFolder}:/app" $imageName /bin/sh -c "$command")

# Wait for a few seconds to ensure the container is fully started
sleep 5

# Tail the logs of the container
docker logs -f $containerId
