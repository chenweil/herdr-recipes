"""Manual two-entry, nine-layout smoke using a temporary isolated Herdr server.

Run: python3 tests/smoke_herdr_layouts.py --binary /path/to/herdr --output /tmp/smoke.json
Requires jq and Bash; starts no agents and leaves the user server/config untouched.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--binary', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
binary = str(Path(args.binary).resolve())
repo = Path(__file__).resolve().parents[1]
# Expected creation-order rectangles as fractions of the workspace area.
expected = {
    '11': [(0,0,.5,1),(.5,0,.5,1)],
    '12': [(0,0,.5,1),(.5,0,.5,.5),(.5,.5,.5,.5)],
    '21': [(0,0,.5,.5),(.5,0,.5,1),(0,.5,.5,.5)],
    '13': [(0,0,.5,1),(.5,0,.5,1/3),(.5,1/3,.5,1/3),(.5,2/3,.5,1/3)],
    '31': [(0,0,.5,1/3),(.5,0,.5,1),(0,1/3,.5,1/3),(0,2/3,.5,1/3)],
    '22': [(0,0,.5,.5),(.5,0,.5,.5),(.5,.5,.5,.5),(0,.5,.5,.5)],
    '111': [(0,0,.5,1),(.5,0,.25,1),(.75,0,.25,1)],
    '221': [(0,0,.25,.5),(.25,0,.25,.5),(.5,0,.5,1),(0,.5,.25,.5),(.25,.5,.25,.5)],
    '122': [(0,0,.5,1),(.5,0,.25,.5),(.75,0,.25,.5),(.75,.5,.25,.5),(.5,.5,.25,.5)],
}
with tempfile.TemporaryDirectory(prefix='hr-smoke-', dir='/private/tmp') as folder:
    root = Path(folder)
    config = root / 'config.toml'
    config.write_text('[keys]\nprefix = "ctrl+b"\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith('HERDR_')}
    env.update(HERDR_SOCKET_PATH=str(root / 'server.sock'), HERDR_CONFIG_PATH=str(config),
               XDG_CONFIG_HOME=str(root / 'config'), XDG_STATE_HOME=str(root / 'state'))
    bin_dir = root / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'herdr').symlink_to(binary)
    env['PATH'] = str(bin_dir) + os.pathsep + env['PATH']
    def command(*items):
        result = subprocess.run(items, env=env, cwd=repo, text=True, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError(f'{items}: exit={result.returncode}: {result.stderr or result.stdout}')
        return result.stdout
    def cli(*items):
        payload = json.loads(command(binary, *items))
        if 'error' in payload:
            raise RuntimeError(payload['error'])
        return payload['result']
    report = {'version': command(binary, '--version').strip(), 'server': 'isolated', 'layouts': []}
    with (root / 'server.log').open('w+') as log:
        server = subprocess.Popen([binary, 'server'], env=env, cwd=repo, stdout=log, stderr=log)
        try:
            for _ in range(100):
                if (root / 'server.sock').exists():
                    break
                if server.poll() is not None:
                    log.seek(0)
                    raise RuntimeError('server exited: ' + log.read())
                time.sleep(.1)
            else:
                raise RuntimeError('server socket did not appear')
            before = {w['workspace_id'] for w in cli('workspace', 'list')['workspaces']}
            for entry in ('hopen.sh', 'hopen-once.sh'):
                for code, positions in expected.items():
                    flags = [code, '-n'] if entry == 'hopen.sh' else ['-l', code, '-n']
                    out = command('bash', str(repo / 'scripts' / entry), *flags)
                    line = out.strip()
                    assert line.startswith('ws=') and ' panes=' in line, line
                    ws, panes = line.split(' panes=')
                    ws = ws.removeprefix('ws=')
                    panes = panes.split()
                    try:
                        layout = cli('pane', 'layout', '--pane', panes[0])['layout']
                        area = layout['area']
                        assert len(layout['panes']) == len(positions) == len(panes)
                        actual = {p['pane_id']: p['rect'] for p in layout['panes']}
                        left = min(r['x'] for r in actual.values())
                        top = min(r['y'] for r in actual.values())
                        width = max(r['x'] + r['width'] for r in actual.values()) - left
                        height = max(r['y'] + r['height'] for r in actual.values()) - top
                        content_area = dict(x=left, y=top, width=width, height=height)
                        for pane, want in zip(panes, positions):
                            rect = actual[pane]
                            for name, fraction, dimension, origin in zip(('x','y','width','height'), want,
                                                                        (width,height,width,height),
                                                                        (left,top,0,0)):
                                assert abs(rect[name] - (origin + fraction * dimension)) <= 1, (entry, code, pane, rect, want)
                        report['layouts'].append({'entry': entry, 'code': code, 'area': area, 'content_area': content_area,
                                                  'panes': [actual[p] for p in panes]})
                        print(report['version'], entry, code, 'OK', flush=True)
                    finally:
                        cli('workspace', 'close', ws)
            after = {w['workspace_id'] for w in cli('workspace', 'list')['workspaces']}
            assert after == before, (before, after)
            report['workspaces_cleaned'] = True
        finally:
            try:
                command(binary, 'server', 'stop')
            finally:
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.terminate()
                    server.wait(timeout=5)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
