"""Sentinel private, relocatable Windows launcher. No system Python or Docker."""
from __future__ import annotations
import atexit, base64, ctypes, hashlib, hmac, importlib, json, msvcrt, os
import shutil, socket, subprocess, sys, tempfile, threading, time, traceback, uuid, webbrowser
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler, Request

ROOT=Path(__file__).resolve().parent
APP=ROOT/'app'
WINDOWS_APP_ID='Sentinel.AISecurityIntelligence'
WINDOW_ICON=ROOT/'portable'/'sentinel.ico'
# Embedded Python isolates sys.path; resolve these from the script itself.
for _path in (ROOT,APP):
    if str(_path) not in sys.path:sys.path.insert(0,str(_path))
CONTROL=ROOT/'data'
CONTROL.mkdir(exist_ok=True)
SETTINGS=CONTROL/'portable-settings.json'
STATE=CONTROL/'instance.json'
NO_WINDOW=getattr(subprocess,'CREATE_NO_WINDOW',0)
OPENER=build_opener(ProxyHandler({}))
SHUTDOWN=threading.Event()
WINDOW=None
SERVER=None
PG=None
GRAPH=None
RUNTIME=None
INSTANCE_ID=''
TOKEN=uuid.uuid4().hex
UI_MODE='browser' if '--browser' in sys.argv or '--headless' in sys.argv else 'webview'

def read_json(p,default=None):
    try:return json.loads(Path(p).read_text(encoding='utf-8'))
    except (OSError,ValueError):return {} if default is None else default

def write_json(p,value):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_name(p.name+'.writing')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(tmp,p)

def http_json(url,data=None,token=None,timeout=4):
    headers={}
    if token:headers['Authorization']='Bearer '+token
    if data is not None:headers['Content-Type']='application/json'
    req=Request(url,data=json.dumps(data).encode() if data is not None else None,headers=headers)
    with OPENER.open(req,timeout=timeout) as f:return json.load(f)

def message(text):
    if '--headless' in sys.argv:
        print(str(text),flush=True);return
    ctypes.windll.user32.MessageBoxW(None,str(text),'Sentinel',0x10)

def log(text):
    stamp=time.strftime('%Y-%m-%d %H:%M:%S')
    line=f'[{stamp}] {text}'
    print(line,flush=True)
    if WINDOW:
        try:WINDOW.evaluate_js('document.getElementById("status").textContent='+json.dumps(str(text)))
        except Exception:pass
    if INSTANCE_ID:
        state=read_json(STATE)
        if state.get('pid')==os.getpid():
            state['progress']=str(text);write_json(STATE,state)

def configure_window_identity():
    # The C# EXE starts pythonw.exe, which owns the actual native window.
    # Give that process its own taskbar group before any windows are created.
    if sys.platform=='win32':
        try:
            set_app_id=ctypes.WinDLL('shell32').SetCurrentProcessExplicitAppUserModelID
            set_app_id.argtypes=[ctypes.c_wchar_p]
            set_app_id.restype=ctypes.c_long
            result=set_app_id(WINDOWS_APP_ID)
            if result<0:
                log(f'任务栏应用标识设置失败：0x{result & 0xffffffff:08X}')
        except OSError as exc:
            log('任务栏应用标识设置失败：'+str(exc))
    if WINDOW_ICON.is_file():return str(WINDOW_ICON)
    log('窗口图标文件缺失：'+str(WINDOW_ICON))
    return None

def available_port(preferred):
    for port in range(preferred,preferred+100):
        try:
            with socket.socket() as s:s.bind(('127.0.0.1',port))
            return port
        except OSError:pass
    raise RuntimeError('没有可用的本机端口，请关闭无关应用后重试。')

def assert_ascii(path):
    if not str(path).isascii():raise RuntimeError('内部数据库路径必须使用英文目录。')

def native_component(name):
    source=ROOT/name
    if str(source).isascii():return source
    dest=RUNTIME/'native'/name
    if not (dest/'.complete.json').exists():
        log('正在准备本机数据库环境：'+name+'（仅首次）')
        shutil.copytree(source,dest,dirs_exist_ok=True)
        write_json(dest/'.complete.json',{'component':name,'instance_id':INSTANCE_ID})
    return dest

def configure():
    global INSTANCE_ID,RUNTIME
    cfg=read_json(SETTINGS)
    INSTANCE_ID=cfg.setdefault('instance_id',uuid.uuid4().hex)
    default=CONTROL/'runtime'
    if not str(default).isascii():
        default=Path(os.environ.get('PUBLIC','C:/Users/Public'))/'SentinelData'/('portable-'+INSTANCE_ID)
    configured=cfg.get('configured_dir')
    RUNTIME=Path(configured) if configured and str(configured).isascii() else default
    assert_ascii(RUNTIME)
    previous=Path(cfg['last_used']) if cfg.get('last_used') else None
    if previous and previous!=RUNTIME and (previous/'pgdata'/'PG_VERSION').exists() and not (RUNTIME/'pgdata'/'PG_VERSION').exists():
        if (previous/'pgdata'/'postmaster.pid').exists():
            raise RuntimeError('旧数据目录仍有数据库运行记录，先正常停止旧实例后再迁移。')
        log('正在复制现有数据到新位置，原数据保留备份。')
        shutil.copytree(previous,RUNTIME,dirs_exist_ok=True)
    RUNTIME.mkdir(parents=True,exist_ok=True)
    owner=read_json(RUNTIME/'owner.json')
    if owner and owner.get('instance_id')!=INSTANCE_ID:
        raise RuntimeError('所选数据目录属于另一份 Sentinel，请改用独立目录。')
    write_json(RUNTIME/'owner.json',{'instance_id':INSTANCE_ID})
    cfg['last_used']=str(RUNTIME)
    write_json(SETTINGS,cfg)
    envs={'SENTINEL_ACTIVE_DATA_DIR':str(RUNTIME),'SENTINEL_CONTROL_FILE':str(SETTINGS),
          'SENTINEL_PORTABLE_ROOT':str(ROOT),'OMP_NUM_THREADS':'2','MKL_NUM_THREADS':'2',
          'TOKENIZERS_PARALLELISM':'false','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
    for key,value in envs.items():os.environ[key]=value
    bypass={x.strip() for x in os.environ.get('NO_PROXY','').split(',') if x.strip()}
    bypass.update(['localhost','127.0.0.1','::1','api.openveer.com'])
    os.environ['NO_PROXY']=os.environ['no_proxy']=','.join(sorted(bypass))
    os.chdir(APP)
    if str(APP) not in sys.path:sys.path.insert(0,str(APP))
    # Environment settings override only local service locations, leaving model keys intact.
    from dotenv import dotenv_values
    env=dict(dotenv_values(APP/'.env.example'));env.update(dotenv_values(APP/'.env'))
    user=env.get('POSTGRES_USER') or 'aiintel'
    db=env.get('POSTGRES_DB') or 'ai_security_intel'
    password=env.get('POSTGRES_PASSWORD') or uuid.uuid4().hex
    ports=cfg.get('ports',{})
    ports={k:available_port(int(ports.get(k,v))) for k,v in [('api',18080),('postgres',15433),('bolt',18687),('neo4j_http',18474)]}
    cfg['ports']=ports;write_json(SETTINGS,cfg)
    overrides={'POSTGRES_HOST':'127.0.0.1','POSTGRES_PORT':str(ports['postgres']),
        'POSTGRES_USER':user,'POSTGRES_DB':db,'POSTGRES_PASSWORD':password,
        'NEO4J_HOST':'127.0.0.1','NEO4J_PORT':str(ports['neo4j_http']),
        'NEO4J_BOLT_PORT':str(ports['bolt']),'NEO4J_USER':env.get('NEO4J_USER') or 'neo4j',
        'NEO4J_PASSWORD':env.get('NEO4J_PASSWORD') or password,
        'API_HOST':'127.0.0.1','API_PORT':str(ports['api'])}
    os.environ.update(overrides)
    return cfg,env,user,db,password

class PostgresRuntime:
    def __init__(self,home,data,port,user,db,password):
        self.home=home;self.data=data;self.port=port;self.user=user;self.db=db;self.password=password
        self.started=False
    def run(self,name,*args,check=True,timeout=180):
        env=os.environ.copy();env['PGPASSWORD']=self.password;env['PGCLIENTENCODING']='UTF8'
        # A long-lived postgres child can inherit pg_ctl's standard handles.
        # Files avoid subprocess.communicate() waiting for pipe EOF forever.
        with tempfile.TemporaryFile() as out,tempfile.TemporaryFile() as err:
            proc=subprocess.run([str(self.home/'bin'/name),*map(str,args)],stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                env=env,creationflags=NO_WINDOW,timeout=timeout)
            out.seek(0);err.seek(0)
            proc.stdout=out.read().decode('utf-8','replace');proc.stderr=err.read().decode('utf-8','replace')
        if check and proc.returncode:raise RuntimeError(name+' 执行失败：'+proc.stderr[-1800:])
        return proc
    def sql(self,text,db=None):
        return self.run('psql.exe','-X','-h','127.0.0.1','-p',self.port,'-U',self.user,'-d',db or self.db,'-At','-v','ON_ERROR_STOP=1','-c',text).stdout.strip()
    def counts(self):
        names=list(read_json(CONTROL/'snapshot-manifest.json')['tables'])
        query='SELECT json_object_agg(t,n) FROM ('+' UNION ALL '.join("SELECT '%s' AS t,count(*) AS n FROM public.%s"%(x,x) for x in names)+') s'
        return json.loads(self.sql(query))
    def start(self):
        new=not (self.data/'PG_VERSION').exists()
        if new:
            log('首次初始化本地数据库…')
            self.data.mkdir(parents=True,exist_ok=True)
            secret=RUNTIME/'.init-secret'
            secret.write_text(self.password+'\n',encoding='utf-8')
            try:self.run('initdb.exe','-D',self.data,'-U',self.user,'--encoding=UTF8','--locale=C','--auth-local=trust','--auth-host=scram-sha-256','--pwfile='+str(secret))
            finally:secret.write_text('',encoding='utf-8')
        # A port is chosen privately; never connect to a random existing PostgreSQL service.
        status=self.run('pg_ctl.exe','-D',self.data,'status',check=False)
        if status.returncode==0:raise RuntimeError('本包数据库仍在运行。请先双击“停止系统.exe”，再打开。')
        log('正在启动本地情报数据库…')
        self.started=True
        self.run('pg_ctl.exe','-D',self.data,'-l',RUNTIME/'postgres.log','-o',f'-h 127.0.0.1 -p {self.port} -c max_connections=50 -c shared_buffers=128MB','-w','-t','90','start')
        actual=Path(self.sql('SHOW data_directory',db='postgres')).resolve()
        if os.path.normcase(str(actual))!=os.path.normcase(str(self.data.resolve())):
            raise RuntimeError('数据库身份校验未通过。')
        if self.sql("SELECT count(*) FROM pg_database WHERE datname='"+self.db.replace("'","''")+"'",db='postgres')=='0':
            self.run('createdb.exe','-h','127.0.0.1','-p',self.port,'-U',self.user,'--encoding=UTF8','--template=template0',self.db)
        ready=RUNTIME/'database-ready.json'
        if ready.exists():return
        existing=int(self.sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"))
        if existing:
            raise RuntimeError('发现已有未核验的数据表；为保护数据，不会覆盖导入。请保留日志联系维护者。')
        manifest=read_json(CONTROL/'snapshot-manifest.json')
        archive=CONTROL/'snapshot.dump'
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=manifest['sha256']:
            raise RuntimeError('随包数据快照校验失败，请重新解压完整 ZIP。')
        log('正在导入原有情报、富化结果和配置（仅首次）…')
        self.run('pg_restore.exe','-h','127.0.0.1','-p',self.port,'-U',self.user,'-d',self.db,'--no-owner','--no-privileges','--single-transaction','--exit-on-error',archive,timeout=900)
        counts=self.counts()
        if counts!=manifest['tables']:raise RuntimeError('导入后数据条数不一致，未标记为完成。')
        vectors={t:int(self.sql(f'SELECT count(*) FROM {t} WHERE embedding IS NOT NULL')) for t in manifest['vectors']}
        if vectors!=manifest['vectors']:raise RuntimeError('向量数据校验失败。')
        if int(self.sql('SELECT count(*) FROM enrichments e LEFT JOIN vulnerabilities v ON e.vulnerability_id=v.id WHERE v.id IS NULL')):
            raise RuntimeError('富化记录关联校验失败。')
        self.sql('SELECT id FROM vulnerabilities WHERE embedding IS NOT NULL ORDER BY embedding <=> (SELECT embedding FROM vulnerabilities WHERE embedding IS NOT NULL LIMIT 1) LIMIT 3')
        write_json(ready,{'snapshot_sha256':manifest['sha256'],'tables':counts,'vectors':vectors,
            'vector_version':self.sql("SELECT extversion FROM pg_extension WHERE extname='vector'"),'instance_id':INSTANCE_ID})
        log('情报数据已核对完整：'+str(counts['vulnerabilities'])+' 条漏洞。')
    def stop(self):
        if self.started:
            log('正在安全关闭本包数据库…')
            self.run('pg_ctl.exe','-D',self.data,'-m','fast','-w','-t','60','stop',check=False,timeout=70)
            self.started=False

def restore_runtime_state():
    state=read_json(RUNTIME/'runtime-state.json') or read_json(CONTROL/'initial-runtime-state.json')
    from 项目源码.拓展模块.controller import get_controller
    c=get_controller();status=state.get('roen_status',{})
    with c._lock:
        for key,attr in [('locked','is_locked'),('total_detections','total_detections'),('total_attacks','total_attacks'),('simulated_events','simulated_events'),('total_encrypted','total_encrypted'),('last_detection','last_detection')]:
            if key in status:setattr(c,attr,status[key])
        if isinstance(status.get('config'),dict):c.cfg.update(status['config'])
        history=state.get('roen_history',[])
        if isinstance(history,list):c.history.extend(reversed(history))
        for image in c.images:
            saved=state.get('encrypted_images',{}).get(image['id'])
            if saved:
                image['encrypted']=base64.b64decode(saved['data'])
                image['encrypted_name']=image['id']+'_encrypted.png'
                if saved.get('key_hash'):image['key_hash']=saved['key_hash']
                else:
                    import io,numpy as np
                    from PIL import Image
                    image['key_hash']=','.join(map(str,c._enc.generate_hash(np.array(Image.open(io.BytesIO(image['original'])).convert('L')))))
    c.ensure_ready()
    return state

def save_runtime_state():
    try:
        from 项目源码.拓展模块.controller import get_controller
        from 项目源码.数据采集.scheduler import get_scheduler
        c=get_controller()
        with c._lock:
            state={'roen_status':c.status(),'roen_history':c.history_list(200),'scheduler_paused':get_scheduler().paused,
                'encrypted_images':{image['id']:{'data':base64.b64encode(image['encrypted']).decode(),'key_hash':image['key_hash']} for image in c.images if image['encrypted'] is not None}}
        write_json(RUNTIME/'runtime-state.json',state)
    except Exception:traceback.print_exc()

def shutdown():
    SHUTDOWN.set()
    if SERVER:SERVER.should_exit=True

def cancelled():
    if read_json(CONTROL/'stop-request.json').get('pid')==os.getpid():SHUTDOWN.set()
    return SHUTDOWN.is_set()

def add_instance_routes(app,url):
    from fastapi import Request,HTTPException
    # Define request type explicitly because this launcher uses postponed annotations.
    async def identity():
        return {'instance_id':INSTANCE_ID,'pid':os.getpid(),'app_root':str(ROOT),'data_dir':str(RUNTIME),'url':url}
    async def stop_request(request):
        auth=request.headers.get('authorization','')
        if not hmac.compare_digest(auth,'Bearer '+TOKEN):raise HTTPException(403)
        threading.Timer(.1,shutdown).start()
        return {'ok':True,'instance_id':INSTANCE_ID}
    stop_request.__annotations__={'request':Request}
    app.add_api_route('/api/__portable__/instance',identity,methods=['GET'],include_in_schema=False)
    app.router.routes.insert(0,app.router.routes.pop())
    app.add_api_route('/api/__portable__/stop',stop_request,methods=['POST'],include_in_schema=False)
    app.router.routes.insert(0,app.router.routes.pop())

def startup():
    global PG,GRAPH,SERVER
    cfg,env,user,db,password=configure()
    ports=cfg['ports'];url='http://127.0.0.1:'+str(ports['api'])
    write_json(STATE,{'pid':os.getpid(),'instance_id':INSTANCE_ID,'status':'starting','url':url,'token':TOKEN,'app_root':str(ROOT),'data_dir':str(RUNTIME)})
    log('正在准备 Sentinel 完整运行环境…')
    PG=PostgresRuntime(native_component('pgsql'),RUNTIME/'pgdata',ports['postgres'],user,db,password)
    PG.start()
    if cancelled():return
    graph_data=RUNTIME/'neo4j'
    if not (RUNTIME/'graph-seed-copied.json').exists():
        log('正在载入原有知识图谱（仅首次）…')
        shutil.copytree(CONTROL/'neo4j-seed',graph_data,dirs_exist_ok=True)
        write_json(RUNTIME/'graph-seed-copied.json',{'instance_id':INSTANCE_ID})
    from portable.neo4j_runtime import Neo4jRuntime
    GRAPH=Neo4jRuntime(home=native_component('neo4j'),java_home=native_component('java'),data_dir=graph_data,
        run_dir=RUNTIME/'neo4j-run',logs_dir=RUNTIME/'neo4j-logs',http_port=ports['neo4j_http'],bolt_port=ports['bolt'],
        username=env.get('NEO4J_USER') or 'neo4j',password=env.get('NEO4J_PASSWORD') or password)
    log('正在启动本地知识图谱…')
    GRAPH.start(timeout=180)
    if cancelled():return
    log('知识图谱已就绪，正在载入应用和本地检测模型…')
    initial=read_json(RUNTIME/'runtime-state.json') or read_json(CONTROL/'initial-runtime-state.json')
    os.environ['SENTINEL_START_PAUSED']='1' if initial.get('scheduler_paused',True) else '0'
    app_module=importlib.import_module('项目源码.后端服务.app')
    restore_runtime_state()
    log('正在启动情报平台服务…')
    add_instance_routes(app_module.app,url)
    import uvicorn
    SERVER=uvicorn.Server(uvicorn.Config(app_module.app,host='127.0.0.1',port=ports['api'],log_level='warning',access_log=False))
    thread=threading.Thread(target=SERVER.run,name='sentinel-api',daemon=True);thread.start()
    deadline=time.monotonic()+120
    while time.monotonic()<deadline and not SHUTDOWN.is_set():
        if SERVER.started:
            verified=http_json(url+'/api/__portable__/instance')
            if verified.get('pid')!=os.getpid() or verified.get('instance_id')!=INSTANCE_ID:raise RuntimeError('启动实例身份不匹配。')
            break
        if not thread.is_alive():raise RuntimeError('后端服务启动失败，请查看启动日志。')
        time.sleep(.3)
    else:raise RuntimeError('等待后端启动超时。')
    log('系统已就绪。')
    current=read_json(STATE);current.update(status='ready',ui_mode=UI_MODE);write_json(STATE,current)
    if WINDOW:WINDOW.load_url(url)
    elif '--headless' not in sys.argv:webbrowser.open(url)
    while not SHUTDOWN.wait(10):save_runtime_state()
    save_runtime_state()
    try:
        from 项目源码.拓展模块.controller import get_controller
        get_controller().stop_monitoring()
    except Exception:pass
    SERVER.should_exit=True;thread.join(timeout=30)

def foreground(pid):
    found=[]
    callback=ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_void_p)
    def visit(hwnd,lparam):
        process=ctypes.c_ulong();ctypes.windll.user32.GetWindowThreadProcessId(hwnd,ctypes.byref(process))
        if process.value==pid and ctypes.windll.user32.IsWindowVisible(hwnd):found.append(hwnd)
        return True
    ctypes.windll.user32.EnumWindows(callback(visit),0)
    if found:
        ctypes.windll.user32.ShowWindow(found[0],9);ctypes.windll.user32.SetForegroundWindow(found[0]);return True
    return False

def second_instance(stop=False):
    end=time.monotonic()+(5 if stop else 45)
    while time.monotonic()<end:
        state=read_json(STATE)
        if state.get('status')=='ready':
            try:
                verified=http_json(state['url']+'/api/__portable__/instance')
                if verified.get('instance_id')!=state.get('instance_id') or verified.get('pid')!=state.get('pid'):break
                if stop:http_json(state['url']+'/api/__portable__/stop',data={},token=state['token'])
                elif not foreground(state['pid']) and '--headless' not in sys.argv:webbrowser.open(state['url'])
                return True
            except Exception:pass
        if stop and state.get('status')=='starting':
            write_json(CONTROL/'stop-request.json',{'pid':state.get('pid')});return True
        time.sleep(.5)
    return False

SPLASH='''<!doctype html><html><meta charset="utf-8"><style>body{background:#092d25;color:#e9ead9;font:18px "Microsoft YaHei";display:grid;place-items:center;height:95vh;text-align:center}h1{color:#b6ed72;letter-spacing:7px;font-size:40px}.loader{height:3px;width:360px;background:#2c5f50;margin:32px auto;overflow:hidden}.loader:after{content:"";display:block;height:100%;width:40%;background:#b6ed72;animation:load 1.8s infinite}@keyframes load{from{transform:translateX(-120%)}to{transform:translateX(350%)}}#status{font-size:15px;color:#bccdc2}main{position:relative}.splash-logo{position:absolute;left:50%;bottom:100%;transform:translateX(-50%);width:120px;height:120px;margin-bottom:12px;pointer-events:none}.splash-logo svg{display:block;width:100%;height:100%}</style><body><main><div class="splash-logo" aria-hidden="true"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" fill="none"><defs><linearGradient id="sentinel-body" x1="12" y1="40" x2="36" y2="8"><stop offset="0%" stop-color="#8ed321"/><stop offset="55%" stop-color="#b6f34a"/><stop offset="100%" stop-color="#4be1ec"/></linearGradient><linearGradient id="sentinel-radar" x1="20" y1="20" x2="8" y2="8"><stop offset="0%" stop-color="#4be1ec" stop-opacity="0.9"/><stop offset="100%" stop-color="#4be1ec" stop-opacity="0.12"/></linearGradient></defs><g stroke="url(#sentinel-radar)" stroke-width="2.4" stroke-linecap="round" fill="none"><path d="M19 21 A 7 7 0 0 0 14 16" opacity="0.9"/><path d="M19 14 A 14 14 0 0 0 8 9" opacity="0.6"/><path d="M20 7 A 21 21 0 0 0 2 2" opacity="0.35"/></g><path d="M26 13 L34 40 L16 40 Z" fill="url(#sentinel-body)" fill-opacity="0.22" stroke="url(#sentinel-body)" stroke-width="2" stroke-linejoin="round"/><circle cx="25.5" cy="10" r="2.6" fill="#b6f34a"/><path d="M11 40 L38 40" stroke="url(#sentinel-body)" stroke-width="2.4" stroke-linecap="round"/><path d="M16 44 L33 44" stroke="#b6f34a" stroke-width="2" stroke-linecap="round" stroke-opacity="0.55"/></svg></div><h1>SENTINEL</h1><p>AI 安全知识情报系统</p><div class="loader"></div><p id="status">正在准备运行环境…</p><p style="font-size:12px;color:#7da690">首次启动会自动载入随包数据，请稍候</p></main></body></html>'''

def main():
    global WINDOW,UI_MODE
    if '--stop' in sys.argv:
        if not second_instance(stop=True):message('这份 Sentinel 当前未运行。')
        return
    lock=(CONTROL/'instance.lock').open('a+b');lock.seek(0)
    if lock.read(1)==b'':lock.write(b'0');lock.flush()
    lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        second_instance();return
    logfile=(CONTROL/'launcher.log').open('a',encoding='utf-8',buffering=1)
    sys.stdout=sys.stderr=logfile
    window_icon=configure_window_identity()
    def worker():
        try:startup()
        except BaseException as exc:
            traceback.print_exc();log('启动未完成：'+str(exc));message(str(exc)+'\n\n日志：'+str(CONTROL/'launcher.log'));SHUTDOWN.set()
        finally:
            try:
                if GRAPH:GRAPH.stop()
            except Exception:traceback.print_exc()
            try:
                if PG:PG.stop()
            except Exception:traceback.print_exc()
            current=read_json(STATE)
            if current.get('pid')==os.getpid():current['status']='stopped';write_json(STATE,current)
            if WINDOW:
                try:WINDOW.destroy()
                except Exception:pass
    worker_threads=[]
    def start_worker():
        if worker_threads:return
        t=threading.Thread(target=worker,name='sentinel-private-runtime',daemon=False)
        worker_threads.append(t);t.start()
    try:
        if UI_MODE=='webview':
            try:
                import webview
                WINDOW=webview.create_window('SENTINEL · AI 安全知识情报系统',html=SPLASH,width=1440,height=920,min_size=(1080,720))
                WINDOW.events.closed+=lambda:shutdown()
                webview.start(start_worker,private_mode=False,storage_path=str(CONTROL/'webview'),debug=False,icon=window_icon)
            except Exception as e:
                # Missing WebView2 does not require installation; use the system browser.
                log('内嵌窗口不可用，改用系统浏览器。');WINDOW=None;UI_MODE='browser'
                start_worker()
                for t in worker_threads:t.join()
        else:
            start_worker()
            for t in worker_threads:t.join()
    finally:
        shutdown()
        # The process lock protects the whole import/shutdown transaction.
        # Never release it while a first-run restore is still running.
        for t in worker_threads:
            while t.is_alive():t.join(timeout=1)
        lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
        except OSError:pass
        lock.close()

if __name__=='__main__':main()
