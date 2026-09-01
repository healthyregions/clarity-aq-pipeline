FROM rocker/tidyverse
WORKDIR /home/rstudio

# Install OS dependencies + stom helpful tools
RUN ln -s -f /usr/bin/python3 /usr/bin/python && \
    apt-get update && \
    apt-get install -y vim curl wget bzip2 ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
    
# Copy the Lambda Web Adapter binary into your container
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.8.4 /lambda-adapter /opt/extensions/lambda-adapter

# Port the Web Adapter will route traffic to inside your container (default is 8080)
ENV PORT=8080
EXPOSE 8080

# Install micromamba
RUN curl -L micro.mamba.pm/install.sh | bash -s -- -y
ENV PATH=$PATH:/root/.local/bin/

# Use micromamba in subsequent RUN commands
SHELL ["micromamba", "run", "-n", "base", "/bin/bash", "-c"]

# Install R dependencies
RUN R -e "install.packages(c('dplyr', 'slider', 'jsonlite', 'arrow'))"

# Install conda dependencies
COPY environment.yml ./environment.yml
RUN micromamba install -n base -f environment.yml -y && \
    micromamba clean --all --yes

# Include our Python and R source
COPY src ./src
COPY scripts ./scripts
COPY operations.yml ./operations.yml

# Mount in data volume at runtime
VOLUME /home/rstudio/data
ENTRYPOINT ["micromamba", "run", "-n", "base", "python", "./src/main.py"]
CMD ["--help"]
