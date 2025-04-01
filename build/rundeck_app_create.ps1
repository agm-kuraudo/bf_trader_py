$envVar = [System.Environment]::GetEnvironmentVariable("vault_unseal_keys")

# Get the script's directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Output "scriptDir: $scriptDir"

# Define the relative path to the folder one level up
$localFolder = Join-Path $scriptDir ".."

docker rm -f some-rundeck

# Replace the placeholder with the actual value

$dockerCommand = "docker run --name some-rundeck --restart unless-stopped -e vault_unseal_keys='$envVar' -p 4440:4440 -v $scriptDir/run_deck_data:/home/rundeck/server/data -v ${localFolder}:/home/rundeck/app -e DOCKER_HOST=tcp://host.docker.internal:2375 agm-karaudo/rundeck-image-01"

Write-Output "dockerCommand: $dockerCommand"

# Run the Docker command in a hidden window
Start-Process -FilePath "powershell" -ArgumentList "-Command $dockerCommand" -WindowStyle Hidden -RedirectStandardOutput "output.log" -RedirectStandardError "error.log"

#docker update --restart unless-stopped some-rundeck