FROM python:3.12-slim

# Build context is the repository root. The portal is standard library only.
COPY slicer-studio/portal/ /app/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 9000
CMD ["python", "/app/studio_portal.py", "serve", "--data-dir", "/portal", "--users-root", "/workspaces", "--port", "9000"]
