# Official multi-architecture Python base; do not force amd64 on Apple Silicon.
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Use the maintained upstream package rather than an old third-party image.
RUN pip install --no-cache-dir volatility3==2.28.2 pefile==2024.8.26
COPY memory-processing/volatility_cli.py /usr/local/bin/vmdiff-volatility3
RUN chmod 755 /usr/local/bin/vmdiff-volatility3 \
    && ln -s /usr/local/bin/vmdiff-volatility3 /usr/local/bin/volatility3
RUN useradd --create-home --uid 5678 unprivileged
WORKDIR /memdiff
COPY memory-processing/memdiff.sh .
RUN mkdir -p volatilitycache results /disk /memory \
    && chown -R unprivileged:unprivileged /memdiff
USER unprivileged
ENTRYPOINT ["/bin/sh", "memdiff.sh"]
