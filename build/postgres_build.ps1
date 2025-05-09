# Define variables
$containerName_PG = "my_postgres"
$containerName_Admin = "my_pgadmin"
$dbPassword = $env:POSTGRES_PASSWORD
$sqlFilePath = "sql/create_database.sql"

# Remove any existing container with the same name
docker rm -f $containerName_PG
docker rm -f $containerName_Admin

# Run the PostgreSQL container
docker run --name $containerName_PG --network my_trading_network --ip 172.19.0.3 --restart unless-stopped -e POSTGRES_PASSWORD=$dbPassword -p 5432:5432 -d postgres:16.1

# Check if PostgreSQL is ready


# Check if PostgreSQL is ready
$pgReady = $false
while (-not $pgReady) {
    $pgReady = docker exec $containerName_PG pg_isready -U postgres | Select-String "accepting connections" -Quiet
    if (-not $pgReady) {
      Write-Host "Waiting for PostgreSQL to start..."
      Start-Sleep -Seconds 2   }
}

#Start-Sleep -Seconds 30

# Copy the SQL file into the container
docker cp $sqlFilePath "${containerName_PG}:/docker-entrypoint-initdb.d/script.sql"

# Execute the SQL file inside the container against the default 'postgres' database
docker exec -i $containerName_PG psql -U postgres -d postgres -f /docker-entrypoint-initdb.d/script.sql

# Run the pgAdmin container
docker run --name $containerName_Admin --network my_trading_network --ip 172.19.0.4 --restart unless-stopped -e PGADMIN_DEFAULT_EMAIL="agm12@duck.com" -e PGADMIN_DEFAULT_PASSWORD=$dbPassword -p 80:80 -d dpage/pgadmin4:latest
