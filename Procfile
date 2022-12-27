web: sanic app.app --host=0.0.0.0 --port=8080 --workers=3 --access-log
celery_worker: celery -A worker:app worker --loglevel=info --concurrency=1
