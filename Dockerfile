FROM python:3.11-slim
WORKDIR /opt/wargame
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY config ./config
RUN useradd --create-home evaluator && mkdir -p data results && chown -R evaluator:evaluator /opt/wargame
USER evaluator
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
