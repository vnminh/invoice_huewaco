"""Small deployment boundary; no database credentials or passwords enter audit records."""
from base64 import b64decode
from contextvars import ContextVar
import ipaddress
import os
import re
import secrets
from threading import Lock
import time
from urllib.parse import urlsplit

from starlette.responses import JSONResponse


actor = ContextVar('knowledge_actor', default='local-admin')
expected_revision = ContextVar('knowledge_expected_revision', default=None)


class AccessPolicy:
    def __init__(self, username='', password='', testing=False):
        if bool(username) != bool(password):
            raise ValueError('Set both APP_ADMIN_USER and APP_ADMIN_PASSWORD')
        if password and len(password) < 16:
            raise ValueError('APP_ADMIN_PASSWORD must contain at least 16 characters')
        if username and not re.fullmatch(r'[A-Za-z0-9._@-]{1,100}', username):
            raise ValueError('APP_ADMIN_USER must contain 1–100 letters, digits, dot, underscore, @ or dash')
        self.username, self.password, self.testing = username, password, testing
        self.failures, self.lock = {}, Lock()

    @classmethod
    def environment(cls, testing=False):
        return cls(os.getenv('APP_ADMIN_USER', ''), os.getenv('APP_ADMIN_PASSWORD', ''), testing)

    def authorize(self, request):
        if self.username:
            address = request.client.host if request.client else 'unknown'
            moment = time.monotonic()
            with self.lock:
                count, since = self.failures.get(address, (0, moment))
                if moment - since > 60:
                    count, since = 0, moment
                if count >= 10:
                    return JSONResponse({'detail': 'Đăng nhập sai nhiều lần. Thử lại sau một phút.'}, 429,
                                        headers={'Retry-After': '60'})
            try:
                kind, token = request.headers.get('authorization', '').split(' ', 1)
                if kind.lower() != 'basic' or len(token) > 4096:
                    raise ValueError()
                user, password = b64decode(token, validate=True).decode('utf-8').split(':', 1)
                valid = secrets.compare_digest(user.encode(), self.username.encode()) & secrets.compare_digest(
                    password.encode(), self.password.encode())
            except (ValueError, UnicodeError):
                valid = False
            if not valid:
                with self.lock:
                    if len(self.failures) >= 1024:
                        self.failures.pop(next(iter(self.failures)))
                    self.failures[address] = (count + 1, since)
                return JSONResponse({'detail': 'Cần đăng nhập bằng tài khoản quản trị.'}, 401,
                                    headers={'WWW-Authenticate': 'Basic realm="HueWACO", charset="UTF-8"'})
            identity = self.username
            with self.lock:
                self.failures.pop(address, None)
        else:
            host = request.client.host if request.client else ''
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = self.testing and host == 'testclient'
            # Do not trust forwarded headers or arbitrary Host headers in local-only mode.
            local_host = request.url.hostname in ('localhost', '127.0.0.1', '::1') or (
                self.testing and request.url.hostname == 'testserver')
            forwarded = any(name in request.headers for name in ('forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto'))
            if not local or not local_host or forwarded:
                return JSONResponse({'detail': 'Chưa cấu hình tài khoản quản trị; chỉ cho phép truy cập cục bộ.'}, 403)
            identity = 'local-admin'
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin:
                try:
                    source = urlsplit(origin)
                    target = urlsplit(str(request.url))
                    def endpoint(url):
                        return (url.scheme, url.hostname, url.port or (443 if url.scheme == 'https' else 80))
                    same_origin = endpoint(source) == endpoint(target)
                except ValueError:
                    same_origin = False
                if not same_origin:
                    return JSONResponse({'detail': 'Không chấp nhận thao tác từ website khác.'}, 403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': 'Không chấp nhận thao tác từ website khác.'}, 403)
        request.state.actor = identity
        return None


SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'same-origin', 'Cache-Control': 'no-store',
    'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
}
