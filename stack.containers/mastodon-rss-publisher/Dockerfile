FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY stack.containers/mastodon-rss-publisher/publisher.py /app/publisher.py
CMD ["python", "/app/publisher.py"]
