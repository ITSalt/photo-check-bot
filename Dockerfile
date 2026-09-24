FROM python:3.12-slim

RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app:app /data
WORKDIR /app
COPY app/ /app/app/

USER app
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "app.bot"]
