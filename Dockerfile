FROM python:3-slim
WORKDIR /usr/app

# Install Python deps/scripts
COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir
COPY . .

CMD ["python", "./fetch_clarity_data.py"]