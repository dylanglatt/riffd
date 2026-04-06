workers = 1
worker_class = "sync"
max_requests = 50          # Recycle worker after 50 requests to clear memory fragmentation
max_requests_jitter = 10   # Randomize to avoid all workers restarting at once
timeout = 600              # 10 min — long enough for full processing pipeline
graceful_timeout = 30
