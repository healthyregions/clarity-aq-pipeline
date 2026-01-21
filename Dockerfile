FROM rocker/tidyverse
WORKDIR /home/rstudio

# Install necessary system dependencies and micromamba
RUN ln -s -f /usr/bin/python3 /usr/bin/python && \
    apt-get update && \
    apt-get install -y vim curl wget bzip2 ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install micromamba
RUN curl -L micro.mamba.pm/install.sh | bash -s -- -y
ENV PATH=$PATH:/root/.local/bin/

SHELL ["/bin/bash", "-c"]
#SHELL ["/bin/bash", "micromamba", "run", "-n", "base", "/bin/bash", "-c"]
# This initializes the "bash" shell for micromamba use in subsequent RUN commands

# Install conda dependenciess + Python and R scripts
COPY environment.yml .
RUN micromamba install -n base -f environment.yml -y && \
    micromamba clean --all --yes
COPY . .

# Install R dependencies
RUN R -e "install.packages(c('dplyr', 'slider', 'jsonlite', 'arrow'))"

# Mount in data volume at runtime
VOLUME /home/rstudio/data

ENTRYPOINT ["micromamba", "run", "-n", "base", "python", "./src/main.py"]
CMD ["--help"]
