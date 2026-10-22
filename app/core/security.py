import asyncio
import ipaddress
import os
import re
import socket
import threading
from urllib.parse import urlsplit
from cryptography.fernet import Fernet
from httpcore._backends.auto import AutoBackend
from app.settings import HOME
_CIPHER_LOCK = threading.RLock()


def cipher():
    with _CIPHER_LOCK:
        key = os.environ.get('WARGAME_SECRET_KEY')
        if not key:
            path = HOME / '.secret.key'
            if not path.exists():
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(Fernet.generate_key())
            key = path.read_bytes()
        return Fernet(key)


def encrypt(value):
    return cipher().encrypt(value.encode()).decode() if value else None


def decrypt(value):
    return cipher().decrypt(value.encode()).decode() if value else ''


def credentials(model, encrypted):
    env = model.get('api_key_env')
    key = os.environ.get(env, '') if env else decrypt(encrypted)
    if not key:
        raise ValueError('未配置有效 API 凭据或环境变量为空')
    return key


def redact(text, secret):
    return text.replace(secret, '[REDACTED]') if secret else text


def public_ip(value):
    ip = ipaddress.ip_address(value.split('%')[0])
    if getattr(ip, 'ipv4_mapped', None):
        ip = ip.ipv4_mapped
    # Some globally routed transition addresses can tunnel private IPv4 destinations.
    return ip.is_global and not ip.is_multicast and not getattr(ip, 'sixtofour', None) and not getattr(ip, 'teredo', None)


def validate_url(url):
    try:
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError()
        if parts.port not in (None, 443, 8443):
            raise ValueError()
        addresses = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
        if not addresses or any(not public_ip(a[4][0]) for a in addresses):
            raise ValueError()
    except (ValueError, OSError):
        raise ValueError('API URL 须为公网 HTTPS 地址（443/8443），禁止凭据、查询串、内网或保留地址') from None
    return url.rstrip('/')


class SafeNetworkBackend(AutoBackend):
    """Re-resolve per connection, validate every answer, connect to the checked IP.

    httpcore keeps the original hostname for TLS SNI/certificate verification.
    This prevents DNS rebinding between validation and socket connection.
    """
    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if port not in (443, 8443):
            raise ValueError('Forbidden port')
        loop = asyncio.get_running_loop()
        addresses = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses or any(not public_ip(a[4][0]) for a in addresses):
            raise ValueError('Forbidden network destination')
        return await super().connect_tcp(addresses[0][4][0], port, timeout, local_address, socket_options)


def valid_env(name):
    return not name or bool(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name))
