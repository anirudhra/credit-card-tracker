FROM python:3.13-slim

WORKDIR /app

COPY app.py /app/app.py
COPY vendor_engine.py /app/vendor_engine.py
COPY static /app/static

ENV HOST=0.0.0.0
ENV PORT=8795
ENV CREDIT_CARD_TRACKER_DATA_DIR=/app/data

VOLUME ["/app/data"]
EXPOSE 8795

CMD ["python3", "/app/app.py"]
