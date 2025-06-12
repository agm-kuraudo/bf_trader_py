FROM arm64v8/debian:bookworm

# Switch to root user to ensure permissions
USER root

# Update package list
RUN apt-get update

# Install necessary dependencies including curl and gnupg
RUN apt-get install -y apt-transport-https ca-certificates gnupg lsb-release curl unzip wget

# Add Docker's official GPG key
RUN curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# Set up the Docker repository for Debian "bullseye" (Debian 11)
RUN echo "deb [arch=arm64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian bullseye stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

# Update package list again
RUN apt-get update

# Install Docker CLI
RUN apt-get install -y docker-ce-cli || { echo "Failed to install Docker CLI"; exit 1; }

# Install OpenJDK 11 JRE
RUN apt-get install -y default-jre || { echo "Failed to install default-jre"; exit 1; }

# Install Rundeck dependencies
RUN apt-get install -y openssh-client uuid-runtime || { echo "Failed to install dependencies"; exit 1; }

# Download and install Rundeck 5.10.0
RUN wget https://packagecloud.io/pagerduty/rundeck/packages/any/any/rundeck_5.10.0.20250312-1_all.deb/download.deb?distro_version_id=35 -O /tmp/rundeck.deb && \
    dpkg -i /tmp/rundeck.deb && \
    rm /tmp/rundeck.deb

# Clean up
RUN apt-get clean && rm -rf /var/lib/apt/lists/*

# Add rundeck user and group, and add rundeck to docker group
# Add rundeck user and group, and add rundeck to docker group if not exists
# Ensure docker group exists and add rundeck user to docker group
RUN groupadd docker || true && useradd -m -s /bin/bash rundeck || true && usermod -aG docker rundeck

# Create a directory for the PID file with appropriate permissions
RUN mkdir -p /var/run/rundeck && chown rundeck:rundeck /var/run/rundeck
# Set environment variables for Rundeck
ENV RUNDECK_SERVER_FORWARDED=true

# Expose the necessary port
EXPOSE 4440

# Switch back to Rundeck user
USER rundeck

# Define the entrypoint using rundeckd script
#java -jar "/var/lib/rundeck/bootstrap/rundeck-5.10.0-20250312.war"
ENTRYPOINT ["java", "-jar", "/var/lib/rundeck/bootstrap/rundeck-5.10.0-20250312.war"]

# Define the command to run Rundeck
CMD ["-p", "4440"]