docker build -f betfair_app.dockerfile -t agm-karaudo/betfair_app_01 .

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Define the relative path to the folder one level up
$localFolder = Join-Path $scriptDir ".."

# Define other variables
$imageName = "agm-karaudo/betfair_app_01:latest"
$target_service_command = "python /app/target_service.py"
$monitor_service_command = "python /app/monitor_service.py"

# Run the Docker image, map the local folder, and execute the command
docker run -d --name bf_target_service --network my_trading_network --ip 172.19.0.5 -v ${localFolder}:/app $imageName /bin/sh -c "$target_service_command"

docker run -d --name bf_monitor_service --network my_trading_network --ip 172.19.0.6 -v ${localFolder}:/app $imageName /bin/sh -c "$monitor_service_command"
