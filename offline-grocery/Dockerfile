FROM python:3.12-alpine
RUN pip install --no-cache-dir pywebpush==2.0.3 py-vapid
WORKDIR /app
COPY server.py /app/
COPY static /app/static
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status==200 else 1)"
CMD ["python", "/app/server.py"]
