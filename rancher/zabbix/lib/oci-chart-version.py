#!/usr/bin/env python3
"""Find the newest stable chart tag without downloading the chart archive."""

import json
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen


def latest_version(reference, opener=urlopen):
    if not reference.startswith('oci://'):
        raise ValueError('expected an OCI chart reference')
    parsed = urlsplit(reference.replace('oci://', 'https://', 1))
    if not parsed.netloc or not parsed.path.strip('/') or parsed.username or parsed.password:
        raise ValueError('invalid OCI chart reference')
    origin = 'https://' + parsed.netloc
    url = origin + '/v2/' + parsed.path.strip('/') + '/tags/list?n=1000'
    deadline = time.monotonic() + 18
    token = None
    versions = []

    def request(target, authenticated=True):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('OCI version lookup exceeded its time budget')
        headers = {'Accept': 'application/json'}
        if authenticated and token:
            headers['Authorization'] = 'Bearer ' + token
        return opener(Request(target, headers=headers), timeout=min(5, remaining))

    for page in range(100):
        response = None
        for attempt in range(3):
            try:
                response = request(url)
                break
            except HTTPError as error:
                if error.code == 401 and token is None:
                    challenge = error.headers.get('WWW-Authenticate', '')
                    if not challenge.lower().startswith('bearer '):
                        raise ValueError('registry requires unsupported authentication') from error
                    params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
                    realm = params.pop('realm', '')
                    if urlsplit(realm).scheme != 'https':
                        raise ValueError('registry supplied an invalid token endpoint')
                    with request(realm + ('&' if '?' in realm else '?') + urlencode(params), False) as auth:
                        payload = json.load(auth)
                        token = payload.get('token') or payload.get('access_token')
                    if not token:
                        raise ValueError('registry did not supply an anonymous access token')
                elif error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise
            except OSError:
                if attempt == 2:
                    raise
        if response is None:
            raise ValueError('registry tag request failed')
        with response:
            payload = json.load(response)
            for tag in payload.get('tags') or []:
                if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', tag):
                    versions.append(tag)
            link = response.headers.get('Link', '')
        next_link = re.search(r'<([^>]+)>;\s*rel="?next"?', link)
        if not next_link:
            break
        url = urljoin(url, next_link.group(1))
        if urlsplit(url).scheme != 'https' or urlsplit(url).netloc != parsed.netloc:
            raise ValueError('registry pagination points outside its HTTPS origin')
    else:
        raise ValueError('registry tag pagination limit exceeded')
    if not versions:
        raise ValueError('registry returned no stable chart versions')
    return max(versions, key=lambda value: tuple(map(int, value.split('.'))))


if __name__ == '__main__':
    try:
        print(latest_version(sys.argv[1]))
    except Exception as error:
        print('OCI chart version lookup failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
