FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .
COPY . .

# a API roda sem root: a pasta das fotos de perfil precisa ser do appuser (o volume herda o dono)
RUN useradd --create-home appuser \
    && mkdir -p /app/media \
    && chown appuser:appuser /app/media
USER appuser

EXPOSE 8000
# PORT vem da hospedagem (Railway); 1 worker de propósito: agendador e limite de tentativas vivem no processo.
# Atrás de proxy, defina FORWARDED_ALLOW_IPS (lido pelo uvicorn) para valerem o IP real e o https.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --workers 1"]
