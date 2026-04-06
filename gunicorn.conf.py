workers = 1
worker_class = "sync"
max_requests = 500         # Recycle worker after 500 requests to clear memory fragmentation
                           # (was 50 — too low, status polls alone burn through it mid-job)
max_requests_jitter = 50   # Randomize to avoid all workers restarting at once
timeout = 600              # 10 min — long enough for full processing pipeline
graceful_timeout = 30
