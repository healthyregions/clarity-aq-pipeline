FROM python:3-slim

# Install PYthon deps/scripts
COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir
COPY . .


CMD ["python", "./fetch_clarity_data.py"]