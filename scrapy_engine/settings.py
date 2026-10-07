from __future__ import annotations

BOT_NAME = "scraper"

SPIDER_MODULES = ["scrapy_engine.spiders"]
NEWSPIDER_MODULE = "scrapy_engine.spiders"

ROBOTSTXT_OBEY = False

CONCURRENT_REQUESTS = 8
CONCURRENT_REQUESTS_PER_DOMAIN = 4
DOWNLOAD_TIMEOUT = 45
DOWNLOAD_DELAY = 0.15
DOWNLOAD_DELAY_JITTER = 0.5

RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [408, 425, 429, 500, 502, 503, 504]

AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 0.5
AUTOTHROTTLE_MAX_DELAY = 30.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0

COOKIES_ENABLED = True
TELNETCONSOLE_ENABLED = False

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/154.0.0.0 Safari/537.36"
)

DEFAULT_REQUEST_HEADERS = {
    "Accept": "application/json,text/html,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
}

ITEM_PIPELINES = {
    "scrapy_engine.pipelines.CanonicalExcelPipeline": 300,
}

FEED_EXPORT_ENCODING = "utf-8"
LOG_LEVEL = "INFO"


# Entrega respuestas de bloqueo al spider para clasificarlas explícitamente
# en lugar de convertirlas silenciosamente en respuestas ignoradas.
HTTPERROR_ALLOWED_CODES = [401, 403, 429]
