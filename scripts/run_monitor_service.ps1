# Start the bf_monitor_service container
docker start bf_monitor_service

# Loop until the container is running
while ((docker inspect -f '{{.State.Running}}' bf_monitor_service) -ne $true) {
    Write-Output "Waiting for bf_monitor_service to start..."
    Start-Sleep -Seconds 1
}

Write-Output "bf_monitor_service is now running."

# Tail the logs of the container
docker logs --since 10s -f bf_monitor_service
