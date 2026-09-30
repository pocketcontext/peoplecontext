#!/usr/bin/env python3
"""Production reader browser tests against isolated synthetic records."""
import argparse, contextlib, json, os, pathlib, shutil, subprocess, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PASSWORD='SyntheticUserPassword123!'
ROOT=pathlib.Path(__file__).resolve().parents[1]

@contextlib.contextmanager
def control_server(action):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            action(self.path);self.send_response(200);self.end_headers();self.wfile.write(b'{}')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield 'http://127.0.0.1:'+str(server.server_port)
    finally:server.shutdown();server.server_close();thread.join()

def browser(base, fixture, action):
    with tempfile.TemporaryDirectory(prefix='reader-browser-') as temp, control_server(action) as controller:
        path=pathlib.Path(temp)/'fixture.json';path.write_text(json.dumps(fixture));path.chmod(0o600)
        env=dict(os.environ,READER_SCREENSHOT_DIR=os.environ.get('READER_SCREENSHOT_DIR',temp),READER_TEST_URL=base,READER_TEST_FIXTURE=str(path),READER_TEST_CONTROL=controller,READER_TEST_OUTPUT=str(pathlib.Path(temp)/'results'))
        result=subprocess.run(['pnpm','e2e'],cwd=ROOT/'ui',env=env)
        if result.returncode:raise SystemExit(result.returncode)

def run(binary):
    from integration import Server
    with tempfile.TemporaryDirectory(prefix='people-reader-') as temp:
        s=Server(binary,temp)
        shutil.copytree(ROOT/'ui/dist',pathlib.Path(temp)/'ui/dist')
        try:
            user=s.create('agents',dict(email='reader@example.com',name='Reader',password=PASSWORD,passwordConfirm=PASSWORD))
            other=s.create('agents',dict(email='other@example.com',name='Other',password=PASSWORD,passwordConfirm=PASSWORD))
            role=s.create('hr_members',{'account':user['id']})
            token=s.login('agents','reader@example.com',PASSWORD)
            create=lambda table,body:s.create(table,body,token)
            records=[create('employees',{'name':f'Fixture person {i:02}','job_title':'Researcher','department':'Synthetic'}) for i in range(35)]
            first=records[0]
            note=create('hr_notes',{'employee':first['id'],'body':'Private personnel evidence'})
            personal=create('personal_details',{'employee':first['id'],'home_address':'Restricted address'})
            fixture=dict(authCollection='agents',otherEmail='other@example.com',privateTable='hr_notes',privateId=note['id'],privateText=note['body'],email='reader@example.com',password=PASSWORD,table='employees',label='People',id=first['id'],title=first['name'],needle=records[-1]['name'],forbiddenTable='hr_notes',forbiddenId='missing00000001',revokeId=note['id'],forbiddenText='Private personnel evidence',relationTitle='Private personnel evidence',relationTable='hr_notes',relationId=note['id'])
            def action(path):
                if path=='/revoke':s.request('DELETE','/api/collections/hr_members/records/'+role['id'],token=s.admin,expected=204)
            browser(s.base,fixture,action)
        finally:s.stop();s.log.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--binary',required=True);args=parser.parse_args()
    run(args.binary)
