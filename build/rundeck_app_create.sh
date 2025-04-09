#!/bin/bash

# Get the environment variable
envVar=$(printenv vault_unseal_keys)

# Get the script's directory
scriptDir=$(dirname "$(readlink -f "$0")")

echo "scriptDir: $scriptDir"

# Define the relative path to the folder one level up
localFolder=$(dirname "$scriptDir")

# Build the Docker image from the Dockerfile
docker build -t agm-karaudo/rundeck-image-01 -f "$scriptDir/rundeck_appv2.dockerfile" "$scriptDir"

# Remove any existing container named some-rundeck
docker rm -f some-rundeck

# Replace the placeholder with the actual value
dockerCommand="docker run --name some-rundeck --restart unless-stopped -e vault_unseal_keys='$envVar' -p 4440:4440 -v $scriptDir/run_deck_data:/home/rundeck/server/data -v $localFolder:/home/rundeck/app agm-karaudo/rundeck-image-01"

echo "dockerCommand: $dockerCommand"

# Run the Docker command
eval $dockerCommand

# Uncomment the following line if you want to update the restart policy
# docker update --restart unless-stopped some-rundeck
