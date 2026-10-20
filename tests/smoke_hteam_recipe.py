"""Opt-in Codex coordination smoke for a fixed 111 Recipe with three pi agents.

Uses isolated Herdr state and real agents. No answers are supplied to clarification
questions: success requires the requested Recipe to be prepared autonomously.

The coordinator runs under a throwaway CODEX_HOME built from the user's own codex
config. Two overrides matter, and both are test-environment concerns rather than
product behaviour:

* ``approval_policy = "never"`` — a Codex approval prompt blocks the run inside a
  pane with nobody to answer it. The prompt is a host-level permission dialog,
  not the layout/role question this smoke is about.
* ``sandbox_mode = "danger-full-access"`` — Codex's default ``workspace-write``
  sandbox cannot connect to Herdr's unix socket, so the launcher command running
  inside the pane is denied before it can create the Recipe.

The user's global codex configuration is copied, never modified.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def provision_codex_home(root: Path, repo: Path) -> Path:
    """Build an isolated CODEX_HOME from the user's own codex configuration."""
    source_home = Path(os.environ.get('CODEX_HOME') or (Path.home() / '.codex'))
    codex_home = root / 'codex-home'
    codex_home.mkdir()
    source_config = source_home / 'config.toml'
    if source_config.exists():
        kept = [line for line in source_config.read_text().splitlines()
                if not line.startswith(('approval_policy', 'sandbox_mode', 'approvals_reviewer', 'notify'))]
    else:
        kept = []
    body = ['approval_policy = "never"', 'sandbox_mode = "danger-full-access"'] + kept
    project = f'[projects."{repo}"]'
    if project not in kept:
        body += ['', project, 'trust_level = "trusted"']
    (codex_home / 'config.toml').write_text('\n'.join(body) + '\n')
    source_auth = source_home / 'auth.json'
    if source_auth.exists():
        (codex_home / 'auth.json').symlink_to(source_auth)
    return codex_home


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--live-agents', action='store_true')
    args = parser.parse_args()
    if not args.live_agents:
        parser.error('this smoke requires explicit --live-agents')
    repo = Path(__file__).resolve().parents[1]
    binary = str(Path(args.binary).resolve())
    report = {'server': 'isolated', 'coordinator_kind': 'codex', 'request': '开始111布局,每个pane 开启pi'}
    with tempfile.TemporaryDirectory(prefix='recipe-live-', dir='/private/tmp') as folder:
        root = Path(folder)
        config = root / 'config.toml'
        config.write_text('[keys]\nprefix="ctrl+b"\n')
        env = {k: v for k, v in os.environ.items() if not k.startswith('HERDR_')}
        env.update(HERDR_SOCKET_PATH=str(root / 's.sock'), HERDR_CONFIG_PATH=str(config),
                   XDG_CONFIG_HOME=str(root / 'config'), XDG_STATE_HOME=str(root / 'state'))
        env['CODEX_HOME'] = str(provision_codex_home(root, repo))
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'herdr').symlink_to(binary)
        env['PATH'] = str(bin_dir) + os.pathsep + env['PATH']

        def run(*items, timeout=20):
            result = subprocess.run(items, env=env, cwd=repo, capture_output=True, text=True, timeout=timeout)
            if result.returncode:
                raise RuntimeError((items[:3], result.returncode, result.stderr))
            return result.stdout

        def cli(*items):
            raw = run(binary, *items)
            return json.loads(raw)['result'] if raw.strip() else {}

        with (root / 'server.log').open('w') as log:
            server = subprocess.Popen([binary, 'server'], env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if (root / 's.sock').exists():
                        break
                    if server.poll() is not None:
                        raise RuntimeError('isolated server exited')
                    time.sleep(.1)
                else:
                    raise RuntimeError('isolated socket did not appear')
                launched = run('bash', str(repo / 'scripts/hteam.sh'), '-k', 'codex', '--no-focus',
                               '-p', report['request'], timeout=75)
                parts = dict(item.split('=', 1) for item in launched.split())
                main_ws, main_pane = parts['ws'], parts['panes']
                report.update(coordinator_workspace=main_ws, coordinator_pane=main_pane)
                print('LAUNCH', launched.strip(), flush=True)
                deadline = time.monotonic() + 180
                ready = False
                while time.monotonic() < deadline:
                    workspaces = cli('workspace', 'list')['workspaces']
                    targets = [w['workspace_id'] for w in workspaces if w['workspace_id'] != main_ws]
                    agents = cli('agent', 'list')['agents']
                    if len(targets) == 1:
                        workers = [a for a in agents if a.get('workspace_id') == targets[0]]
                        coordinator = [a for a in agents if a.get('pane_id') == main_pane]
                        if (len(workers) == 3 and len(coordinator) == 1
                                and all(a.get('agent_status') in ('idle', 'done') for a in workers + coordinator)):
                            ready = True
                            break
                    time.sleep(2)
                if not ready:
                    transcript = run(binary, 'agent', 'read', main_pane, '--source', 'visible', '--format', 'text')
                    raise AssertionError(('111 Recipe not prepared without further answers', transcript[-5000:]))
                assert len(workspaces) == 2, workspaces
                assert coordinator[0]['agent'] == 'codex', coordinator
                assert all(a['agent'] == 'pi' for a in workers), workers
                assert len(cli('pane', 'list', '--workspace', main_ws)['panes']) == 1
                panes = cli('pane', 'list', '--workspace', targets[0])['panes']
                assert len(panes) == 3 and len({p['tab_id'] for p in panes}) == 1
                rects = cli('pane', 'layout', '--pane', panes[0]['pane_id'])['layout']['panes']
                actual = sorted((p['rect']['x'], p['rect']['y'], p['rect']['width'], p['rect']['height']) for p in rects)
                left = min(r[0] for r in actual)
                top = min(r[1] for r in actual)
                width = max(r[0] + r[2] for r in actual) - left
                height = max(r[1] + r[3] for r in actual) - top
                expected = [(0, 0, .5, 1), (.5, 0, .25, 1), (.75, 0, .25, 1)]
                assert len(actual) == 3
                for got, want in zip(actual, expected):
                    normalized = (got[0] - left, got[1] - top, got[2], got[3])
                    assert all(abs(a - f * d) <= 1 for a, f, d in
                               zip(normalized, want, (width, height, width, height))), (actual, expected)
                report.update(recipe_workspace=targets[0], coordinator_preserved=True,
                              pi_count=3, geometry_111=True, completed_without_answers=True)
                for workspace in cli('workspace', 'list')['workspaces']:
                    cli('workspace', 'close', workspace['workspace_id'])
                assert not cli('workspace', 'list')['workspaces']
                report['cleaned'] = True
                print('FIXED_RECIPE_WITHOUT_CLARIFICATION_OK', flush=True)
            except Exception as error:
                report['error'] = str(error)
                raise
            finally:
                try:
                    cli('server', 'stop')
                finally:
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.terminate()
                        server.wait(timeout=5)
                    report['server_stopped'] = server.poll() is not None
                    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
