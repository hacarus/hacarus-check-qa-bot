FROM python:3.11-slim

# Claude Code の CLI は claude-agent-sdk に同梱されているので Node.js は不要
RUN useradd --create-home --uid 10001 qabot
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY config ./config

USER qabot
ENV REPO_PATH=/repo \
    DB_PATH=/data/qa_bot.sqlite3 \
    PRICING_PATH=/app/config/pricing.toml \
    SYSTEM_PROMPT_PATH=/app/config/system_prompt.md

ENTRYPOINT ["qa-bot"]
CMD ["slack"]
