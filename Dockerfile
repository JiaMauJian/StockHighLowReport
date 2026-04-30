FROM python:3.11-slim
WORKDIR /app
COPY requirements_ga.txt .
RUN pip install --no-cache-dir -r requirements_ga.txt
COPY scripts/update_turso.py scripts/
CMD ["python", "scripts/update_turso.py"]
