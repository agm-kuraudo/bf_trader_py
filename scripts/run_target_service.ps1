docker start bf_target_service


# Loop until the container is running
while ((docker inspect -f '{{.State.Running}}' bf_target_service) -ne $true) {
   Write-Output "Waiting for bf_target_service to start..."
   Start-Sleep -Seconds 1
}

Write-Output "bf_target_service is now running."
# Tail the logs of the container
docker logs -f bf_target_service