# Get the script's directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Define the relative path to the folder one level up
$localFolder = Join-Path $scriptDir ".."

# Define other variables
$imageName = "agm-karaudo/betfair_app_01:latest"
$command = "python /app/monitor_service.py"

# Run the Docker image, map the local folder, and execute the command
$containerId = docker run -d -v ${localFolder}:/app $imageName /bin/sh -c "$command"

# Wait for a few seconds to ensure the container is fully started
Start-Sleep -Seconds 5

# Tail the logs of the container
docker logs -f $containerId