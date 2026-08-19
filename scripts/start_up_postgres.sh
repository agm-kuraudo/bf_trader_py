#!/bin/bash

# Start PostgreSQL and pgAdmin containers
docker start my_postgres

# Wait for PostgreSQL to be ready
sleep 5

docker start my_pgadmin
