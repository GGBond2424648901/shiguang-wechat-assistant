"""Public RSS/Atom only; pinned public DNS addresses and bounded HTTP/XML input."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime
from email.utils import parsedate_to_datetime
import http.client
import ipaddress
import re
import socket
import ssl
import threading
from time import monotonic
from urllib.parse import urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from .web_search import _public_url

MAX_FEED_BYTES = 1024 * 1024
_DNS = ThreadPoolExecutor(max_workers=2, thread_name_prefix='news-feed-dns')
_DNS_SLOTS = threading.BoundedSemaphore(2)
_XML_BASE = '{http://www.w3.org/XML/1998/namespace}base'
_ATOM = '{http://www.w3.org/2005/Atom}'


def _public_address(raw):
    address = ipaddress.ip_address(raw)
    return address.is_global and not (address.is_multicast or address.is_reserved or address.is_unspecified)


def validate_feed_url(url):
    if not _public_url(url):
        raise ValueError('订阅源只允许无凭据的公共 HTTP(S) 地址')
    try:
        parsed = urlsplit(url)
        if parsed.port not in (None, 80 if parsed.scheme == 'http' else 443):
            raise ValueError('订阅源仅允许标准 HTTP(S) 端口')
        host = parsed.hostname.rstrip('.').lower()
        if host in ('localhost', 'localhost.localdomain') or host.endswith(('.localhost', '.local', '.internal', '.home', '.lan')):
            raise ValueError('订阅源不能使用本地或内部地址')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Reject numeric/legacy IPv4 forms before platform DNS interprets them.
            if re.fullmatch(r'[0-9.]+|0[xX][0-9a-fA-F]+', host):
                raise ValueError('订阅源地址格式无效')
        else:
            if not _public_address(host): raise ValueError('订阅源不能使用内部地址')
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or '/', parsed.query, ''))
    except (ValueError, AttributeError):
        raise ValueError('订阅源地址或端口不安全，只允许公共 HTTP(S) 标准端口') from None


def _public_addresses(host, port):
    if not _DNS_SLOTS.acquire(timeout=.1): raise ValueError('订阅源 DNS 繁忙，请稍后重试')
    try:
        future = _DNS.submit(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except RuntimeError:
        _DNS_SLOTS.release()
        raise ValueError('订阅源 DNS 不可用') from None
    future.add_done_callback(lambda _: _DNS_SLOTS.release())
    try: answers = future.result(timeout=3)
    except TimeoutError: raise ValueError('订阅源 DNS 超时') from None
    except OSError: raise ValueError('订阅源 DNS 解析失败') from None
    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses or any(not _public_address(address) for address in addresses):
        raise ValueError('订阅源 DNS 包含内部或非公共地址')
    return addresses


class _PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, port, address, secure, timeout, deadline=None):
        super().__init__(host, port, timeout=timeout)
        self.address, self.secure, self.deadline = address, secure, deadline

    def _remaining(self):
        remaining = min(self.timeout, self.deadline - monotonic()) if self.deadline is not None else self.timeout
        if remaining <= 0: raise TimeoutError('feed deadline')
        return remaining

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self._remaining())
        self.sock.settimeout(self._remaining())
        if self.secure:
            try: self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)
            except BaseException:
                self.sock.close()
                raise
        self.sock.settimeout(self._remaining())


def _interrupt_connection(connection):
    # A drip-fed status/header line must not keep HTTPResponse.getresponse()
    # blocked indefinitely despite ordinary per-read socket timeouts.
    sock = getattr(connection, 'sock', None)
    if sock is not None:
        try: sock.shutdown(socket.SHUT_RDWR)
        except OSError: pass
    connection.close()


def _fetch_bytes(url, cancelled=lambda: False, runtime=None):
    deadline = monotonic() + 18
    for hop in range(4):
        if cancelled(): raise ValueError('订阅源采集已取消')
        url = validate_feed_url(url)
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        addresses = _public_addresses(parsed.hostname, port)
        remaining = deadline - monotonic()
        if remaining <= 0: raise ValueError('订阅源请求超时')
        connection = _PinnedConnection(parsed.hostname, port, addresses[0], parsed.scheme == 'https', min(5, remaining), deadline)
        started, failed = monotonic(), True
        timer = threading.Timer(remaining, _interrupt_connection, args=(connection,))
        timer.daemon = True
        timer.start()
        try:
            connection.request('GET', (parsed.path or '/') + ('?' + parsed.query if parsed.query else ''),
                headers={'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml',
                         'Accept-Encoding': 'identity', 'User-Agent': 'Shiguang-PublicNews/1.0'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location or hop == 3: raise ValueError('订阅源重定向过多或缺少地址')
                url = validate_feed_url(urljoin(url, location))
                failed = False
                continue
            if response.status != 200: raise ValueError('订阅源 HTTP 请求失败（' + str(response.status) + '）')
            if response.getheader('Content-Encoding', 'identity').lower() not in ('identity', ''):
                raise ValueError('订阅源不支持压缩响应，请提供标准 XML 源')
            length = response.getheader('Content-Length')
            if length and (not length.isdigit() or int(length) > MAX_FEED_BYTES): raise ValueError('订阅源超过 1 MiB 大小限制')
            chunks, total = [], 0
            while True:
                if cancelled(): raise ValueError('订阅源采集已取消')
                remaining = deadline - monotonic()
                if remaining <= 0: raise ValueError('订阅源请求超时')
                if connection.sock: connection.sock.settimeout(min(5, remaining))
                reader = getattr(response, 'read1', response.read)
                chunk = reader(min(65536, MAX_FEED_BYTES + 1 - total))
                total += len(chunk)
                if total > MAX_FEED_BYTES: raise ValueError('订阅源超过 1 MiB 大小限制')
                if not chunk: break
                chunks.append(chunk)
            failed = False
            return b''.join(chunks), url
        except (OSError, http.client.HTTPException):
            raise ValueError('订阅源网络请求失败或超时') from None
        finally:
            timer.cancel()
            connection.close()
            if runtime is not None:
                from .operations_usage import record_usage
                record_usage(runtime, kind='feed', duration_ms=(monotonic() - started) * 1000, error=failed)
    raise ValueError('订阅源重定向失败')


def _text(node, name, ns=''):
    child = node.find(ns + name)
    return ''.join(child.itertext()).strip()[:4000] if child is not None else ''


def _stamp(raw):
    try:
        stamp = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        try: stamp = parsedate_to_datetime(raw)
        except (ValueError, TypeError, OverflowError): return ''
    # An absent timezone is not invented; downstream labels the assumption.
    try: return stamp.isoformat()
    except (ValueError, OverflowError): return ''


def parse_feed(data, base_url):
    if not isinstance(data, bytes) or len(data) > MAX_FEED_BYTES: raise ValueError('订阅源超过 1 MiB 大小限制')
    # Reject NUL (UTF-16/32) and all DTD/entity declarations before XML parsing.
    if b'\x00' in data or re.search(br'<!\s*(?:DOCTYPE|ENTITY)', data, re.I): raise ValueError('订阅源不允许 DTD 或 XML 实体')
    try: root = ET.fromstring(data)
    except (ET.ParseError, ValueError, RecursionError): raise ValueError('订阅源 XML 格式无效') from None
    atom = root.tag == _ATOM + 'feed'
    if not atom and root.tag != 'rss': raise ValueError('仅支持 RSS 2.0 或 Atom 1.0 公共订阅源')
    ns, rows, seen = _ATOM if atom else '', [], set()
    root_base = urljoin(base_url, root.get(_XML_BASE, ''))
    entries = ([(item, root_base) for item in root.findall(_ATOM + 'entry')] if atom else
               [(item, urljoin(root_base, channel.get(_XML_BASE, '')))
                for channel in root.findall('channel') for item in channel.findall('item')])
    for item, parent_base in entries[:200]:
        item_base = urljoin(parent_base, item.get(_XML_BASE, ''))
        if atom:
            link = next((node for node in item.findall(ns + 'link') if node.get('rel', 'alternate') == 'alternate' and node.get('href')), None)
            raw_url = urljoin(urljoin(item_base, link.get(_XML_BASE, '')), link.get('href')) if link is not None else ''
            published = _text(item, 'published', ns)
            updated = _text(item, 'updated', ns)
            stamp = _stamp(published or updated)
            date_kind = 'published' if published else 'updated'
            content = _text(item, 'summary', ns) or _text(item, 'content', ns)
        else:
            link = item.find('link')
            raw_url = urljoin(urljoin(item_base, link.get(_XML_BASE, '')), _text(item, 'link')) if link is not None else ''
            stamp, date_kind = _stamp(_text(item, 'pubDate')), 'published'
            content = _text(item, 'description')
        title = _text(item, 'title', ns)
        if not title or not raw_url or not _public_url(raw_url) or raw_url in seen: continue
        seen.add(raw_url)
        rows.append(dict(url=raw_url, title=title, snippet=content, sourceType='feed', dateKind=date_kind,
                         feedDate=stamp, page_excerpt=('发布时间：' + stamp + '。' if stamp and date_kind == 'published' else '') + content))
    return rows


def fetch_feed(url, cancelled=lambda: False, runtime=None):
    data, final_url = _fetch_bytes(url, cancelled, runtime)
    return parse_feed(data, final_url)
