FROM rocker/rstudio
WORKDIR /usr/app

# TODO: Install R dependencies? anything needed?
# Install R scripts
COPY ./*.R .

# Mount in data volume at runtime
VOLUME /home/rstudio/data

# TODO: create placeholder data cleanup script
CMD ["Rscript", "./data-cleanup.R"]