FROM python:3.14-slim

WORKDIR /app

RUN pip install --no-cache-dir 'python-dotenv>=1.0,<2' 'tzdata>=2024.1'

COPY ha_commutes.py /app/ha_commutes.py

ENTRYPOINT ["python", "/app/ha_commutes.py", "--state", "/app/data/ha-commutes.state.json"]
