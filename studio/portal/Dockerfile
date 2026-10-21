FROM python:3.12-slim

# Build context is the repository root.
COPY studio/portal/ /app/
COPY MyomaReviewLite/MyomaReviewLiteLib/ /lib/MyomaReviewLite/MyomaReviewLiteLib/

ENV PYTHONPATH=/app:/lib/MyomaReviewLite
ENV PYTHONUNBUFFERED=1

EXPOSE 9000
CMD ["python", "/app/studio_portal.py", "serve", "--data-dir", "/portal", "--data-root", "/data", "--readers-root", "/readers", "--port", "9000"]
