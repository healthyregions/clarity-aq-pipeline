FROM rocker/tidyverse
WORKDIR /home/rstudio

# Install R dependencies
RUN R -e "install.packages(c('dplyr', 'slider', 'jsonlite', 'arrow'))"

# TODO: Install R dependencies? anything needed?
# Install R scripts
COPY clarity_qa_qc.R .

# Mount in data volume at runtime
VOLUME /home/rstudio/data

# TODO: create placeholder data cleanup script
ENTRYPOINT ["Rscript", "./clarity_qa_qc.R"]
CMD []
