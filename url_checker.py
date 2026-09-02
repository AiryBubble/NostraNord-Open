import requests
from urllib.parse import urlparse
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

FILTER_URLS = [
    "filter urls here",
]

_filter_cache = {
    'domains': set(),
    'patterns': [],
    'last_update': 0,
    'update_interval': 3600,
}

_session = requests.Session()


def _parse_filter_text(text, domains, patterns):
    for line in text.split('\n'):
        line = line.strip()

        if not line or line.startswith('!') or line.startswith('['):
            continue

        if line.startswith('||') and '^' in line:
            domain = line[2:].split('^')[0]
        elif not line.startswith(('|', '/', '@', '#')) and '.' in line:
            domain = line.rstrip('^')
        else:
            continue

        if not domain:
            continue

        if '*' in domain:
            pattern = re.escape(domain).replace(r'\*', '.*')
            patterns.append(re.compile(pattern, re.IGNORECASE))
        else:
            domains.add(domain.lower())


def _fetch_filter(url):
    try:
        response = _session.get(url, timeout=10)
        if response.status_code == 200:
            return response.text
    except Exception as e:
        print(f"フィルター取得エラー ({url}): {e}")
    return None


def download_filter_list():
    domains = set()
    patterns = []
    success = False

    with ThreadPoolExecutor(max_workers=min(8, len(FILTER_URLS)) or 1) as executor:
        future_to_url = {executor.submit(_fetch_filter, url): url for url in FILTER_URLS}

        for future in as_completed(future_to_url):
            text = future.result()
            if text is not None:
                _parse_filter_text(text, domains, patterns)
                success = True

    if success:
        _filter_cache['domains'] = domains
        _filter_cache['patterns'] = patterns
        _filter_cache['last_update'] = time.time()
        print(f"フィルターを更新: {len(domains)} ドメイン, {len(patterns)} パターン ({len(FILTER_URLS)} リスト)")

    return success


def ensure_filter_updated():
    current_time = time.time()

    if (not _filter_cache['domains'] and not _filter_cache['patterns']) or \
       current_time - _filter_cache['last_update'] > _filter_cache['update_interval']:
        return download_filter_list()

    return bool(_filter_cache['domains'] or _filter_cache['patterns'])


def extract_domain_from_url(url):
    try:
        if not url.startswith(('http://', 'https://')):
            url = 'http://' + url

        parsed = urlparse(url)
        domain = parsed.netloc

        if domain.startswith('www.'):
            domain = domain[4:]

        return domain.lower()
    except Exception:
        return None


def _domain_matches(domain):
    domains = _filter_cache['domains']
    if domain in domains:
        return True

    parts = domain.split('.')
    for i in range(1, len(parts) - 1):
        parent = '.'.join(parts[i:])
        if parent in domains:
            return True

    return False


def check_url_with_filter(url):
    try:
        if not ensure_filter_updated():
            return False

        domain = extract_domain_from_url(url)
        if not domain:
            return False

        if _domain_matches(domain):
            return True

        if _filter_cache['patterns']:
            full_url = url
            if not full_url.startswith(('http://', 'https://')):
                full_url = 'http://' + full_url

            for pattern in _filter_cache['patterns']:
                if pattern.search(full_url) or pattern.search(domain):
                    return True

        return False

    except Exception as e:
        print(f"URLチェックエラー: {e}")
        return False
