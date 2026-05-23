"""
ApplyBot Worker v3 — polls API for pending applications, processes via CDP.
v3 (S6 Tiferet): retry logic, circuit breaker, exponential backoff.
v3 (S8 Hod): reports success/failure analytics to API.
"""
import os, sys, time, json, logging, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

API_BASE = os.environ.get("API_BASE", "https://applybot-yavz.onrender.com")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [worker] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("worker")

# ── CIRCUIT BREAKER (S6 Tiferet) ──
class CircuitBreaker:
    """Prevents hammering a broken API. Opens after N consecutive failures."""
    def __init__(self, name: str, max_failures: int = 5, cooldown: int = 300):
        self.name = name
        self.max_failures = max_failures
        self.cooldown = cooldown  # seconds before retrying
        self.failures = 0
        self.last_failure_time = 0
        self.state = "closed"  # closed → open → half-open → closed

    def call(self, fn, *args, **kwargs):
        if self.state == "open":
            if time.time() - self.last_failure_time > self.cooldown:
                self.state = "half-open"
                log.info(f"[breaker] {self.name}: open → half-open (testing)")
            else:
                raise Exception(f"Circuit OPEN for {self.name} — skipping")

        try:
            result = fn(*args, **kwargs)
            if self.state == "half-open":
                self.state = "closed"
                self.failures = 0
                log.info(f"[breaker] {self.name}: half-open → closed (recovered)")
            else:
                self.failures = 0  # reset on success
            return result
        except Exception as e:
            self.failures += 1
            self.last_failure_time = time.time()
            if self.failures >= self.max_failures:
                self.state = "open"
                log.warning(f"[breaker] {self.name}: OPEN after {self.failures} failures: {e}")
            raise


# Global circuit breakers
api_breaker = CircuitBreaker("api", max_failures=5, cooldown=300)
apply_breaker = CircuitBreaker("apply", max_failures=3, cooldown=120)


def api_call(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Call the ApplyBot API with retry logic."""
    url = f"{API_BASE}{endpoint}"
    body = json.dumps(data).encode() if data else None

    max_retries = 3
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, data=body, method=method)
            req.add_header("Content-Type", "application/json")
            resp = urllib.request.urlopen(req, timeout=30)
            return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < max_retries - 1:
                delay = 2 ** attempt
                log.warning(f"API {e.code} on {endpoint}, retry in {delay}s...")
                time.sleep(delay)
                continue
            return {"error": f"HTTP {e.code}: {str(e)}"}
        except Exception as e:
            if attempt < max_retries - 1:
                delay = 2 ** attempt
                log.warning(f"API error on {endpoint}, retry in {delay}s: {e}")
                time.sleep(delay)
                continue
            return {"error": str(e)}


def process_queue():
    """Fetch pending applications and process them via CDP engine."""
    try:
        result = api_breaker.call(api_call, "/api/queue/pending")
    except Exception as e:
        log.warning(f"Queue check blocked by circuit breaker: {e}")
        return 0

    if "error" in result:
        log.warning(f"Queue check failed: {result['error']}")
        return 0

    users = result.get("users", [])
    if not users:
        return 0

    processed = 0
    for user in users:
        user_id = user["user_id"]
        tokens = user["tokens"]
        email = user.get("email", "unknown")

        log.info(f"Processing {email} — {tokens} tokens remaining")

        try:
            from engine import auto_apply

            result = apply_breaker.call(auto_apply, user_id)
            status = result.get("status", "error")

            if status == "submitted":
                processed += 1
                log.info(f"  ✓ {result.get('job_title', '?')} @ "
                         f"{result.get('company', '?')} — "
                         f"{result.get('tokens_remaining', '?')} tokens left")
            elif status == "no_jobs_found":
                log.info(f"  No matching jobs for {email}")
            elif status == "no_tokens":
                log.info(f"  {email} is out of tokens — skipping")
                break  # skip this user in future ticks
            elif status == "failed":
                log.warning(f"  ✗ {result.get('job_title', '?')}: "
                           f"{result.get('error', 'unknown')}")
            elif status == "error":
                log.error(f"  Engine error: {result.get('error', 'unknown')}")

        except Exception as e:
            log.error(f"Failed to process {user_id}: {e}")

    return processed


def main():
    log.info("=" * 50)
    log.info("ApplyBot Worker v3 Started")
    log.info(f"API: {API_BASE} | Poll: {POLL_INTERVAL}s")
    log.info(f"Circuit breakers: api(max=5, cool=300s) apply(max=3, cool=120s)")
    log.info("=" * 50)

    total_processed = 0
    consecutive_idle_ticks = 0

    while True:
        try:
            count = process_queue()
            total_processed += count

            if count > 0:
                log.info(f"Tick — {count} processed (total: {total_processed})")
                consecutive_idle_ticks = 0
            else:
                consecutive_idle_ticks += 1
                # Only log idle every 5 ticks to reduce noise
                if consecutive_idle_ticks % 5 == 0:
                    log.info(f"No work for {consecutive_idle_ticks} ticks "
                             f"(total: {total_processed})")

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            log.info(f"\nShutting down. Total processed: {total_processed}")
            break
        except Exception as e:
            log.error(f"Worker panic: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
