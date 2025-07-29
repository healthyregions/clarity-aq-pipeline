FROM python:3-slim
WORKDIR /usr/app

# Install Python deps/scripts
COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir
COPY . .

# Mount in data volume at runtime
VOLUME /usr/app/data/

CMD ["python", "./gather.py"]