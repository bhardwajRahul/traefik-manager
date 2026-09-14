import pytest

from core import monitor


class _Resp:
    def __init__(self, code, payload=None):
        self.status_code = code
        self._payload = payload or {}

    def json(self):
        return self._payload


@pytest.fixture
def agent_events(monkeypatch):
    calls = []
    replies = {}
    agent = {'id': 'a1', 'name': 'edge'}

    monkeypatch.setattr(monitor, '_agents', lambda: [agent])
    monkeypatch.setattr(monitor, '_agent_usable', lambda a: replies.get('usable', True))

    def fake_request(a, method, path):
        calls.append(path)
        return replies.get('by_path', {}).get(path, replies.get('resp'))

    monkeypatch.setattr(monitor.agents_http_mod, '_agent_request', fake_request)
    monitor._state.clear()
    monitor._memory_state.clear()
    return {'calls': calls, 'replies': replies, 'agent': agent}


def _run():
    return monitor._check_agent_events()


def test_the_first_poll_only_records_a_baseline(agent_events):
    agent_events['replies']['resp'] = _Resp(200, {
        'events': [{'id': 1, 'kind': 'git', 'message': 'auto-push failed'}],
        'latest': 1,
    })
    assert _run() == [], 'history from before the hub was watching must not be replayed'
    assert monitor._section('agent_events')['a1']['id'] == 1


def test_new_failures_are_raised_once(agent_events):
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 0})
    _run()
    agent_events['replies']['resp'] = _Resp(200, {
        'events': [{'id': 1, 'kind': 'git', 'message': 'auto-push failed: no remote'}],
        'latest': 1,
    })
    raised = _run()
    assert len(raised) == 1
    kind, message, category = raised[0]
    assert kind == 'error'
    assert category == 'agent'
    assert 'edge' in message and 'git backup' in message and 'no remote' in message

    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 1})
    assert _run() == [], 'the same failure must not be raised twice'


def test_the_cursor_is_sent_so_the_agent_can_filter(agent_events):
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 7})
    _run()
    _run()
    assert agent_events['calls'][-1] == '/api/events?since=7'


def test_an_unreachable_agent_keeps_its_cursor(agent_events):
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 4})
    _run()
    agent_events['replies']['usable'] = False
    assert _run() == []
    assert monitor._section('agent_events')['a1']['id'] == 4, \
        'losing the cursor would replay every event when the agent comes back'


def test_a_failed_poll_does_not_lose_the_cursor(agent_events):
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 9})
    _run()
    agent_events['replies']['resp'] = _Resp(502)
    assert _run() == []
    assert monitor._section('agent_events')['a1']['id'] == 9


def test_a_flood_is_capped(agent_events):
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 0})
    _run()
    many = [{'id': i, 'kind': 'backup', 'message': f'failure {i}'} for i in range(1, 40)]
    agent_events['replies']['resp'] = _Resp(200, {'events': many, 'latest': 39})
    raised = _run()
    assert len(raised) == monitor.AGENT_EVENT_MAX + 1
    assert 'more failures not shown' in raised[-1][1]


def test_an_empty_message_is_skipped(agent_events):
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 0})
    _run()
    agent_events['replies']['resp'] = _Resp(200, {
        'events': [{'id': 1, 'kind': 'git', 'message': '   '}], 'latest': 1})
    assert _run() == []


def test_every_agent_failure_kind_gets_a_readable_label(agent_events):
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 0})
    _run()
    kinds = ['git', 'restart', 'backup', 'storage']
    events = [{'id': i + 1, 'kind': k, 'message': 'boom'} for i, k in enumerate(kinds)]
    agent_events['replies']['resp'] = _Resp(200, {'events': events, 'latest': len(kinds)})
    raised = _run()
    assert len(raised) == len(kinds)
    for kind, (_t, message, _c) in zip(kinds, raised):
        assert monitor._AGENT_EVENT_LABELS[kind] in message


def test_the_check_is_registered_with_the_monitor():
    names = [name for name, _interval, _fn in monitor._checks]
    assert 'agent_events' in names


def test_the_category_is_one_channels_already_know(agent_events):
    from core import settings
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 0})
    _run()
    agent_events['replies']['resp'] = _Resp(200, {
        'events': [{'id': 1, 'kind': 'git', 'message': 'boom'}], 'latest': 1})
    assert _run()[0][2] in settings.CHANNEL_CATEGORIES


def _ev(*ids, kind='git'):
    return [{'id': i, 'kind': kind, 'message': f'failure {i}'} for i in ids]


def test_the_boot_is_sent_once_known(agent_events):
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 7, 'boot': 'b1'})
    _run()
    _run()
    assert agent_events['calls'][0] == '/api/events?since=0'
    assert agent_events['calls'][-1] == '/api/events?since=7&boot=b1'


def test_a_matching_boot_uses_the_cursor(agent_events):
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 7, 'boot': 'b1'})
    _run()
    agent_events['replies']['resp'] = _Resp(200, {'events': _ev(8), 'latest': 8, 'boot': 'b1'})
    assert len(_run()) == 1
    assert monitor._section('agent_events')['a1'] == {'boot': 'b1', 'id': 8}


def test_a_changed_boot_raises_every_event_in_the_new_ring(agent_events):
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 57, 'boot': 'old'})
    _run()
    agent_events['replies']['resp'] = _Resp(200, {'events': _ev(1, 2, kind='storage'), 'latest': 2, 'boot': 'new'})
    raised = _run()
    assert len(raised) == 2 and 'storage' in raised[0][1], \
        'events recorded after an agent restart were hidden behind the old cursor'
    assert monitor._section('agent_events')['a1'] == {'boot': 'new', 'id': 2}


def test_an_agent_without_boot_falls_back_to_id_regression(agent_events):
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 57})
    _run()
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 3})
    agent_events['replies']['by_path'] = {'/api/events?since=0': _Resp(200, {'events': _ev(1, 2, 3), 'latest': 3})}
    raised = _run()
    assert len(raised) == 3
    assert agent_events['calls'][-1] == '/api/events?since=0'
    assert monitor._section('agent_events')['a1']['id'] == 3


def test_a_restart_with_an_empty_log_resets_the_cursor(agent_events):
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 57})
    _run()
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 0})
    assert _run() == []
    agent_events['replies']['resp'] = _Resp(200, {'events': _ev(1), 'latest': 1})
    assert len(_run()) == 1, 'the first failure after a restart was skipped'


def test_a_failed_second_read_starts_over_from_zero(agent_events):
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 57})
    _run()
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 3})
    agent_events['replies']['by_path'] = {'/api/events?since=0': _Resp(502)}
    assert _run() == []
    assert monitor._section('agent_events')['a1']['id'] == 0


def test_a_legacy_int_state_is_read_and_upgraded(agent_events):
    monitor._section('agent_events')['a1'] = 57
    agent_events['replies']['resp'] = _Resp(200, {'events': _ev(58), 'latest': 58, 'boot': 'b1'})
    assert len(_run()) == 1, 'an upgrade must neither replay history nor skip the next failure'
    assert agent_events['calls'][-1] == '/api/events?since=57'
    assert monitor._section('agent_events')['a1'] == {'boot': 'b1', 'id': 58}


def test_an_unreachable_agent_keeps_its_boot_and_cursor(agent_events):
    agent_events['replies']['resp'] = _Resp(200, {'events': [], 'latest': 4, 'boot': 'b1'})
    _run()
    agent_events['replies']['resp'] = _Resp(502)
    assert _run() == []
    assert monitor._section('agent_events')['a1'] == {'boot': 'b1', 'id': 4}
