"""Populate a shared on-disk thumbnail cache independently of searches."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, SoupStrainer
from PIL import Image, ImageOps, UnidentifiedImageError

from app.catalog import store
from app.catalog.crawlers import Pace, lenta, vkusvill

log = logging.getLogger(__name__)
DAY = 86400
# Only the retailer's storefront and known product CDNs are eligible download origins.
HOSTS = {
    "dixy": ("dixy.ru",), "fixprice": ("fix-price.com", "fix-price.ru"),
    "lenta": ("lenta.com",), "magnit": ("magnit.ru",),
    "metro": ("metro-cc.ru",), "monetka": ("monetka.ru",),
    "perekrestok": ("perekrestok.ru", "x5static.net"), "pyaterochka": ("5ka.ru", "x5static.net"),
    "samokat": ("samokat.ru",), "vkusvill": ("vkusvill.ru",), "vprok": ("vprok.ru",),
}


def directory() -> Path:
    return Path(store.path()).parent / "catalog-photos"


def path_for(digest: str) -> Path:
    return directory() / digest[:2] / (digest + ".webp")


def _allowed(chain: str, url: str) -> bool:
    try:
        u = urlsplit(url)
        return (u.scheme == "https" and not u.username and not u.password and u.port in (None, 443)
                and any(u.hostname == d or (u.hostname or "").endswith("." + d) for d in HOSTS[chain]))
    except ValueError:
        return False


class SourceBusy(Exception):
    """A source-wide failure. Resume this network later."""
    def __init__(self, delay: int = 900):
        self.delay = delay


def _get(chain: str, url: str, pace: Pace, limit: int) -> tuple[str, bytes]:
    for _ in range(4):
        if not _allowed(chain, url):
            return "", b""
        pace.wait()
        with requests.get(url, timeout=(4, 8), allow_redirects=False, stream=True) as r:
            if r.is_redirect:
                target = urljoin(url, r.headers["Location"])
                if target == url:
                    return "", b""
                url = target
                continue
            if r.status_code in (401, 403, 429) or r.status_code >= 500:
                retry = r.headers.get("Retry-After", "")
                raise SourceBusy(max(1800, min(int(retry), DAY)) if retry.isdecimal() else 1800)
            if r.status_code != 200:
                return "", b""
            data = bytearray()
            for chunk in r.iter_content(65536):
                data.extend(chunk)
                if len(data) > limit:
                    return "", b""
            return url, bytes(data)
    return "", b""


def _image_from_page(html: bytes, page_url: str, sku: str) -> str:
    # Read the primary product only. Suggestions and image galleries for other SKUs
    # can also occur on the same page.
    soup = BeautifulSoup(html, "html.parser", parse_only=SoupStrainer(["link", "meta", "script"]))
    canonical = soup.select_one('link[rel~="canonical"]')
    def same_page(url):
        a, b = urlsplit(urljoin(page_url, url)), urlsplit(page_url)
        return a.hostname == b.hostname and a.path.rstrip("/") == b.path.rstrip("/")
    if canonical and not same_page(canonical.get("href") or ""):
        return ""
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (ValueError, RecursionError):
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            products = node.get("@graph", [node])
            if not isinstance(products, list):
                continue
            for obj in products:
                if not isinstance(obj, dict) or obj.get("@type") != "Product":
                    continue
                if not (str(obj.get("sku") or "") == sku or obj.get("url") and same_page(obj["url"])):
                    continue
                image = obj.get("image")
                for candidate in (image if isinstance(image, list) else [image]):
                    value = candidate.get("url") if isinstance(candidate, dict) else candidate
                    if isinstance(value, str) and value.strip():
                        return urljoin(page_url, value)
    # A branded favicon or a generic preview is not a product photograph.
    if canonical or soup.select_one('meta[property="og:type"][content*="product"]'):
        for tag in reversed(soup.select('meta[property="og:image"]')):
            url = tag.get("content") or ""
            if url and not any(word in url.lower() for word in ("favicon", "logo", "placeholder", "no-photo", "no_image")):
                return urljoin(page_url, url)
    return ""


def _discover(row, pace):
    chain, sku = row["chain"], row["sku"]
    if chain in ("lenta", "vkusvill") and sku.isascii() and sku.isdecimal():
        if chain == "lenta":
            item, status = lenta.LentaCrawler().details(sku)
            if status == lenta.ERROR:
                raise SourceBusy()
            product = lenta.to_product(item) if item else None
        else:
            item = vkusvill.VkusvillCrawler().details(sku)
            product = vkusvill.to_product(item) if item else None
        return product.image if product and str(product.sku) == sku else ""
    page = row["url"] or ""
    url, html = _get(chain, page, pace, 4 * 1024 * 1024)
    if not html or urlsplit(url).path.rstrip("/") != urlsplit(page).path.rstrip("/"):
        return ""
    return _image_from_page(html, url, sku)


def _thumbnail(chain, url, pace):
    if not url:
        return None
    _, content = _get(chain, url, pace, 6 * 1024 * 1024)
    if not content:
        return None
    try:
        with Image.open(io.BytesIO(content)) as picture:
            if picture.format not in ("JPEG", "PNG", "WEBP", "GIF", "AVIF"):
                return None
            if min(picture.size) < 16 or picture.width * picture.height > 20_000_000:
                return None
            picture.thumbnail((240, 240), Image.Resampling.LANCZOS)
            thumb = ImageOps.exif_transpose(picture).convert("RGBA" if picture.has_transparency_data else "RGB")
            buf = io.BytesIO()
            thumb.save(buf, "WEBP", quality=80, method=3, exif=b"", icc_profile=None)
    except (UnidentifiedImageError, Image.DecompressionBombError, ValueError, OSError):
        return None
    return buf.getvalue()


def _save(data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    path = path_for(digest)
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(dir=path.parent, delete=False)
        try:
            with tmp:
                tmp.write(data)
            os.replace(tmp.name, path)
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    return digest


def warm(row, pace):
    chain, source = row["chain"], row["image"] or ""
    data = _thumbnail(chain, source, pace)
    if not data:
        candidate = _discover(row, pace)
        if candidate and candidate != source:
            data = _thumbnail(chain, candidate, pace)
            if data:
                source = candidate
    digest = _save(data) if data else None
    with store.connect() as db:
        db.execute("UPDATE chain_products SET photo_digest=COALESCE(?,photo_digest), photo_after=?,"
                   " image=COALESCE(NULLIF(?,''),image) WHERE id=? AND active=1 AND sku=?"
                   " AND name=? AND COALESCE(image,'')=? AND COALESCE(url,'')=?",
                   (digest, int(time.time()) + (90 if digest else 7) * DAY, source, row["id"],
                    row["sku"], row["name"], row["image"] or "", row["url"] or ""))
    return bool(digest)


def _batch(chain, limit=20):
    with store.connect() as db:
        rows = db.execute("SELECT id,chain,sku,name,image,url FROM chain_products WHERE chain=? AND active=1"
                          " AND photo_after<=? ORDER BY (photo_after=0) DESC, (COALESCE(image,'')!='') DESC,"
                          " (price>0 AND in_stock=1) DESC, photo_after, id LIMIT ?",
                          (chain, int(time.time()), limit)).fetchall()
    if not rows:
        return 300
    pace = Pace(chain=chain)
    started = time.monotonic()
    saved = 0
    for row in rows:
        try:
            saved += warm(row, pace)
        except (SourceBusy, requests.RequestException) as exc:
            delay = exc.delay if isinstance(exc, SourceBusy) else 300
            with store.connect() as db:
                db.execute("UPDATE chain_products SET photo_after=? WHERE id=? AND photo_after<=?",
                           (int(time.time()) + delay, row["id"], int(time.time())))
            log.info("фото %s: источник временно недоступен, повтор через %d с", chain, delay)
            return delay
        if time.monotonic() - started >= 12:
            break
    if saved:
        log.info("фото %s: сохранено %d", chain, saved)
    return 0


def _serve():
    from app.catalog.worker import one_crawler

    chains = deque(HOSTS)
    resume = {}
    directory().mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="catalog-photo") as pool:
        while True:
            due = []
            try:
                if shutil.disk_usage(directory()).free < 1024**3:
                    log.warning("фото: на томе осталось меньше 1 ГБ")
                    time.sleep(300)
                    continue
                for _ in range(len(chains)):
                    chain = chains[0]
                    chains.rotate(-1)
                    if resume.get(chain, 0) <= time.monotonic():
                        due.append(chain)
                    if len(due) == 3:
                        break
                if due:
                    with one_crawler():
                        jobs = [(chain, pool.submit(_batch, chain)) for chain in due]
                        for chain, job in jobs:
                            try:
                                resume[chain] = time.monotonic() + job.result()
                            except Exception:
                                log.exception("фото: ошибка обработки %s", chain)
                                resume[chain] = time.monotonic() + 300
            except Exception:
                log.exception("фото: не удалось продолжить обход")
            time.sleep(1 if due else 30)


def serve_in_background():
    threading.Thread(target=_serve, name="catalog-photo-cache", daemon=True).start()
