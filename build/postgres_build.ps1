# Define variables
$containerName = "my_postgres"
$dbPassword = $env:POSTGRES_PASSWORD
$sqlFilePath = "sql/create_database.sql"

# Pull the PostgreSQL image
docker rm -f my_postgres

# Run the PostgreSQL container
docker run --name $containerName --restart unless-stopped -e POSTGRES_PASSWORD=$dbPassword -p 5432:5432 -d postgres:16.1

# Wait for the PostgreSQL server to start
Start-Sleep -Seconds 10

# Copy the SQL file into the container
docker cp $sqlFilePath ${containerName}:/docker-entrypoint-initdb.d/script.sql

# Execute the SQL file inside the container against the default 'postgres' database
docker exec -i $containerName psql -U postgres -d postgres -f /docker-entrypoint-initdb.d/script.sql
