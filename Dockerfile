FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY core/ ./core/
COPY web/  ./web/
COPY healthcheck.py /

RUN mkdir -p data && chmod +x /healthcheck.py

VOLUME ["/app/data"]

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD /healthcheck.py

CMD ["python", "-u", "main.py"]
