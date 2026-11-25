"""Bounded HTTP reads for public paper artifacts and metadata."""
import httpx

MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


async def download_bytes(url: str, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> bytes:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        async with client.stream('GET', url) as response:
            if response.status_code != 200:
                raise ValueError(f'HTTP {response.status_code} fetching {url}')
            length = response.headers.get('content-length', '')
            if length.isdigit() and int(length) > max_bytes:
                raise ValueError(f'Download exceeds the {max_bytes // 1024} KiB limit')
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f'Download exceeds the {max_bytes // 1024} KiB limit')
                chunks.append(chunk)
            return b''.join(chunks)
