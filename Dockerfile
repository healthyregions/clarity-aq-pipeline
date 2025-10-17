FROM python:3
WORKDIR /usr/app

# Install Python deps/scripts
COPY requirements.txt .
RUN pip install -r requirements.txt --user --no-cache-dir
COPY . .

# Mount in data volume at runtime
VOLUME /usr/app/data/

ENTRYPOINT ["python", "./src/main.py"]
CMD ["--help"]
