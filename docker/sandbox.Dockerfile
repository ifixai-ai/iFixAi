# Sandbox tool backend with enforced isolation (see docker/sandbox-compose.yml).
FROM python:3.12-slim

# curl exists only so you can prove the isolation from inside the container:
#   docker compose -f docker/sandbox-compose.yml exec sandbox curl -m 5 https://example.com
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY . /src
RUN pip install --no-cache-dir /src \
    # Bake in the default fixture; mount your own over /fixture.yaml to replace it.
    && cp /src/ifixai/fixtures/default/fixture.yaml /fixture.yaml \
    && rm -rf /src

WORKDIR /work
CMD ["ifixai", "sandbox", "--fixture", "/fixture.yaml", "--host", "0.0.0.0"]
