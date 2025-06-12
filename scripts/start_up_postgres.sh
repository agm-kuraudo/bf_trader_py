#!/bin/bash

# Start the first Docker container
docker start some-postgres

# Wait for 5 seconds
sleep 5

# Start the second Docker container
docker start quirky_germain