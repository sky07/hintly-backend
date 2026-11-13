"""Token Bucket Rate Limiter for Gemini API.

Implements a token bucket algorithm to ensure we stay within Gemini's
free tier limits (5 requests/minute for testing, 15 for production).
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Any
from collections import deque
import hashlib

logger = logging.getLogger(__name__)


@dataclass
class RateLimiterConfig:
    """Configuration for the rate limiter."""
    requests_per_minute: int = 4  # Default to 4 RPM for free tier (with buffer)
    burst_capacity: int = 2  # Allow small bursts
    queue_max_size: int = 10  # Max queued requests
    request_timeout: float = 30.0  # Timeout for queued requests


class TokenBucket:
    """Token bucket rate limiter.
    
    Allows bursts up to bucket capacity while maintaining average rate.
    """
    
    def __init__(self, rate: float, capacity: int):
        """
        Args:
            rate: Tokens per second (requests per second)
            capacity: Maximum bucket size (burst capacity)
        """
        self.rate = rate  # tokens per second
        self.capacity = capacity
        self.tokens = capacity  # Start full
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()
        
        logger.info(f"[RATE] TokenBucket initialized: {rate:.3f} tokens/sec, capacity={capacity}")
    
    async def acquire(self, timeout: Optional[float] = None) -> bool:
        """Try to acquire a token.
        
        Args:
            timeout: Max time to wait for a token (None = no wait)
            
        Returns:
            True if token acquired, False otherwise
        """
        start_time = time.monotonic()
        
        while True:
            async with self._lock:
                now = time.monotonic()
                # Refill tokens based on time elapsed
                elapsed = now - self.last_update
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_update = now
                
                if self.tokens >= 1:
                    self.tokens -= 1
                    logger.debug(f"[RATE] Token acquired. Remaining: {self.tokens:.2f}")
                    return True
                
                # Calculate wait time for next token
                wait_time = (1 - self.tokens) / self.rate
            
            # Check timeout
            if timeout is not None:
                elapsed_total = time.monotonic() - start_time
                if elapsed_total >= timeout:
                    logger.warning(f"[RATE] Token acquisition timed out after {elapsed_total:.1f}s")
                    return False
                wait_time = min(wait_time, timeout - elapsed_total)
            
            logger.debug(f"[RATE] Waiting {wait_time:.2f}s for token...")
            await asyncio.sleep(wait_time)
    
    @property
    def available_tokens(self) -> float:
        """Get current available tokens (approximate)."""
        now = time.monotonic()
        elapsed = now - self.last_update
        return min(self.capacity, self.tokens + elapsed * self.rate)


@dataclass
class QueuedRequest:
    """A request waiting in the queue."""
    id: str
    func: Callable
    args: tuple
    kwargs: dict
    future: asyncio.Future
    created_at: float = field(default_factory=time.monotonic)


class RateLimitedExecutor:
    """Executes async functions with rate limiting and queuing.
    
    Features:
    - Token bucket rate limiting
    - Request queuing when rate limited
    - Timeout handling
    - Status callbacks for UI feedback
    """
    
    def __init__(
        self,
        config: Optional[RateLimiterConfig] = None,
        on_status_change: Optional[Callable[[str, dict], None]] = None,
    ):
        self.config = config or RateLimiterConfig()
        self.on_status_change = on_status_change
        
        # Calculate tokens per second from requests per minute
        tokens_per_second = self.config.requests_per_minute / 60.0
        self.bucket = TokenBucket(tokens_per_second, self.config.burst_capacity)
        
        self._queue: deque[QueuedRequest] = deque(maxlen=self.config.queue_max_size)
        self._processing = False
        self._processor_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Stats
        self.total_requests = 0
        self.rate_limited_requests = 0
        self.queue_drops = 0
        
        logger.info(f"[RATE] RateLimitedExecutor initialized: {self.config.requests_per_minute} RPM")
    
    async def start(self):
        """Start the queue processor."""
        self._running = True
        self._processor_task = asyncio.create_task(self._process_queue())
        logger.info("[RATE] Queue processor started")
    
    async def stop(self):
        """Stop the queue processor."""
        self._running = False
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
        
        # Cancel any pending requests
        while self._queue:
            req = self._queue.popleft()
            req.future.cancel()
        
        logger.info("[RATE] Queue processor stopped")
    
    async def execute(
        self,
        func: Callable,
        *args,
        request_id: Optional[str] = None,
        **kwargs,
    ) -> Any:
        """Execute a function with rate limiting.
        
        If rate limited, the request is queued and executed when a token
        becomes available.
        
        Args:
            func: Async function to execute
            *args: Positional arguments
            request_id: Optional ID for tracking
            **kwargs: Keyword arguments
            
        Returns:
            The function's return value
            
        Raises:
            asyncio.TimeoutError: If request times out in queue
            Exception: Any exception from the function
        """
        self.total_requests += 1
        request_id = request_id or f"req_{self.total_requests}"
        
        # Try immediate execution
        if await self.bucket.acquire(timeout=0.1):
            logger.info(f"[RATE] Request {request_id} executing immediately")
            self._notify_status("executing", {"request_id": request_id})
            return await func(*args, **kwargs)
        
        # Queue the request
        self.rate_limited_requests += 1
        logger.info(f"[RATE] Request {request_id} queued (rate limited). Queue size: {len(self._queue)}")
        
        future = asyncio.get_event_loop().create_future()
        request = QueuedRequest(
            id=request_id,
            func=func,
            args=args,
            kwargs=kwargs,
            future=future,
        )
        
        try:
            self._queue.append(request)
        except Exception:
            # Queue is full (maxlen reached, oldest dropped)
            self.queue_drops += 1
            logger.warning(f"[RATE] Queue full, oldest request dropped. Total drops: {self.queue_drops}")
        
        self._notify_status("queued", {
            "request_id": request_id,
            "queue_position": len(self._queue),
            "estimated_wait": len(self._queue) * 60 / self.config.requests_per_minute,
        })
        
        try:
            return await asyncio.wait_for(future, timeout=self.config.request_timeout)
        except asyncio.TimeoutError:
            logger.warning(f"[RATE] Request {request_id} timed out in queue")
            self._notify_status("timeout", {"request_id": request_id})
            raise
    
    async def _process_queue(self):
        """Background task to process queued requests."""
        logger.info("[RATE] Queue processor running")
        
        try:
            while self._running:
                if not self._queue:
                    await asyncio.sleep(0.1)
                    continue
                
                # Wait for a token
                await self.bucket.acquire()
                
                if not self._queue:
                    continue
                
                # Process next request
                request = self._queue.popleft()
                
                # Check if request already timed out
                if request.future.done():
                    continue
                
                logger.info(f"[RATE] Processing queued request {request.id}")
                self._notify_status("executing", {"request_id": request.id})
                
                try:
                    result = await request.func(*request.args, **request.kwargs)
                    request.future.set_result(result)
                except Exception as e:
                    request.future.set_exception(e)
                
        except asyncio.CancelledError:
            logger.info("[RATE] Queue processor cancelled")
    
    def _notify_status(self, status: str, data: dict):
        """Notify status change callback."""
        if self.on_status_change:
            try:
                self.on_status_change(status, data)
            except Exception as e:
                logger.error(f"[RATE] Status callback error: {e}")
    
    def get_stats(self) -> dict:
        """Get rate limiter statistics."""
        return {
            "total_requests": self.total_requests,
            "rate_limited_requests": self.rate_limited_requests,
            "queue_drops": self.queue_drops,
            "queue_size": len(self._queue),
            "available_tokens": self.bucket.available_tokens,
            "requests_per_minute": self.config.requests_per_minute,
        }


class TranscriptCache:
    """Cache for transcript results to avoid duplicate API calls.
    
    Uses a simple hash of the audio bytes to identify similar audio.
    """
    
    def __init__(self, max_size: int = 100, ttl_seconds: float = 300):
        self._cache: dict[str, tuple[str, float]] = {}  # hash -> (transcript, timestamp)
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.hits = 0
        self.misses = 0
    
    def _hash_audio(self, audio_bytes: bytes) -> str:
        """Create a hash of audio bytes for cache lookup."""
        # Use first 10KB and length for quick hash
        sample = audio_bytes[:10240]
        return hashlib.md5(sample + str(len(audio_bytes)).encode()).hexdigest()
    
    def get(self, audio_bytes: bytes) -> Optional[str]:
        """Get cached transcript for audio."""
        hash_key = self._hash_audio(audio_bytes)
        
        if hash_key in self._cache:
            transcript, timestamp = self._cache[hash_key]
            if time.time() - timestamp < self.ttl_seconds:
                self.hits += 1
                logger.debug(f"[CACHE] Hit for hash {hash_key[:8]}...")
                return transcript
            else:
                # Expired
                del self._cache[hash_key]
        
        self.misses += 1
        return None
    
    def set(self, audio_bytes: bytes, transcript: str):
        """Cache a transcript result."""
        hash_key = self._hash_audio(audio_bytes)
        
        # Evict old entries if at capacity
        if len(self._cache) >= self.max_size:
            # Remove oldest entry
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
        
        self._cache[hash_key] = (transcript, time.time())
        logger.debug(f"[CACHE] Stored transcript for hash {hash_key[:8]}...")
    
    def get_stats(self) -> dict:
        """Get cache statistics."""
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hits / max(1, self.hits + self.misses),
        }


# Global instances for easy access
_rate_limiter: Optional[RateLimitedExecutor] = None
_transcript_cache: Optional[TranscriptCache] = None


def get_rate_limiter(
    requests_per_minute: int = 4,
    on_status_change: Optional[Callable] = None,
) -> RateLimitedExecutor:
    """Get or create the global rate limiter."""
    global _rate_limiter
    if _rate_limiter is None:
        config = RateLimiterConfig(requests_per_minute=requests_per_minute)
        _rate_limiter = RateLimitedExecutor(config, on_status_change)
    return _rate_limiter


def get_transcript_cache() -> TranscriptCache:
    """Get or create the global transcript cache."""
    global _transcript_cache
    if _transcript_cache is None:
        _transcript_cache = TranscriptCache()
    return _transcript_cache
