"""Client for the Korean Police Agency CrossRoadInfoService OpenAPI."""
import json
import os
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


API_ROOT = "https://apis.data.go.kr/1320000/CrossRoadInfoService"
API_KEY_NAME = "DATA_GO_KR_SERVICE_KEY"
API_ROOT_NAME = "DATA_GO_KR_API_ROOT"
API_ERRORS = {
    "1": "APPLICATION_ERROR", "10": "INVALID_REQUEST_PARAMETER_ERROR",
    "12": "NO_OPENAPI_SERVICE_ERROR", "20": "SERVICE_ACCESS_DENIED_ERROR",
    "22": "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
    "30": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "31": "DEADLINE_HAS_EXPIRED_ERROR",
    "32": "UNREGISTERED_IP_ERROR", "99": "UNKNOWN_ERROR",
}


def _load_service_key():
    key = os.environ.get(API_KEY_NAME)
    if key:
        return key.strip()
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith(API_KEY_NAME + "="):
                return line.split("=", 1)[1].strip().strip("\"'") or None
    return None


def _load_api_root():
    root = os.environ.get(API_ROOT_NAME)
    if not root:
        env_file = Path(__file__).resolve().parents[2] / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                if line.strip().startswith(API_ROOT_NAME + "="):
                    root = line.split("=", 1)[1].strip().strip("\"'")
                    break
    root = (root or API_ROOT).rstrip("/")
    parsed = urllib.parse.urlparse(root)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("DATA_GO_KR_API_ROOT must be a valid HTTPS URL")
    return root


def _as_records(value):
    if value is None:
        return []
    if isinstance(value, list):
        records = []
        for item in value:
            records.extend(_as_records(item))
        return records
    if isinstance(value, dict):
        wrappers = {"item", "items", "crossroadinfolist", "crossroadinfodetail"}
        for key, child in value.items():
            if key.casefold() in wrappers:
                return _as_records(child)
        if any(key in value for key in ("INT_NO", "intNo", "INT_NM", "intNm")):
            return [value]
        records = []
        for child in value.values():
            records.extend(_as_records(child))
        return records
    return []


def _find_key(value, names):
    names = {name.casefold() for name in names}
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in names:
                return child
            found = _find_key(child, names)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_key(child, names)
            if found is not None:
                return found
    return None


class CrossRoadInfoClient:
    """Paginated, rate-limited API client that never logs the service key."""

    def __init__(self, service_key=None, base_url=None, timeout=25,
                 min_request_interval=0.25, retries=4, opener=None):
        self.service_key = service_key or _load_service_key()
        if not self.service_key:
            raise RuntimeError("Set DATA_GO_KR_SERVICE_KEY in the project .env or process environment")
        self.base_url = (base_url or _load_api_root()).rstrip("/")
        self.timeout = float(timeout)
        self.min_request_interval = max(0.2, float(min_request_interval))
        self.retries = max(0, int(retries))
        self.opener = opener or urllib.request.urlopen
        self._last_request_at = 0.0

    def _get_json(self, endpoint, params):
        query = dict(params)
        query.update({"serviceKey": self.service_key, "type": "json"})
        url = self.base_url + "/" + endpoint + "?" + urllib.parse.urlencode(query)
        last_error = None
        for attempt in range(self.retries + 1):
            wait = self.min_request_interval - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.monotonic()
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "AATSC/0.1 city-data-client"})
                with self.opener(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8-sig"))
                header = _find_key(payload, {"Header", "header"}) or {}
                if isinstance(header, dict):
                    code = str(header.get("resultCode", header.get("resultcode", "0")))
                    if code not in ("0", "00", "NORMAL_SERVICE"):
                        message = API_ERRORS.get(code, "government API error")
                        raise RuntimeError("CrossRoadInfoService {} (code {})".format(message, code))
                return payload
            except urllib.error.HTTPError as exc:
                last_error = "CrossRoadInfoService HTTP {}".format(exc.code)
                if exc.code not in (429, 500, 502, 503, 504) or attempt >= self.retries:
                    break
            except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
                last_error = "CrossRoadInfoService request failed ({})".format(type(exc).__name__)
                if attempt >= self.retries:
                    break
            except (ValueError, UnicodeError):
                raise RuntimeError("CrossRoadInfoService returned invalid JSON") from None
            time.sleep(min(8.0, (2 ** attempt) * 0.5 + random.random() * 0.2))
        raise RuntimeError(last_error or "CrossRoadInfoService request failed")

    def fetch_all(self, endpoint, region_code, page_size=100, name=None):
        if endpoint not in ("getCrossRoadInfoList", "getCrossRoadInfoDetail"):
            raise ValueError("Unsupported CrossRoadInfoService endpoint")
        if not 1 <= int(page_size) <= 1000:
            raise ValueError("page_size must be between 1 and 1000")
        page = 1
        records = []
        while True:
            params = {"numOfRows": int(page_size), "pageNo": page, "srchCTId": region_code}
            if name:
                params["srchCRNm"] = name
            response = self._get_json(endpoint, params)
            header = _find_key(response, {"Header", "header"}) or {}
            record_key = "CrossRoadInfoList" if endpoint == "getCrossRoadInfoList" else "CrossRoadInfoDetail"
            body = _find_key(response, {record_key})
            page_records = _as_records(body)
            if not page_records:
                page_records = _as_records(response)
            records.extend(page_records)
            try:
                total_pages = int(header.get("totPage", header.get("totalPage", 0)))
            except (TypeError, ValueError, AttributeError):
                total_pages = 0
            try:
                reported_total = int(header.get("totCount", header.get("totalCount", 0)))
            except (TypeError, ValueError, AttributeError):
                reported_total = 0
            if page == 1 and reported_total > 0 and not page_records:
                top_keys = ", ".join(str(key) for key in response.keys()) if isinstance(response, dict) else "non-object JSON"
                raise RuntimeError("CrossRoadInfoService reports {} records but no list could be parsed (keys: {})".format(
                    reported_total, top_keys[:250]))
            if total_pages and page >= total_pages:
                break
            if not page_records or len(page_records) < page_size:
                break
            page += 1
            if page > 10000:
                raise RuntimeError("CrossRoadInfoService pagination safety limit reached")
        return records

    def fetch_city(self, region_code):
        intersections = self.fetch_all("getCrossRoadInfoList", region_code)
        details = self.fetch_all("getCrossRoadInfoDetail", region_code)
        return intersections, details
