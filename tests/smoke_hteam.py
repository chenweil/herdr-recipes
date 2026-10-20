"""Opt-in live model smoke: standby worker, later delegation, and evidence-backed summary.

Requires authenticated pi workers and coordinator. Uses isolated Herdr state and
real model calls. Add --workers 3 to check top-one/bottom-two worker geometry.
Run with --live-agents --binary /path/to/herdr --output /tmp/team.json.
"""
import argparse
import json
import os
import pathlib
import subprocess
import tempfile
import time

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--binary',required=True)
parser.add_argument('--output',required=True)
parser.add_argument('--kind',default='pi',help='coordinator kind; workers are pi')
parser.add_argument('--workers',type=int,choices=(1,3),default=1,help='standby workers; three checks top-one/bottom-two geometry')
parser.add_argument('--timeout',type=int,default=240,help='seconds per phase, including local evidence records')
parser.add_argument('--live-agents',action='store_true',help='explicitly allow real model calls')
args=parser.parse_args()
if not args.live_agents:
    parser.error('this smoke requires explicit --live-agents')
repo=pathlib.Path(__file__).resolve().parents[1]
binary=str(pathlib.Path(args.binary).resolve())
with tempfile.TemporaryDirectory(prefix='team-live-',dir='/tmp') as folder:
    root=pathlib.Path(folder)
    project=root/'project';project.mkdir()
    config=root/'config.toml';config.write_text('[keys]\nprefix="ctrl+b"\n')
    env={k:v for k,v in os.environ.items() if not k.startswith('HERDR_')}
    env.update(HERDR_SOCKET_PATH=str(root/'s.sock'),HERDR_CONFIG_PATH=str(config),XDG_CONFIG_HOME=str(root/'config'),XDG_STATE_HOME=str(root/'state'))
    bin_dir=root/'bin';bin_dir.mkdir();(bin_dir/'herdr').symlink_to(binary)
    env['PATH']=str(bin_dir)+os.pathsep+env['PATH']
    def run(*args,timeout=20):
        result=subprocess.run(args,env=env,cwd=repo,capture_output=True,text=True,timeout=timeout)
        if result.returncode: raise RuntimeError((args[:3],result.returncode,result.stderr))
        return result.stdout
    def cli(*args):
        raw=run(binary,*args)
        return json.loads(raw).get('result',{}) if raw.strip() else {}
    def output(target):
        return run(binary,'agent','read',target,'--source','visible','--format','text')
    report={'server':'isolated','coordinator_kind':args.kind}
    with (root/'server.log').open('w') as log:
        server=subprocess.Popen([binary,'server'],env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                if (root/'s.sock').exists():break
                if server.poll() is not None:raise RuntimeError('server exited')
                time.sleep(.1)
            initial=f'验证对话式协调功能：请用 Herdr CLI 在本 tab 的右侧准备 {args.workers} 个 pi worker，为每个生成唯一名称，只发送角色说明和待命指令，先不要给计算或代码任务。只允许写本地执行记录，不修改项目源码，不要提交、推送或安装集成，不调用子 agent 工具替代真实 Herdr pane。准备好后简短汇报，等待我继续委托。'
            launched=run('bash',str(repo/'scripts/hteam.sh'),'-C',str(project),'-k',args.kind,'--no-focus','-p',initial,timeout=75)
            print('LAUNCH',launched.strip(),flush=True)
            parts=dict(part.split('=',1) for part in launched.strip().split())
            ws=parts['ws'];coordinator=parts['coordinator'];main=parts['panes']
            deadline=time.monotonic()+args.timeout
            workers=[]
            while time.monotonic()<deadline:
                panes=cli('pane','list','--workspace',ws)['panes']
                agents=cli('agent','list')['agents']
                workers=[a for a in agents if a.get('workspace_id')==ws and a.get('pane_id')!=main]
                if len(workers)==args.workers and all(a.get('agent_status') in ('idle','done') for a in agents):break
                time.sleep(2)
            assert len(workers)==args.workers,('expected ready workers',args.workers,len(workers))
            assert all(a.get('agent_status') in ('idle','done') for a in agents),'agents did not settle'
            assert len(panes)==args.workers+1 and len({p['tab_id'] for p in panes})==1
            layout=cli('pane','layout','--pane',main)['layout']
            rects={p['pane_id']:p['rect'] for p in layout['panes']}
            left=min(r['x'] for r in rects.values())
            right=max(r['x']+r['width'] for r in rects.values())
            top=min(r['y'] for r in rects.values())
            bottom=max(r['y']+r['height'] for r in rects.values())
            main_rect=rects[main]
            assert main_rect['x']==left and main_rect['y']==top
            assert main_rect['height']==bottom-top and abs(main_rect['width']-(right-left)/2)<=1
            report['main_left_half']=True
            if args.workers==3:
                actual=sorted((r['x']-left,r['y']-top,r['width'],r['height'])
                              for pane,r in rects.items() if pane!=main)
                width=right-left; height=bottom-top
                expected=sorted((x*width,y*height,w*width,h*height) for x,y,w,h in
                                ((.5,0,.5,.5),(.5,.5,.25,.5),(.75,.5,.25,.5)))
                assert len(actual)==3 and all(abs(a-e)<=1 for got,want in zip(actual,expected)
                                             for a,e in zip(got,want)),('top-one/bottom-two layout mismatch',actual,expected)
                report['worker_layout']='top-one-bottom-two'
            report['worker_count']=args.workers
            report['panes']={pane:rect for pane,rect in rects.items()}
            worker=workers[0].get('name') or workers[0]['pane_id']
            print('STANDBY_READY',worker,flush=True)
            later=f'现在把任务 T2 委托给待命 worker {worker}：让它计算十九乘二十三，仅输出 TEAM_PROBE_RESULT: 后跟计算结果。你必须通过 Herdr agent prompt 委托并读取该 worker 的实际输出，不能自己代替 worker 计算。拿到结果后，你在主 pane 输出 TEAM_SUMMARY: 后跟结果。其它 worker 继续待命。仅可更新本地执行记录，不修改源码或再开 worker。'
            cli('agent','prompt',coordinator,later)
            deadline=time.monotonic()+args.timeout
            success=False
            while time.monotonic()<deadline:
                worker_text=output(worker)
                main_text=output(coordinator)
                if 'TEAM_PROBE_RESULT: 437' in worker_text and 'TEAM_SUMMARY: 437' in main_text:
                    success=True;break
                time.sleep(2)
            assert success,'delegation/result summary markers not observed'
            assert len(cli('pane','list','--workspace',ws)['panes'])==args.workers+1
            assert len(cli('workspace','list')['workspaces'])==1
            report.update(workspace=ws,main=main,worker=worker,standby_prepared=True,later_delegation=True,worker_result=437,coordinator_summary=437,same_tab=True)
            print('DELEGATION_AND_SUMMARY_OK',flush=True)
            cli('workspace','close',ws)
            assert not cli('workspace','list')['workspaces']
            report['cleaned']=True
        finally:
            try:cli('server','stop')
            finally:
                try:server.wait(timeout=5)
                except subprocess.TimeoutExpired:server.terminate();server.wait(timeout=5)
        pathlib.Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
