#!/bin/bash

# Start the Docker container
docker start bf_target_service

# Loop until the container is running
while [[ "$(docker inspect -f '{{.State.Running}}' bf_target_service)" != "true" ]]; do
   echo "Waiting for bf_target_service to start..."
   sleep 1
done

echo "bf_target_service is now running."

# Tail the logs of the container
docker logs -f bf_target_service
