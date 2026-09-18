import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_compact_stat_cards_do_not_hide_an_opened_alert():
    with open(os.path.join(ROOT, 'static', 'css', 'app.css'), encoding='utf-8') as fh:
        css = fh.read()
    for selectors, body in re.findall(r'([^{}]+)\{([^{}]*)\}', css):
        if '.atk-open' in selectors and ('compact' in selectors) and re.search(r'display\s*:\s*none', body):
            raise AssertionError(
                'compact stat cards hid the detail of the alert that was just clicked (issue 175): '
                + ' '.join(selectors.split()))
