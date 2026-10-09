FROM python:3.12-slim

RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app:app /data
WORKDIR /app
COPY app/ /app/app/
# The vision chain imports the T2 core and the T3b adapter: ship the candidate
# modules only, not the public contract test folders.
COPY core_t2/photo_compare_t2_candidate.py /app/core_t2/
COPY core_t3/photo_compare_t3_candidate.py /app/core_t3/

USER app
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "app.bot"]
