#!/bin/bash

# Define variables
containerName_PG="my_postgres"
containerName_Admin="my_pgadmin"
dbPassword="$POSTGRES_PASSWORD"
sqlFilePath="sql/create_database.sql"

# Remove any existing container with the same name
docker rm -f $containerName_PG
docker rm -f $containerName_Admin

# Run the PostgreSQL container
docker run --name $containerName_PG --restart unless-stopped -e POSTGRES_PASSWORD=$dbPassword -p 5432:5432 -d postgres:16.1

# Check if PostgreSQL is ready
until docker exec $containerName_PG pg_isready -U postgres; do
  echo "Waiting for PostgreSQL to start..."
  sleep 2
done

# Copy the SQL file into the container
docker cp $sqlFilePath $containerName_PG:/docker-entrypoint-initdb.d/script.sql

# Execute the SQL file inside the container against the default 'postgres' database
docker exec -i $containerName_PG psql -U postgres -d postgres -f /docker-entrypoint-initdb.d/script.sql

docker run --name $containerName_Admin --restart unless-stopped -e PGADMIN_DEFAULT_EMAIL=agm12@duck.com -e PGADMIN_DEFAULT_PASSWORD=$dbPassword -p 80:80 -d dpage/pgadmin4:latest
