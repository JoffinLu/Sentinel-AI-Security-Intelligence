"""Portable, loopback-only Neo4j 5 runtime. No service/registry/PowerShell changes.

The caller owns this object for the entire process lifetime. Runtime configuration
is generated under the supplied run directory and never changes the source app.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from collections import Counter, defaultdict


def _hidden_kwargs(console=False):
    if os.name != 'nt':
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return {'startupinfo': si, 'creationflags': subprocess.CREATE_NEW_CONSOLE if console else subprocess.CREATE_NO_WINDOW}


def _quoted(name):
    return '`' + name.replace('`', '``') + '`'


def _owned_process_handles(root_pid):
    """Pin handles of this launcher's descendants before sending CTRL_C.

    Neo4jBoot can exit while its server JVM is still flushing database stores.
    Process handles avoid both that race and unsafe reuse of a numeric PID.
    """
    if os.name != 'nt':
        return []
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),
                    ('th32ProcessID',wintypes.DWORD),('th32DefaultHeapID',ctypes.c_size_t),
                    ('th32ModuleID',wintypes.DWORD),('cntThreads',wintypes.DWORD),
                    ('th32ParentProcessID',wintypes.DWORD),('pcPriClassBase',wintypes.LONG),
                    ('dwFlags',wintypes.DWORD),('szExeFile',wintypes.WCHAR*260)]
    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(PROCESSENTRY32)]
    kernel.Process32NextW.argtypes=[wintypes.HANDLE,ctypes.POINTER(PROCESSENTRY32)]
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    snapshot=kernel.CreateToolhelp32Snapshot(2,0)
    if snapshot==wintypes.HANDLE(-1).value:
        return []
    entry=PROCESSENTRY32();entry.dwSize=ctypes.sizeof(entry)
    parent_map={}
    try:
        ok=kernel.Process32FirstW(snapshot,ctypes.byref(entry))
        while ok:
            parent_map[int(entry.th32ProcessID)]=int(entry.th32ParentProcessID)
            ok=kernel.Process32NextW(snapshot,ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    owned={root_pid}
    while True:
        new={pid for pid,parent in parent_map.items() if parent in owned}
        if new<=owned:break
        owned.update(new)
    handles=[]
    for pid in owned:
        handle=kernel.OpenProcess(0x00100000|0x1000|0x0001,False,pid)
        if handle:handles.append((pid,handle))
    return handles


class Neo4jRuntime:
    def __init__(self, home, java_home, data_dir, run_dir, logs_dir,
                 http_port=17499, bolt_port=17699, username='neo4j', password=None):
        self.home = Path(home).resolve()
        self.java_home = Path(java_home).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.run_dir = Path(run_dir).resolve()
        self.logs_dir = Path(logs_dir).resolve()
        self.conf_dir = self.run_dir / 'conf'
        self.http_port, self.bolt_port = int(http_port), int(bolt_port)
        self.username, self.password = username, password
        if username != 'neo4j':
            raise ValueError('Initial portable database identity must be neo4j')
        if not password or len(password) < 8:
            raise ValueError('An explicit password of at least 8 characters is required')
        self.process = None
        self._log = None
        self.stop_method = None

    def _env(self):
        env = dict(os.environ)
        env['JAVA_HOME'] = str(self.java_home)
        env['NEO4J_HOME'] = str(self.home)
        env['NEO4J_CONF'] = str(self.conf_dir)
        env.pop('JAVA_OPTS', None)
        env.pop('JAVA_TOOL_OPTIONS', None)
        env.pop('_JAVA_OPTIONS', None)
        return env

    def _java_command(self, main, *args):
        return [str(self.java_home / 'bin/java.exe'), '-cp', str(self.home / 'lib/*'),
                '-Dbasedir=' + str(self.home), main, *args]

    def configure(self):
        for p in (self.data_dir, self.run_dir, self.logs_dir, self.conf_dir):
            p.mkdir(parents=True, exist_ok=True)
        lines = [
            'server.default_listen_address=127.0.0.1',
            'server.default_advertised_address=127.0.0.1',
            'server.bolt.enabled=true',
            f'server.bolt.listen_address=127.0.0.1:{self.bolt_port}',
            f'server.bolt.advertised_address=127.0.0.1:{self.bolt_port}',
            'server.http.enabled=true',
            f'server.http.listen_address=127.0.0.1:{self.http_port}',
            f'server.http.advertised_address=127.0.0.1:{self.http_port}',
            'server.https.enabled=false',
            'dbms.security.auth_enabled=true',
            'dbms.usage_report.enabled=false',
            'server.memory.heap.initial_size=128m',
            'server.memory.heap.max_size=512m',
            'server.memory.pagecache.size=128m',
            'server.directories.data=' + self.data_dir.as_posix(),
            'server.directories.logs=' + self.logs_dir.as_posix(),
            'server.directories.run=' + self.run_dir.as_posix(),
            'db.tx_log.rotation.retention_policy=64M size',
            'db.logs.query.enabled=OFF',
        ]
        (self.conf_dir / 'neo4j.conf').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        for name in ('server-logs.xml', 'user-logs.xml'):
            source = self.home / 'conf' / name
            if source.exists() and not (self.conf_dir / name).exists():
                (self.conf_dir / name).write_bytes(source.read_bytes())

    def initialize_auth(self):
        if (self.data_dir / 'dbms/auth.ini').exists() or (self.data_dir / 'databases/system').exists():
            return
        command = self._java_command('org.neo4j.server.startup.Neo4jAdminBoot',
                                     'dbms', 'set-initial-password', self.password)
        result = subprocess.run(command, cwd=self.home, env=self._env(),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=90, **_hidden_kwargs())
        if result.returncode:
            # Never include the command/password in errors or audit logs.
            raise RuntimeError('Neo4j initial authentication setup failed: ' + result.stdout.decode('utf-8', errors='replace')[-1500:])

    def start(self, timeout=180):
        if self.process is not None and self.process.poll() is None:
            return self
        for port in (self.http_port, self.bolt_port):
            with socket.socket() as check:
                if check.connect_ex(('127.0.0.1', port)) == 0:
                    raise RuntimeError(f'Portable Neo4j port {port} is already in use; refusing to reuse another instance')
        self.configure()
        self.initialize_auth()
        self._log = (self.logs_dir / 'portable-console.log').open('ab', buffering=0)
        self.process = subprocess.Popen(
            self._java_command('org.neo4j.server.startup.Neo4jBoot', 'console'),
            cwd=self.home, env=self._env(), stdin=subprocess.DEVNULL,
            stdout=self._log, stderr=subprocess.STDOUT, **_hidden_kwargs(console=True))
        try:
            self.wait_ready(timeout)
        except Exception:
            self.stop()
            raise
        return self

    def driver(self):
        from neo4j import GraphDatabase
        return GraphDatabase.driver(f'bolt://127.0.0.1:{self.bolt_port}',
                                   auth=(self.username, self.password),
                                   connection_timeout=3, connection_acquisition_timeout=4)

    def wait_ready(self, timeout=180):
        deadline = time.monotonic() + timeout
        last_error = ''
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Portable Neo4j exited before becoming ready; see portable-console.log')
            try:
                with self.driver() as driver:
                    driver.verify_connectivity()
                    with driver.session() as session:
                        if session.run('RETURN 1 AS ready').single()['ready'] == 1:
                            return True
            except Exception as exc:
                last_error = type(exc).__name__
            time.sleep(1)
        raise TimeoutError('Portable Neo4j readiness timeout (' + last_error + '); see portable-console.log')

    def graph_counts(self):
        with self.driver() as driver, driver.session(default_access_mode='READ') as s:
            return {'nodes': s.run('MATCH (n) RETURN count(n) AS c').single()['c'],
                    'relationships': s.run('MATCH ()-[r]->() RETURN count(r) AS c').single()['c'],
                    'labels': {r['label']:r['count'] for r in s.run('MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS count ORDER BY label')},
                    'relation_types': {r['type']:r['count'] for r in s.run('MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count ORDER BY type')}}

    def restore_json(self, snapshot):
        """Restore only to an empty owned database; business IDs are kept exactly.

        Original element IDs are a mapping aid only. Neo4j generates new internal
        IDs, so these must never be treated as durable application identifiers.
        """
        graph = json.loads(Path(snapshot).read_text(encoding='utf-8'))
        with self.driver() as driver, driver.session() as s:
            counts = s.run('MATCH (n) RETURN count(n) AS c').single()['c']
            if counts:
                raise RuntimeError('Target graph is not empty; refusing to overwrite existing graph')
            mapping = {}
            grouped = defaultdict(list)
            for node in graph['nodes']:
                grouped[tuple(node['labels'])].append(node)
            for labels, nodes in grouped.items():
                labelstr = ''.join(':' + _quoted(v) for v in labels)
                for start in range(0, len(nodes), 500):
                    result = s.run('UNWIND $rows AS row CREATE (n' + labelstr + ') SET n = row.properties RETURN row.element_id AS old_id, elementId(n) AS new_id', rows=nodes[start:start+500])
                    mapping.update({r['old_id']: r['new_id'] for r in result})
            relgroups = defaultdict(list)
            for rel in graph['relationships']:
                relgroups[rel['type']].append({'start_id':mapping[rel['start_id']], 'end_id':mapping[rel['end_id']], 'properties':rel['properties']})
            for kind, rels in relgroups.items():
                for start in range(0, len(rels), 500):
                    s.run('UNWIND $rows AS row MATCH (a) WHERE elementId(a)=row.start_id MATCH (b) WHERE elementId(b)=row.end_id CREATE (a)-[r:' + _quoted(kind) + ']->(b) SET r = row.properties', rows=rels[start:start+500]).consume()
            for con in graph.get('constraints', []):
                labels = con.get('labelsOrTypes', [])
                properties = con.get('properties', [])
                if con.get('type') == 'UNIQUENESS' and len(labels) == len(properties) == 1:
                    s.run('CREATE CONSTRAINT '+_quoted(con['name'])+' IF NOT EXISTS FOR (n:'+_quoted(labels[0])+') REQUIRE n.'+_quoted(properties[0])+' IS UNIQUE').consume()
            for idx in graph.get('indexes', []):
                labels, properties = idx.get('labelsOrTypes', []), idx.get('properties', [])
                if idx.get('type') == 'RANGE' and not idx.get('owningConstraint') and len(labels)==1 and properties:
                    props = ','.join('n.'+_quoted(p) for p in properties)
                    s.run('CREATE INDEX '+_quoted(idx['name'])+' IF NOT EXISTS FOR (n:'+_quoted(labels[0])+') ON ('+props+')').consume()
            s.run('CALL db.awaitIndexes(120)').consume()
        actual = self.graph_counts()
        if actual['nodes'] != len(graph['nodes']) or actual['relationships'] != len(graph['relationships']):
            raise RuntimeError('Restored graph counts differ from snapshot')
        return actual

    def stop(self, timeout=45):
        if self.process is None:
            return
        if self.process.poll() is not None and getattr(self,'children_fully_stopped',False):
            return
        owned_handles=_owned_process_handles(self.process.pid)
        self.stopped_process_count=len(owned_handles)
        stopped_deadline=time.monotonic()+timeout
        if self.process.poll() is None and os.name == 'nt':
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            # This is a private hidden console created solely for this process.
            # CTRL_C invokes Java shutdown hooks and closes database stores cleanly.
            parent_console = bool(kernel.GetConsoleWindow())
            kernel.FreeConsole()
            attached = bool(kernel.AttachConsole(self.process.pid))
            if attached:
                kernel.SetConsoleCtrlHandler(None, True)
                kernel.GenerateConsoleCtrlEvent(0, 0)
                try:
                    self.process.wait(timeout=timeout)
                    self.stop_method = 'private-console-ctrl-c-graceful'
                except subprocess.TimeoutExpired:
                    self.stop_method = 'private-console-timeout'
                finally:
                    kernel.FreeConsole()
                    if parent_console:
                        kernel.AttachConsole(-1)
                    kernel.SetConsoleCtrlHandler(None, False)
            if self.process.poll() is None:
                subprocess.run(['taskkill.exe','/PID',str(self.process.pid),'/T','/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               **_hidden_kwargs())
                self.process.wait(timeout=15)
                self.stop_method = 'owned-process-tree-force-fallback'
        elif self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=timeout)
            self.stop_method = 'terminate'
        if os.name == 'nt':
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
            kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            all_stopped=bool(owned_handles)
            try:
                for pid,handle in owned_handles:
                    remaining=max(0,int((stopped_deadline-time.monotonic())*1000))
                    status=kernel.WaitForSingleObject(handle,remaining)
                    if status==258:
                        kernel.TerminateProcess(handle,1)
                        status=kernel.WaitForSingleObject(handle,15000)
                        self.stop_method='owned-process-handle-force-fallback'
                    if status!=0:all_stopped=False
                self.children_fully_stopped=all_stopped
            finally:
                for pid,handle in owned_handles:kernel.CloseHandle(handle)
        if self._log:
            self._log.close()
            self._log = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.stop()
