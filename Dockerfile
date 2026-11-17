# One image for every role: the command decides what the container is
# (serve, process, schedule, deliver, migrate). See docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# A regular (non-editable) install: the package lands in site-packages and
# the sources are not needed at run time.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[service]" && rm -rf /app/src /app/pyproject.toml /app/README.md

RUN useradd --system --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin duebook
USER duebook

EXPOSE 8000
ENTRYPOINT ["duebook"]
CMD ["serve"]
