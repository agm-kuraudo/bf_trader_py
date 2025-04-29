# Use the official Rundeck image as the base
FROM rundeck/rundeck:5.10.0

# Switch to root user to ensure permissions
USER root

# Install Docker CLI
RUN apt-get update && \
    apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release && \
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt-get update && \
    apt-get install -y docker-ce-cli && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Add rundeck user to docker group
RUN groupadd docker && usermod -aG docker rundeck

# Set environment variables for Rundeck
ENV RUNDECK_SERVER_FORWARDED=true

# Expose the necessary port
EXPOSE 4440

# Switch back to Rundeck user
USER rundeck

# Define the entrypoint using rundeckd script
ENTRYPOINT ["/usr/bin/rundeckd"]

# Define the command to run Rundeck
CMD ["-p", "4440"]

#docker rm -f some-rundeck; docker run --name some-rundeck -p 4440:4440 -v data:/home/rundeck/server/data -e DOCKER_HOST=tcp://host.docker.internal:2375 agm-karaudo/rundeck-image-01
