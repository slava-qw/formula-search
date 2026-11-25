"""Run the extension's source-block regressions and Node adapter tests."""
from pathlib import Path
import subprocess
import sys
import re

ROOT = Path(__file__).resolve().parents[1]
POPUP = 'extension/popup/popup.js'


def main():
    cases = {
        'smoke_dom': (None, None),
        'settings_store': (None, 'options_dom'),
        'settings_store_popup': ('SETTINGS STORE (popup outer)', None),
        'search_history': ('SEARCH HISTORY (popup outer)', None),
        'bookmarks': ('BOOKMARKS (popup outer)', None),
        'recent_papers': ('RECENT PAPERS (popup outer)', None),
        'pending_query': ('PENDING QUERY (popup outer)', 'pending_query_dom'),
        'message_router': ('MESSAGE ROUTER (popup outer)', 'message_router_dom'),
        'export_results': ('EXPORT RESULTS (popup outer)', None),
        'service_worker': (None, None),
        'chat_store_popup_injected': ('CHAT STORE (popup-injected)', None),
        'chat_store_content': ('CHAT STORE (content.js)', None),
        'chat_store_popup_view': ('CHAT STORE (popup outer)', 'popup_chat_dom'),
        'chat_feature_corner_cases': ('CHAT STORE (popup-injected)', None),
        'chat_feature_corner_cases_content': ('CHAT STORE (content.js)', None),
        'note_draft_recovery': ('CHAT STORE (popup-injected)', None),
    }
    minimum_assertions = dict(zip(cases, [6, 15, 4, 6, 15, 12, 26, 11, 5, 15, 36, 35, 58, 7, 7, 7]))
    failures = []
    for name, (block, fixture) in cases.items():
        args = [sys.executable, 'tools/run_js_test.py', f'tests_js/{name}.js']
        args += ['--whole', 'extension/document-identity.js', '--whole', 'extension/bookmark-identity.js', '--whole', 'extension/bookmark-backup.js', '--whole', 'extension/shared-store.js', '--whole', 'extension/store-client.js']
        if name != 'service_worker':
            args += ['--whole', 'tests_js/fixtures/shared_store_relay.js']
        if fixture:
            args += ['--whole', f'tests_js/fixtures/{fixture}.js']
        if name == 'settings_store':
            args += ['--whole', 'extension/options/options.js']
        if name == 'service_worker':
            args += ['--whole', 'extension/service-worker.js']
        if name == 'pending_query':
            args += ['--block', f'{POPUP}:=== QUERY INTENT (popup outer) BEGIN ===:=== QUERY INTENT (popup outer) END ===']
        if block:
            source = 'extension/content.js' if '(content.js)' in block else POPUP
            args += ['--block', f'{source}:=== {block} BEGIN ===:=== {block} END ===']
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        lines = result.stdout.strip().splitlines()
        print(f'{name}: {lines[-1] if lines else result.stderr.strip()}')
        count = re.search(r'(\d+)/(\d+) passed', result.stdout)
        incomplete = not count or int(count[2]) < minimum_assertions[name]
        if result.returncode or incomplete:
            failures.append(name)
            print('\n'.join(line[:500] for line in lines if line.startswith('FAIL:')) or result.stderr.strip())
            if incomplete:
                print(f'Incomplete test script: expected at least {minimum_assertions[name]} assertions.')
    node = subprocess.run(['node', '--test', *[str(p) for p in sorted((ROOT / 'tests_js').glob('*.test.cjs'))]], cwd=ROOT)
    return 1 if failures or node.returncode else 0


if __name__ == '__main__':
    raise SystemExit(main())
