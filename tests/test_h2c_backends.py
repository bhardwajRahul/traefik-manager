import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


def test_every_scheme_picker_offers_h2c():
    route = _read('templates', 'modals', 'route_modal.html')
    block = route[route.index('name="scheme"'):route.index('name="targetIp"')]
    assert 'value="h2c"' in block, (
        'Traefik 3.7.13 stopped forwarding the h2c upgrade, so a cleartext HTTP/2 backend can '
        'only be reached by declaring the server with the h2c scheme')
    for path in (('static', 'js', 'routes.js'), ('static', 'js', 'services.js')):
        src = _read(*path)
        for row in re.findall(r'<select class="input-field (?:bk|svc)-scheme[^>]*>.*?</select>', src):
            assert 'value="h2c"' in row, '%s: %s' % ('/'.join(path), row)


def test_an_h2c_server_survives_being_read_back():
    src = _read('static', 'js', 'routes.js')
    block = src[src.index('const _SCHEME_RE'):src.index('function _populateBackends(')]
    assert 'h2c' in block, 'the parser only knew http and https, so h2c landed in the host field'
    assert "replace('http://','')" not in block, \
        'a scheme-by-scheme replace misses h2c and leaves it glued to the host'

    scheme_re = re.search(r'const _SCHEME_RE = /(.+?)/i;', block)
    assert scheme_re, block[:120]
    pattern = re.compile(scheme_re.group(1).replace('\\/', '/'), re.I)
    for url, scheme, host in (('h2c://10.0.0.5:8080', 'h2c', '10.0.0.5:8080'),
                              ('https://10.0.0.5:8080', 'https', '10.0.0.5:8080'),
                              ('http://10.0.0.5:8080', 'http', '10.0.0.5:8080'),
                              ('10.0.0.5:8080', None, '10.0.0.5:8080')):
        m = pattern.match(url)
        assert (m.group(1).lower() if m else None) == scheme, url
        assert pattern.sub('', url) == host, url


def test_the_route_form_no_longer_hardcodes_two_schemes():
    src = _read('static', 'js', 'routes.js')
    assert "startsWith('https://') ? 'https' : 'http'" not in src, \
        'every read of a server URL has to go through the shared scheme helper'
    svc = _read('static', 'js', 'services.js')
    assert "u.startsWith('https://') ? 'https' : 'http'" not in svc


def test_a_full_url_typed_into_the_host_field_is_passed_through():
    src = _read('app.py')
    block = src[src.index('_SCHEME_IN_HOST'):src.index('_HC_TEXT')]
    assert 'h2c' in block, (
        'a host typed as h2c://box was not recognised as a full URL, so the scheme was '
        'prefixed again and Traefik got http://h2c://box')


def test_the_docs_say_when_to_pick_h2c():
    routes = _read('docs', 'tab-routes.md')
    line = [l for l in routes.splitlines() if l.startswith('| Scheme |')]
    assert line and 'h2c' in line[0], 'the scheme row still lists only HTTP and HTTPS'
    assert '3.7.13' in line[0], 'say which Traefik release made this the only way'
    assert 'h2c' in _read('docs', 'tab-services.md')
