import ipaddress
import re

from core import env as env_mod


def _diagnostic(app_module, peer, xff):
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s['authenticated'] = True
        s['csrf_token'] = 'testtoken'
    r = c.get('/api/diagnostics/client-ip', headers={'X-Forwarded-For': xff},
              environ_base={'REMOTE_ADDR': peer})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def test_a_public_peer_cannot_choose_its_own_ip(app_module):
    d = _diagnostic(app_module, '203.0.113.9', '1.2.3.4')
    assert d['effective_ip'] == '203.0.113.9', \
        'a client reaching Traefik Manager directly picked its own address with X-Forwarded-For'
    assert d['socket_peer'] == '203.0.113.9' and d['proxy_trusted'] is False


def test_a_proxy_on_a_docker_network_is_trusted(app_module):
    d = _diagnostic(app_module, '172.18.0.2', '1.2.3.4')
    assert d['effective_ip'] == '1.2.3.4' and d['proxy_trusted'] is True


def test_an_ipv4_mapped_peer_counts_as_its_ipv4_address(app_module):
    assert _diagnostic(app_module, '::ffff:10.0.0.5', '1.2.3.4')['effective_ip'] == '1.2.3.4'


def test_star_restores_trusting_every_peer(app_module, monkeypatch):
    monkeypatch.setattr(env_mod, 'TRUSTED_PROXIES', '*')
    d = _diagnostic(app_module, '203.0.113.9', '1.2.3.4')
    assert d['effective_ip'] == '1.2.3.4' and d['trusted_proxies'] == ['*']


def test_rotating_the_header_no_longer_resets_the_login_limit(app_module):
    c = app_module.app.test_client()
    peer = {'REMOTE_ADDR': '203.0.113.9'}
    page = c.get('/login', environ_base=peer).get_data(as_text=True)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page).group(1)
    codes = []
    for i in range(6):
        r = c.post('/login', data={'csrf_token': token, 'password': 'wrong-password'},
                   headers={'X-Forwarded-For': f'198.51.100.{i}'}, environ_base=peer)
        codes.append(r.status_code)
    assert codes[-1] == 429, codes


def test_the_wrapper_ignores_every_forwarding_header_from_an_untrusted_peer(app_module):
    seen = {}

    def echo(environ, start_response):
        seen.update(host=environ.get('HTTP_HOST'), addr=environ.get('REMOTE_ADDR'),
                    scheme=environ.get('wsgi.url_scheme'))
        start_response('200 OK', [])
        return [b'']

    wrapped = app_module._TrustedProxyFix(echo, 1)
    base = {'REQUEST_METHOD': 'GET', 'PATH_INFO': '/', 'SERVER_NAME': 'tm', 'SERVER_PORT': '5000',
            'HTTP_HOST': 'tm.lan', 'wsgi.url_scheme': 'http', 'HTTP_X_FORWARDED_FOR': '1.2.3.4',
            'HTTP_X_FORWARDED_HOST': 'evil.example', 'HTTP_X_FORWARDED_PROTO': 'https'}
    wrapped(dict(base, REMOTE_ADDR='203.0.113.9'), lambda *a: None)
    assert seen == {'host': 'tm.lan', 'addr': '203.0.113.9', 'scheme': 'http'}
    wrapped(dict(base, REMOTE_ADDR='192.168.1.2'), lambda *a: None)
    assert seen == {'host': 'evil.example', 'addr': '1.2.3.4', 'scheme': 'https'}


def test_trusted_proxies_parsing(monkeypatch):
    monkeypatch.setenv('TRUSTED_PROXIES', '10.1.2.3, nonsense ,2001:db8::/32')
    assert [str(n) for n in env_mod.trusted_proxies()] == ['10.1.2.3/32', '2001:db8::/32']
    monkeypatch.setenv('TRUSTED_PROXIES', '*')
    assert env_mod.trusted_proxies() == '*'
    monkeypatch.delenv('TRUSTED_PROXIES')
    default = env_mod.trusted_proxies()
    for trusted in ('127.0.0.1', '172.18.0.2', '192.168.1.5', '100.101.102.103', 'fd00::5'):
        assert any(ipaddress.ip_address(trusted) in n for n in default), trusted
    for public in ('203.0.113.9', '2001:db8::1'):
        assert not any(ipaddress.ip_address(public) in n for n in default), public
