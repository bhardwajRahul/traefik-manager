import pytest


@pytest.mark.parametrize('value', ['-1', '0', 'abc', '5abc', ''])
def test_an_invalid_line_count_is_rejected(client, value):
    r = client.get('/api/traefik/logs?lines=' + value)
    assert r.status_code == 400, r.get_data(as_text=True)
    assert r.get_json()['error'] == 'Invalid lines parameter', \
        'the agent answers invalid values the same way, so the two must not drift'
