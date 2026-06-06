FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip build && \
    python -m build --wheel --outdir /wheels

FROM python:3.12-slim
LABEL org.opencontainers.image.title="moodle-mcp" \
      org.opencontainers.image.description="MCP server for Moodle developer documentation" \
      org.opencontainers.image.source="https://github.com/SaadRahman01/moodle-mcp" \
      org.opencontainers.image.licenses="MIT"
RUN useradd --create-home --uid 1000 mcp
USER mcp
WORKDIR /home/mcp
COPY --from=build /wheels/*.whl /tmp/
RUN pip install --user --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
ENV PATH="/home/mcp/.local/bin:${PATH}"
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["moodle-mcp"]
