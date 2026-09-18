import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


def test_switching_the_route_view_redraws_without_refetching():
    src = _read('static', 'js', 'routes.js')
    body = src[src.index('function toggleRouteView()'):]
    body = body[:body.index('\n}\n')]
    assert 'renderRouteGrid(window._lastRenderedApps)' in body, \
        'switching between cards and list fetched every route from the server again'
    assert body.index('renderRouteGrid(') < body.index('refreshRoutes()'), \
        'the server fetch must only be the fallback when nothing has been drawn yet'


def test_the_list_view_is_parsed_once():
    src = _read('static', 'js', 'routes.js')
    body = src[src.index('function renderRouteGrid(apps)'):src.index('function _ensureResolverOption(')]
    assert '${grid.innerHTML}' not in body, \
        'the list view parsed every row into the page and then re-parsed all of it to add the header'
