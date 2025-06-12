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

# Start the Docker container
docker start bf_monitor_service

# Loop until the container is running
while [[ "$(docker inspect -f '{{.State.Running}}' bf_monitor_service)" != "true" ]]; do
   echo "Waiting for bf_monitor_service to start..."
   sleep 1
done

echo "bf_monitor_service is now running."

# Tail the logs of the container
docker logs --since 10s -f bf_monitor_service
