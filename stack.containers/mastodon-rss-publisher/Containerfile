FROM docker.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY stack.containers/mastodon-rss-publisher/publisher.py /app/publisher.py
RUN groupadd --gid 10001 publisher && \
    useradd --uid 10001 --gid 10001 --home-dir /nonexistent --no-create-home publisher && \
    mkdir -p /state /configs/mastodon-rss/feeds.d && \
    chown -R publisher:publisher /app /state /configs/mastodon-rss
USER publisher
CMD ["python", "/app/publisher.py"]
