"""FINZ AI Growth Studio: local-first, multi-workspace analytics MVP.

This version supports real CSV uploads and manually verified competitor links.
No platform APIs are silently scraped; integrations are intentionally stubs.
"""
from __future__ import annotations
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional
from urllib.parse import urlparse, quote_plus
import httpx
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent

def load_env():
    p = ROOT / '.env'
    if p.exists():
        for line in p.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())
load_env()
DB = Path(os.environ.get('DATABASE_PATH', str(ROOT / 'data' / 'finz.sqlite3')))
if not DB.is_absolute(): DB = ROOT / DB
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
if not DATABASE_URL:
    DB.parent.mkdir(parents=True, exist_ok=True)
SECURE_COOKIE = os.environ.get('COOKIE_SECURE') == '1'
app = FastAPI(title='FINZ AI Growth Studio', version='0.1.0', docs_url='/api/docs')

@contextmanager
def db():
    if DATABASE_URL:
        from pg_adapter import PostgresConnection
        c = PostgresConnection(DATABASE_URL)
    else:
        c = sqlite3.connect(DB, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
    try:
        yield c
        c.commit()
    finally:
        c.close()

def utcnow(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def local_date(): return datetime.now(ZoneInfo('Asia/Dubai')).date()
def today(): return local_date().isoformat()
def hash_pw(pw: str, salt: Optional[str]=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', pw.encode(), bytes.fromhex(salt), 250000).hex()
    return salt + '$' + digest

def valid_pw(pw, stored):
    try:
        s, d = stored.split('$')
        return hmac.compare_digest(hash_pw(pw,s).split('$')[1], d)
    except Exception: return False

def init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY, name TEXT NOT NULL, industry TEXT NOT NULL DEFAULT 'Hospitality', location TEXT DEFAULT 'Dubai, UAE', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memberships(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, role TEXT NOT NULL DEFAULT 'owner', PRIMARY KEY(user_id,workspace_id));
        CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, day TEXT NOT NULL, platform TEXT NOT NULL, post_title TEXT NOT NULL DEFAULT '', views INTEGER DEFAULT 0, reach INTEGER DEFAULT 0, likes INTEGER DEFAULT 0, comments INTEGER DEFAULT 0, shares INTEGER DEFAULT 0, saves INTEGER DEFAULT 0, profile_visits INTEGER DEFAULT 0, inquiries INTEGER DEFAULT 0, bookings INTEGER DEFAULT 0, revenue REAL DEFAULT 0, source TEXT DEFAULT 'CSV', created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_metrics ON metrics(workspace_id,day);
        CREATE TABLE IF NOT EXISTS audience(id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, day TEXT NOT NULL, platform TEXT NOT NULL, dimension TEXT NOT NULL, segment TEXT NOT NULL, count INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_audience ON audience(workspace_id,dimension);
        CREATE TABLE IF NOT EXISTS competitors(id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, name TEXT NOT NULL, handle TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reels(id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, competitor_id INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE, url TEXT NOT NULL, posted_at TEXT, content_type TEXT NOT NULL DEFAULT 'Other', notes TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL, UNIQUE(workspace_id,url));
        CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, day TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(workspace_id,day));
        ''')
        if os.environ.get('DEMO_MODE') == '1':
            user = c.execute('SELECT id FROM users WHERE email=?', ('demo@finz.local',)).fetchone()
            if not user:
                c.execute('INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)',('demo@finz.local',hash_pw('Demo123!'),utcnow()))
                uid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
                c.execute('INSERT INTO workspaces(name,industry,location,created_at) VALUES(?,?,?,?)',('FINZ Demo Brand','Board-game café','Dubai, UAE',utcnow()))
                wid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
                c.execute('INSERT INTO memberships VALUES(?,?,?)',(uid,wid,'owner'))
                for name,handle in [('The Six','thesixcafe'),('Hive',''),('DICE',''),('Kefi',''),('TILE','')]:
                    c.execute('INSERT INTO competitors(workspace_id,name,handle,created_at) VALUES(?,?,?,?)',(wid,name,handle,utcnow()))
                # Demo metrics are artificial and labeled as such in the UI.
                for i in range(28):
                    day=(local_date()-timedelta(days=i)).isoformat()
                    v=1100 + (i*317 % 2600)
                    c.execute('''INSERT INTO metrics(workspace_id,day,platform,post_title,views,reach,likes,comments,shares,saves,profile_visits,inquiries,bookings,revenue,source,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (wid,day,'Instagram',f'DEMO Reel {i+1}',v,int(v*.76),int(v*.045),int(v*.004),int(v*.008),int(v*.012),int(v*.027),int(v*.003),int(v*.001),int(v*.001)*180,'DEMO',utcnow()))
                for dimension, items in [('age',[('18–24',340),('25–34',480),('35–44',230),('45+',95)]),('city',[('Dubai',780),('Sharjah',170),('Abu Dhabi',90),('Other',105)]),('gender',[('Women',510),('Men',615),('Other / unspecified',20)])]:
                    for seg, cnt in items:
                        c.execute('INSERT INTO audience(workspace_id,day,platform,dimension,segment,count) VALUES(?,?,?,?,?,?)',(wid,today(),'Instagram',dimension,seg,cnt))
init_db()

class Credentials(BaseModel):
    email: str
    password: str
class NewWorkspace(BaseModel):
    name: str = Field(min_length=2,max_length=100)
    industry: str = Field(default='Hospitality',max_length=80)
    location: str = Field(default='Dubai, UAE',max_length=100)
class CompetitorIn(BaseModel):
    name: str = Field(min_length=2,max_length=100)
    handle: str = Field(default='',max_length=100)
class ReelIn(BaseModel):
    competitor_id: int
    url: str
    posted_at: Optional[str] = None
    content_type: str = Field(default='Other',max_length=40)
    notes: str = Field(default='',max_length=2000)

def auth(request:Request):
    authorization = request.headers.get('authorization', '')
    token = authorization[7:].strip() if authorization.lower().startswith('bearer ') else request.cookies.get('finz_session')
    if not token: raise HTTPException(401,'Sign in required')
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with db() as c:
        r=c.execute('''SELECT u.id,u.email FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?''',(token_hash,utcnow())).fetchone()
        if not r: raise HTTPException(401,'Session expired')
        return dict(r)

def allow_workspace(wid:int,user:dict):
    with db() as c:
        row=c.execute('SELECT w.*,m.role FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE w.id=? AND m.user_id=?',(wid,user['id'])).fetchone()
        if not row: raise HTTPException(404,'Workspace not found')
        return dict(row)

@app.middleware('http')
async def origin_protection(request:Request,call_next):
    # Defense in depth for cookie-auth write endpoints (localhost cross-origin posts).
    if request.method in ('POST','DELETE','PUT','PATCH'):
        origin=request.headers.get('origin')
        if origin:
            from urllib.parse import urlparse
            o=urlparse(origin)
            host=request.headers.get('host','').split(':')[0]
            # Apple posts its sign-in result from appleid.apple.com; that callback is protected by its one-time state instead.
            if o.hostname != host and not (request.url.path.startswith('/api/auth/') and request.url.path.endswith('/callback')): return Response('Origin not allowed',status_code=403)
    return await call_next(request)

@app.post('/api/auth/register')
def register(creds:Credentials,response:Response):
    email=creds.email.strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email): raise HTTPException(400,'Valid email required')
    if len(creds.password)<10: raise HTTPException(400,'Password must have at least 10 characters')
    with db() as c:
        if c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone(): raise HTTPException(409,'Email already registered')
        c.execute('INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)',(email,hash_pw(creds.password),utcnow()))
        uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    sign_session(uid,response)
    return {'email':email}

def sign_session(uid,response):
    token=secrets.token_urlsafe(36)
    expires=(datetime.now(timezone.utc)+timedelta(days=7)).isoformat(timespec='seconds')
    with db() as c:
        c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),uid,expires))
    response.set_cookie('finz_session',token,httponly=True,secure=SECURE_COOKIE,samesite='lax',max_age=7*86400,path='/')
    return token

@app.post('/api/auth/login')
def login(creds:Credentials,response:Response):
    with db() as c:
        row=c.execute('SELECT * FROM users WHERE email=?',(creds.email.strip().lower(),)).fetchone()
    if not row or not valid_pw(creds.password,row['password_hash']): raise HTTPException(401,'Incorrect email or password')
    sign_session(row['id'],response)
    return {'email':row['email']}

# Dedicated mobile sessions. The bearer token is returned only to the
# authenticated client and must be kept in the OS secure credential store.
@app.post('/api/mobile/auth/login')
def mobile_login(creds:Credentials,response:Response):
    with db() as c:
        row=c.execute('SELECT * FROM users WHERE email=?',(creds.email.strip().lower(),)).fetchone()
    if not row or not valid_pw(creds.password,row['password_hash']):
        raise HTTPException(401,'Incorrect email or password')
    token=sign_session(row['id'],response)
    return {'email':row['email'],'token':token,'expires_in':7*86400}

@app.post('/api/mobile/auth/register')
def mobile_register(creds:Credentials,response:Response):
    email=creds.email.strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email):
        raise HTTPException(400,'Valid email required')
    if len(creds.password)<10:
        raise HTTPException(400,'Password must have at least 10 characters')
    with db() as c:
        if c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone():
            raise HTTPException(409,'Email already registered')
        c.execute('INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)',
                  (email,hash_pw(creds.password),utcnow()))
        uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    token=sign_session(uid,response)
    return {'email':email,'token':token,'expires_in':7*86400}

@app.post('/api/mobile/auth/logout')
def mobile_logout(request:Request):
    authorization=request.headers.get('authorization','')
    if authorization.lower().startswith('bearer '):
        token=authorization[7:].strip()
        with db() as c:
            c.execute('DELETE FROM sessions WHERE token_hash=?',
                      (hashlib.sha256(token.encode()).hexdigest(),))
    return {'ok':True}

@app.post('/api/auth/logout')
def logout(response:Response,request:Request):
    token=request.cookies.get('finz_session')
    if token:
        with db() as c: c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie('finz_session',path='/')
    return {'ok':True}

# ===== Social sign-in: Google, Apple, Facebook =====
# Env: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET | FACEBOOK_APP_ID, FACEBOOK_APP_SECRET |
#      APPLE_CLIENT_ID (Services ID), APPLE_TEAM_ID, APPLE_KEY_ID, APPLE_PRIVATE_KEY (.p8 contents). Optional: PUBLIC_BASE_URL.
SOCIAL={
 'google':{'env':('GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET'),'auth':'https://accounts.google.com/o/oauth2/v2/auth','scope':'openid email profile'},
 'facebook':{'env':('FACEBOOK_APP_ID','FACEBOOK_APP_SECRET'),'auth':'https://www.facebook.com/v25.0/dialog/oauth','scope':'email,public_profile'},
 'apple':{'env':('APPLE_CLIENT_ID','APPLE_TEAM_ID','APPLE_KEY_ID','APPLE_PRIVATE_KEY'),'auth':'https://appleid.apple.com/auth/authorize','scope':'name email'},
}
SOCIAL_NAMES={'google':'Google','facebook':'Facebook','apple':'Apple'}
def social_on(p): return p in SOCIAL and all(os.environ.get(k,'').strip() for k in SOCIAL[p]['env'])
def public_base(request:Request):
    env=os.environ.get('PUBLIC_BASE_URL','').strip().rstrip('/')
    if env: return env
    host=request.headers.get('x-forwarded-host') or request.headers.get('host','')
    proto='http' if host.startswith(('localhost','127.0.0.1')) else 'https'
    return f'{proto}://{host}'
def social_redirect_uri(request,p): return public_base(request)+f'/api/auth/{p}/callback'
def init_social_tables():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS user_identities(provider TEXT NOT NULL, subject TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, email TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, PRIMARY KEY(provider,subject));
        CREATE TABLE IF NOT EXISTS login_states(state_hash TEXT PRIMARY KEY, provider TEXT NOT NULL, expires_at TEXT NOT NULL);
        ''')
init_social_tables()
def _b64url(b:bytes)->str:
    import base64
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()
def _jwt_payload(token:str)->dict:
    import base64
    part=token.split('.')[1]; part+='='*(-len(part)%4)
    return json.loads(base64.urlsafe_b64decode(part))
def apple_client_secret():
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    pem=os.environ['APPLE_PRIVATE_KEY'].replace('\\n','\n').strip().encode()
    key=serialization.load_pem_private_key(pem,password=None)
    now=int(datetime.now(timezone.utc).timestamp())
    head=_b64url(json.dumps({'alg':'ES256','kid':os.environ['APPLE_KEY_ID']}).encode())
    body=_b64url(json.dumps({'iss':os.environ['APPLE_TEAM_ID'],'iat':now,'exp':now+3000,'aud':'https://appleid.apple.com','sub':os.environ['APPLE_CLIENT_ID']}).encode())
    r,s=decode_dss_signature(key.sign(f'{head}.{body}'.encode(),ec.ECDSA(hashes.SHA256())))
    return f'{head}.{body}.'+_b64url(r.to_bytes(32,'big')+s.to_bytes(32,'big'))

@app.get('/api/auth/providers')
def auth_providers(): return {p:social_on(p) for p in SOCIAL}

@app.get('/api/auth/{provider}/start',include_in_schema=False)
def social_start(provider:str,request:Request):
    from fastapi.responses import RedirectResponse
    from urllib.parse import urlencode
    if not social_on(provider): return RedirectResponse('/?login_error='+quote_plus(f'{SOCIAL_NAMES.get(provider,provider)} sign-in is not set up yet'),status_code=303)
    state=secrets.token_urlsafe(32)
    with db() as c:
        c.execute('DELETE FROM login_states WHERE expires_at<?',(utcnow(),))
        c.execute('INSERT INTO login_states(state_hash,provider,expires_at) VALUES(?,?,?)',(hashlib.sha256(state.encode()).hexdigest(),provider,(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat(timespec='seconds')))
    cfg=SOCIAL[provider];cid=os.environ[cfg['env'][0]]
    q={'client_id':cid,'redirect_uri':social_redirect_uri(request,provider),'response_type':'code','scope':cfg['scope'],'state':state}
    if provider=='google': q['prompt']='select_account'
    if provider=='apple': q['response_mode']='form_post'
    return RedirectResponse(cfg['auth']+'?'+urlencode(q),status_code=303)

def social_identity(provider,code,request):
    """Exchange the code with the provider and return (subject, email, email_verified)."""
    redirect=social_redirect_uri(request,provider)
    if provider=='google':
        r=httpx.post('https://oauth2.googleapis.com/token',data={'code':code,'client_id':os.environ['GOOGLE_CLIENT_ID'],'client_secret':os.environ['GOOGLE_CLIENT_SECRET'],'redirect_uri':redirect,'grant_type':'authorization_code'},timeout=20)
        if r.status_code!=200: raise ValueError('Google did not accept the sign-in')
        r=httpx.get('https://openidconnect.googleapis.com/v1/userinfo',headers={'Authorization':'Bearer '+r.json()['access_token']},timeout=20)
        if r.status_code!=200: raise ValueError('Could not read your Google account')
        u=r.json(); return str(u['sub']),(u.get('email') or '').lower(),bool(u.get('email_verified'))
    if provider=='facebook':
        r=httpx.get('https://graph.facebook.com/v25.0/oauth/access_token',params={'client_id':os.environ['FACEBOOK_APP_ID'],'client_secret':os.environ['FACEBOOK_APP_SECRET'],'redirect_uri':redirect,'code':code},timeout=20)
        if r.status_code!=200: raise ValueError('Facebook did not accept the sign-in')
        r=httpx.get('https://graph.facebook.com/v25.0/me',params={'fields':'id,email','access_token':r.json()['access_token']},timeout=20)
        if r.status_code!=200: raise ValueError('Could not read your Facebook account')
        u=r.json(); return str(u['id']),(u.get('email') or '').lower(),bool(u.get('email'))
    if provider=='apple':
        r=httpx.post('https://appleid.apple.com/auth/token',data={'client_id':os.environ['APPLE_CLIENT_ID'],'client_secret':apple_client_secret(),'code':code,'grant_type':'authorization_code','redirect_uri':redirect},timeout=20)
        if r.status_code!=200: raise ValueError('Apple did not accept the sign-in')
        u=_jwt_payload(r.json()['id_token'])
        if u.get('iss')!='https://appleid.apple.com' or u.get('aud')!=os.environ['APPLE_CLIENT_ID']: raise ValueError('Apple sign-in could not be verified')
        return str(u['sub']),(u.get('email') or '').lower(),str(u.get('email_verified')).lower()=='true'
    raise ValueError('Unknown sign-in provider')

@app.api_route('/api/auth/{provider}/callback',methods=['GET','POST'],include_in_schema=False)
async def social_callback(provider:str,request:Request):
    from fastapi.responses import RedirectResponse
    params=dict(request.query_params)
    if request.method=='POST': params.update({k:str(v) for k,v in (await request.form()).items()})
    name=SOCIAL_NAMES.get(provider,provider)
    def fail(msg): return RedirectResponse('/?login_error='+quote_plus(msg[:200]),status_code=303)
    if params.get('error'): return fail(f'{name} sign-in was cancelled')
    code,state=params.get('code',''),params.get('state','')
    if not social_on(provider) or not code or not state: return fail(f'{name} sign-in did not finish. Please try again.')
    sh=hashlib.sha256(state.encode()).hexdigest()
    with db() as c:
        st=c.execute('SELECT provider FROM login_states WHERE state_hash=? AND expires_at>?',(sh,utcnow())).fetchone()
        c.execute('DELETE FROM login_states WHERE state_hash=?',(sh,))
    if not st or st['provider']!=provider: return fail('This sign-in link expired. Please try again.')
    try: subject,email,verified=social_identity(provider,code,request)
    except (httpx.HTTPError,ValueError,KeyError) as e: return fail(str(e) if isinstance(e,ValueError) else f'Could not reach {name}. Please try again.')
    with db() as c:
        row=c.execute('SELECT user_id FROM user_identities WHERE provider=? AND subject=?',(provider,subject)).fetchone()
        if row: uid=row['user_id']
        else:
            if not email: return fail(f'Your {name} account did not share an email address. Please sign in with email instead.')
            if not verified: return fail(f'Please verify your email with {name} first, or sign in with email.')
            u=c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
            if u: uid=u['id']
            else:
                c.execute('INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)',(email,'social$'+secrets.token_hex(32),utcnow()))
                uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO user_identities(provider,subject,user_id,email,created_at) VALUES(?,?,?,?,?)',(provider,subject,uid,email,utcnow()))
    resp=RedirectResponse('/',status_code=303)
    sign_session(uid,resp)
    return resp

@app.get('/api/me')
def me(user=Depends(auth)): return user

@app.get('/api/workspaces')
def workspaces(user=Depends(auth)):
    with db() as c:
        return [dict(x) for x in c.execute('SELECT w.*,m.role FROM workspaces w JOIN memberships m ON w.id=m.workspace_id WHERE m.user_id=? ORDER BY w.id',(user['id'],)).fetchall()]

@app.post('/api/workspaces')
def new_workspace(x:NewWorkspace,user=Depends(auth)):
    with db() as c:
        c.execute('INSERT INTO workspaces(name,industry,location,created_at) VALUES(?,?,?,?)',(x.name,x.industry,x.location,utcnow()))
        wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute('INSERT INTO memberships VALUES(?,?,?)',(user['id'],wid,'owner'))
    return {'id':wid,'name':x.name}

@app.get('/api/workspaces/{wid}/metrics')
def metrics(wid:int,days:int=30,user=Depends(auth)):
    allow_workspace(wid,user)
    days=min(max(days,7),365)
    start=(local_date()-timedelta(days=days-1)).isoformat()
    with db() as c:
        rows=[dict(x) for x in c.execute('SELECT * FROM metrics WHERE workspace_id=? AND day>=? ORDER BY day DESC,id DESC',(wid,start))]
    totals={k:sum(float(r[k]) for r in rows) for k in ('views','reach','likes','comments','shares','saves','profile_visits','inquiries','bookings','revenue')}
    grouped=defaultdict(lambda:{'views':0,'reach':0,'inquiries':0,'bookings':0,'revenue':0})
    for r in rows:
        for k in grouped[r['day']]:grouped[r['day']][k]+=r[k]
    return {'totals':totals,'daily':[{'day':k,**v} for k,v in sorted(grouped.items())],'posts':rows[:100], 'has_demo':any(r['source']=='DEMO' for r in rows),'count':len(rows)}

MAX_UPLOAD=2_000_000
METRIC_FIELDS={'views','reach','likes','comments','shares','saves','profile_visits','inquiries','bookings','revenue'}
def parse_csv(data:bytes):
    if len(data)>MAX_UPLOAD: raise HTTPException(413,'CSV must be under 2MB')
    try: decoded=data.decode('utf-8-sig')
    except UnicodeDecodeError: raise HTTPException(400,'Please upload UTF-8 CSV')
    reader=csv.DictReader(io.StringIO(decoded))
    if not reader.fieldnames:raise HTTPException(400,'CSV header missing')
    return [{str(k).strip().lower(): (v or '').strip() for k,v in row.items() if k is not None} for row in reader]

def parse_date(s):
    try:return date.fromisoformat(s).isoformat()
    except Exception:raise ValueError('Use YYYY-MM-DD dates')

def num(s,floating=False):
    try:
        v=float(s or '0')
        if not (0<=v<=1e12):raise ValueError()
        return v if floating else int(v)
    except Exception:raise ValueError('Numbers must be nonnegative')

@app.post('/api/workspaces/{wid}/import/metrics')
async def import_metrics(wid:int,file:UploadFile=File(...),user=Depends(auth)):
    allow_workspace(wid,user)
    rows=parse_csv(await file.read())
    if not rows:raise HTTPException(400,'CSV has no rows')
    staged=[]
    for i,r in enumerate(rows,2):
        try:
            if not r.get('date') or not r.get('platform'): raise ValueError('date and platform are required')
            day=parse_date(r['date'])
            platform=r['platform'][:50]
            title=r.get('post_title','')[:150]
            vals=[num(r.get(k,''),k=='revenue') for k in METRIC_ORDER]
            staged.append((wid,day,platform,title,*vals,'CSV',utcnow()))
        except ValueError as e:raise HTTPException(400,f'Row {i}: {e}')
    with db() as c:
        c.executemany('''INSERT INTO metrics(workspace_id,day,platform,post_title,views,reach,likes,comments,shares,saves,profile_visits,inquiries,bookings,revenue,source,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',staged)
    return {'imported':len(staged)}
METRIC_ORDER=['views','reach','likes','comments','shares','saves','profile_visits','inquiries','bookings','revenue']

@app.post('/api/workspaces/{wid}/import/audience')
async def import_audience(wid:int,file:UploadFile=File(...),user=Depends(auth)):
    allow_workspace(wid,user)
    rows=parse_csv(await file.read())
    staged=[]
    for i,r in enumerate(rows,2):
        try:
            if not all(r.get(k) for k in ('date','platform','dimension','segment','count')):raise ValueError('date, platform, dimension, segment, count required')
            if r['dimension'].lower() not in ('age','city','gender','interest','country'):raise ValueError('dimension must be age, city, gender, interest, or country')
            staged.append((wid,parse_date(r['date']),r['platform'][:50],r['dimension'].lower(),r['segment'][:100],num(r['count'])))
        except ValueError as e:raise HTTPException(400,f'Row {i}: {e}')
    with db() as c:
        c.executemany('INSERT INTO audience(workspace_id,day,platform,dimension,segment,count) VALUES(?,?,?,?,?,?)',staged)
    return {'imported':len(staged)}

@app.get('/api/workspaces/{wid}/audience')
def audience(wid:int,user=Depends(auth)):
    allow_workspace(wid,user)
    with db() as c:
        rows=c.execute('''SELECT a.dimension,a.segment,SUM(a.count) total FROM audience a JOIN (SELECT platform,dimension,MAX(day) as day FROM audience WHERE workspace_id=? GROUP BY platform,dimension) latest ON latest.platform=a.platform AND latest.dimension=a.dimension AND latest.day=a.day WHERE a.workspace_id=? GROUP BY a.dimension,a.segment ORDER BY a.dimension,total DESC''',(wid,wid)).fetchall()
    grouped=defaultdict(list)
    for r in rows:grouped[r['dimension']].append({'segment':r['segment'],'count':r['total']})
    return dict(grouped)

@app.get('/api/workspaces/{wid}/competitors')
def competitors(wid:int,user=Depends(auth)):
    allow_workspace(wid,user)
    with db() as c:
        return [dict(x) for x in c.execute('SELECT * FROM competitors WHERE workspace_id=? ORDER BY name',(wid,))]

@app.post('/api/workspaces/{wid}/competitors')
def add_competitor(wid:int,x:CompetitorIn,user=Depends(auth)):
    allow_workspace(wid,user)
    with db() as c:
        c.execute('INSERT INTO competitors(workspace_id,name,handle,created_at) VALUES(?,?,?,?)',(wid,x.name.strip(),x.handle.lstrip('@').strip(),utcnow()))
        return {'id':c.execute('SELECT last_insert_rowid()').fetchone()[0]}

@app.get('/api/workspaces/{wid}/reels')
def reels(wid:int,user=Depends(auth)):
    allow_workspace(wid,user)
    with db() as c:
        return [dict(x) for x in c.execute('''SELECT r.*,co.name competitor_name FROM reels r JOIN competitors co ON co.id=r.competitor_id WHERE r.workspace_id=? ORDER BY COALESCE(r.posted_at,r.added_at) DESC,r.id DESC LIMIT 250''',(wid,))]

@app.post('/api/workspaces/{wid}/reels')
def add_reel(wid:int,x:ReelIn,user=Depends(auth)):
    allow_workspace(wid,user)
    url=x.url.strip()
    p=urlparse(url)
    if p.scheme!='https' or p.hostname not in ('www.instagram.com','instagram.com') or not re.fullmatch(r'/reel/[A-Za-z0-9_-]+/?',p.path):
        raise HTTPException(400,'Use a direct https://www.instagram.com/reel/... link')
    if x.posted_at:
        try:date.fromisoformat(x.posted_at)
        except ValueError:raise HTTPException(400,'Post date must be YYYY-MM-DD')
    with db() as c:
        if not c.execute('SELECT id FROM competitors WHERE id=? AND workspace_id=?',(x.competitor_id,wid)).fetchone():raise HTTPException(404,'Competitor not found')
        try:
            c.execute('INSERT INTO reels(workspace_id,competitor_id,url,posted_at,content_type,notes,added_at) VALUES(?,?,?,?,?,?,?)',(wid,x.competitor_id,f'https://www.instagram.com{p.path}',x.posted_at,x.content_type,x.notes,utcnow()))
        except (sqlite3.IntegrityError, __import__('psycopg').errors.UniqueViolation):raise HTTPException(409,'That Reel is already in this workspace')
    return {'ok':True}

@app.delete('/api/workspaces/{wid}/reels/{rid}')
def delete_reel(wid:int,rid:int,user=Depends(auth)):
    allow_workspace(wid,user)
    with db() as c:
        cur=c.execute('DELETE FROM reels WHERE workspace_id=? AND id=?',(wid,rid))
        if cur.rowcount==0:raise HTTPException(404,'Reel not found')
    return {'ok':True}

@app.get('/api/workspaces/{wid}/integrations')
def integrations(wid:int,user=Depends(auth)):
    allow_workspace(wid,user)
    ig=get_connection(wid,'instagram')
    if ig: igcard={'key':'instagram','name':'Instagram','status':'Connected','method':'@'+ig['account_name'] if ig['account_name'] else 'Connected account','notes':('Last synced '+ig['last_sync_at'][:16].replace('T',' ')+' UTC' if ig.get('last_sync_at') else 'Not synced yet')+(' · '+ig['last_sync_status'] if ig.get('last_sync_status') and ig['last_sync_status']!='OK' else ''),'action':'sync'}
    elif ig_configured(): igcard={'key':'instagram','name':'Instagram','status':'Ready to connect','method':'Instagram Business Login','notes':'Connect your Instagram professional account to sync daily views, reach, likes, comments, shares and saves.','action':'connect'}
    else: igcard={'key':'instagram','name':'Instagram','status':'Setup needed','method':'Instagram Business Login','notes':'Add INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET to the server settings to turn this on.','action':None}
    return [igcard,
            {'name':'Facebook Page','status':'Coming soon','method':'Facebook Login for Business','notes':'Page insights need a separate Facebook Login connection.'},
            {'name':'Google Business Profile','status':'Coming soon','method':'Business Profile Performance API','notes':'Requires authorized business profile.'},
            {'name':'TikTok','status':'Coming soon','method':'TikTok APIs / business authorization','notes':'Access and allowed analytics depend on the product and permissions.'},
            {'name':'WhatsApp Business','status':'Coming soon','method':'WhatsApp Cloud API webhooks','notes':'Requires approved Meta business assets.'},
            {'name':'CSV import','status':'Ready','method':'Local file upload','notes':'Works now without external credentials.'}]

# ===== Instagram connection (Business Login for Instagram) =====
# Needs env vars: INSTAGRAM_APP_ID, INSTAGRAM_APP_SECRET. Optional: INSTAGRAM_REDIRECT_URI, TOKEN_ENCRYPTION_KEY.
IG_GRAPH='https://graph.instagram.com/v25.0'
IG_SCOPES='instagram_business_basic,instagram_business_manage_insights'
def ig_configured(): return bool(os.environ.get('INSTAGRAM_APP_ID') and os.environ.get('INSTAGRAM_APP_SECRET'))
def ig_redirect_uri(request:Request):
    env=os.environ.get('INSTAGRAM_REDIRECT_URI','').strip()
    if env: return env
    host=request.headers.get('x-forwarded-host') or request.headers.get('host','')
    return f'https://{host}/api/oauth/instagram/callback'
def _fernet():
    import base64
    from cryptography.fernet import Fernet
    raw=os.environ.get('TOKEN_ENCRYPTION_KEY') or ('finz-token-key:'+os.environ.get('INSTAGRAM_APP_SECRET',''))
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))
def seal(token:str)->str: return _fernet().encrypt(token.encode()).decode()
def unseal(blob:str)->str: return _fernet().decrypt(blob.encode()).decode()
def init_connection_tables():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS oauth_connections(id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, provider TEXT NOT NULL, external_account_id TEXT NOT NULL, account_name TEXT NOT NULL DEFAULT '', token_enc TEXT NOT NULL, expires_at TEXT, last_sync_at TEXT, last_sync_status TEXT DEFAULT '', created_at TEXT NOT NULL, UNIQUE(workspace_id,provider));
        CREATE TABLE IF NOT EXISTS oauth_states(state_hash TEXT PRIMARY KEY, workspace_id INTEGER NOT NULL, user_id INTEGER NOT NULL, provider TEXT NOT NULL, expires_at TEXT NOT NULL);
        ''')
init_connection_tables()
def get_connection(wid:int,provider:str):
    with db() as c:
        r=c.execute('SELECT * FROM oauth_connections WHERE workspace_id=? AND provider=?',(wid,provider)).fetchone()
        return dict(r) if r else None
def ig_error(r):
    try: msg=r.json().get('error',{}); msg=msg.get('message') if isinstance(msg,dict) else (r.json().get('error_message') or str(msg))
    except Exception: msg=r.text[:200]
    return f'Instagram said: {msg}'

@app.get('/api/workspaces/{wid}/connect/instagram')
def connect_instagram(wid:int,request:Request,user=Depends(auth)):
    allow_workspace(wid,user)
    if not ig_configured(): raise HTTPException(400,'Instagram is not set up yet. Add INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET to the server settings.')
    state=secrets.token_urlsafe(32)
    with db() as c:
        c.execute('DELETE FROM oauth_states WHERE expires_at<?',(utcnow(),))
        c.execute('INSERT INTO oauth_states(state_hash,workspace_id,user_id,provider,expires_at) VALUES(?,?,?,?,?)',(hashlib.sha256(state.encode()).hexdigest(),wid,user['id'],'instagram',(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat(timespec='seconds')))
    from urllib.parse import urlencode
    q=urlencode({'client_id':os.environ['INSTAGRAM_APP_ID'],'redirect_uri':ig_redirect_uri(request),'response_type':'code','scope':IG_SCOPES,'state':state})
    return {'url':'https://www.instagram.com/oauth/authorize?'+q}

@app.get('/api/oauth/instagram/callback',include_in_schema=False)
def instagram_callback(request:Request,code:str='',state:str='',error:str='',error_description:str=''):
    from fastapi.responses import RedirectResponse
    from urllib.parse import quote
    def back(msg=None): return RedirectResponse('/?connected=instagram' if not msg else '/?ig_error='+quote(msg[:200]),status_code=303)
    if error: return back(error_description or 'Instagram connection was cancelled')
    if not code or not state or not ig_configured(): return back('Missing code from Instagram')
    with db() as c:
        st=c.execute('SELECT * FROM oauth_states WHERE state_hash=? AND expires_at>?',(hashlib.sha256(state.encode()).hexdigest(),utcnow())).fetchone()
        c.execute('DELETE FROM oauth_states WHERE state_hash=?',(hashlib.sha256(state.encode()).hexdigest(),))
    if not st: return back('This connect link expired. Please try again.')
    st=dict(st)
    try:
        r=httpx.post('https://api.instagram.com/oauth/access_token',data={'client_id':os.environ['INSTAGRAM_APP_ID'],'client_secret':os.environ['INSTAGRAM_APP_SECRET'],'grant_type':'authorization_code','redirect_uri':ig_redirect_uri(request),'code':code.split('#')[0]},timeout=20)
        if r.status_code!=200: return back(ig_error(r))
        short=r.json(); short=short['data'][0] if 'data' in short else short
        r=httpx.get('https://graph.instagram.com/access_token',params={'grant_type':'ig_exchange_token','client_secret':os.environ['INSTAGRAM_APP_SECRET'],'access_token':short['access_token']},timeout=20)
        if r.status_code!=200: return back(ig_error(r))
        long=r.json(); token=long['access_token']; expires=(datetime.now(timezone.utc)+timedelta(seconds=int(long.get('expires_in',5184000)))).isoformat(timespec='seconds')
        r=httpx.get(f'{IG_GRAPH}/me',params={'fields':'user_id,username','access_token':token},timeout=20)
        if r.status_code!=200: return back(ig_error(r))
        me=r.json()
    except httpx.HTTPError: return back('Could not reach Instagram. Please try again.')
    with db() as c:
        c.execute('DELETE FROM oauth_connections WHERE workspace_id=? AND provider=?',(st['workspace_id'],'instagram'))
        c.execute('INSERT INTO oauth_connections(workspace_id,provider,external_account_id,account_name,token_enc,expires_at,created_at) VALUES(?,?,?,?,?,?,?)',(st['workspace_id'],'instagram',str(me.get('user_id') or me.get('id')),me.get('username',''),seal(token),expires,utcnow()))
    try: sync_instagram(st['workspace_id'])
    except Exception: pass
    return back()

IG_DAY_METRICS=['reach','views','likes','comments','shares','saves','profile_links_taps']
def sync_instagram(wid:int,days:int=30):
    conn=get_connection(wid,'instagram')
    if not conn: raise HTTPException(400,'Instagram is not connected')
    token=unseal(conn['token_enc'])
    # refresh the 60-day token when it has less than 10 days left
    try:
        if conn.get('expires_at') and datetime.fromisoformat(conn['expires_at'])-datetime.now(timezone.utc)<timedelta(days=10):
            r=httpx.get('https://graph.instagram.com/refresh_access_token',params={'grant_type':'ig_refresh_token','access_token':token},timeout=20)
            if r.status_code==200:
                j=r.json(); token=j['access_token']
                with db() as c: c.execute('UPDATE oauth_connections SET token_enc=?,expires_at=? WHERE id=?',(seal(token),(datetime.now(timezone.utc)+timedelta(seconds=int(j.get('expires_in',5184000)))).isoformat(timespec='seconds'),conn['id']))
    except Exception: pass
    tz=ZoneInfo('Asia/Dubai'); rows=[]; errors=[]
    def one(params):
        try:
            r=httpx.get(f"{IG_GRAPH}/{conn['external_account_id']}/insights",params={**params,'period':'day','metric_type':'total_value','access_token':token},timeout=20)
            if r.status_code!=200: return None,ig_error(r)
            return {x.get('name'):int((x.get('total_value') or {}).get('value',0)) for x in r.json().get('data',[])},None
        except httpx.HTTPError as e: return None,str(e)
    def fetch_day(i):
        d=local_date()-timedelta(days=i)
        since=int(datetime(d.year,d.month,d.day,tzinfo=tz).timestamp()); win={'since':since,'until':since+86400}
        vals,err=one({**win,'metric':','.join(IG_DAY_METRICS)})
        errs=[]
        if vals is None:  # one metric was refused: fall back to asking one by one
            vals={}
            for m in IG_DAY_METRICS:
                v,e=one({**win,'metric':m})
                if v: vals.update(v)
                elif e: errs.append(e)
        return d,vals,errs
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        for d,vals,errs in ex.map(fetch_day,range(1,days+1)):
            errors+=errs
            if vals: rows.append((wid,d.isoformat(),'Instagram','Instagram daily total',vals.get('views',0),vals.get('reach',0),vals.get('likes',0),vals.get('comments',0),vals.get('shares',0),vals.get('saves',0),vals.get('profile_links_taps',0),0,0,0,'INSTAGRAM_API',utcnow()))
    if not rows:
        msg=errors[0] if errors else 'No data returned'
        with db() as c: c.execute('UPDATE oauth_connections SET last_sync_at=?,last_sync_status=? WHERE id=?',(utcnow(),'Error: '+msg[:180],conn['id']))
        raise HTTPException(502,msg)
    with db() as c:
        c.execute('DELETE FROM metrics WHERE workspace_id=? AND source=? AND day>=?',(wid,'INSTAGRAM_API',(local_date()-timedelta(days=days)).isoformat()))
        c.executemany('''INSERT INTO metrics(workspace_id,day,platform,post_title,views,reach,likes,comments,shares,saves,profile_visits,inquiries,bookings,revenue,source,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',rows)
        c.execute('UPDATE oauth_connections SET last_sync_at=?,last_sync_status=? WHERE id=?',(utcnow(),'OK' if not errors else 'Partly synced',conn['id']))
    posts=0
    try: posts=sync_instagram_posts(wid,conn,token)
    except Exception as e: errors.append('Posts: '+str(e)[:150])
    return {'days':len(rows),'posts':posts,'warnings':errors[:3]}

@app.post('/api/workspaces/{wid}/sync/instagram')
def sync_instagram_now(wid:int,user=Depends(auth)):
    allow_workspace(wid,user)
    return sync_instagram(wid)

@app.delete('/api/workspaces/{wid}/connections/instagram')
def disconnect_instagram(wid:int,user=Depends(auth)):
    allow_workspace(wid,user)
    with db() as c: c.execute('DELETE FROM oauth_connections WHERE workspace_id=? AND provider=?',(wid,'instagram'))
    return {'ok':True}

def create_report(wid:int):
    with db() as c:
        rows=[dict(r) for r in c.execute('SELECT * FROM metrics WHERE workspace_id=? AND day>=?', (wid,(local_date()-timedelta(days=13)).isoformat()))]
        new_reels=[dict(r) for r in c.execute('SELECT r.*,co.name name FROM reels r JOIN competitors co ON co.id=r.competitor_id WHERE r.workspace_id=? AND r.posted_at>=?',(wid,(local_date()-timedelta(days=1)).isoformat()))]
    recent=[r for r in rows if r['day']>=(local_date()-timedelta(days=6)).isoformat()]
    prior=[r for r in rows if r['day']<(local_date()-timedelta(days=6)).isoformat()]
    def sumk(xs,k):return round(sum(r[k] for r in xs),2)
    recent_views=sumk(recent,'views');prior_views=sumk(prior,'views')
    recent_inq=sumk(recent,'inquiries');prior_inq=sumk(prior,'inquiries')
    recs=[]
    if not rows:
        recs.append({'title':'Upload your numbers first','detail':'Go to Upload Data and add your Instagram results. Then FINZ can give you ideas based on your real numbers.','priority':'High'})
    else:
        if prior_views>0 and recent_views<prior_views*.9:
            recs.append({'title':'Your views went down','detail':f'You got {round((1-recent_views/prior_views)*100)}% fewer views than last week. Make the first 3 seconds of your next Reel more exciting so people keep watching.','priority':'High'})
        if sumk(recent,'inquiries')<max(3,sumk(recent,'profile_visits')*.03):
            recs.append({'title':'Turn visitors into customers','detail':'People visit your profile but few ask about you. Add a clear booking link, pin a Reel that shows your prices, and add a "Book now" button to your Stories.','priority':'High'})
        if prior_inq and recent_inq>prior_inq*1.1:
            recs.append({'title':'More people are asking about you','detail':'You got more enquiries than last week. Find the posts that started those chats and make more posts like them.','priority':'Medium'})
        if sumk(recent,'bookings')==0:
            recs.append({'title':'Add your bookings','detail':'Your upload has no bookings yet. Add the bookings column so you can see which posts bring real customers.','priority':'High'})
    if len(new_reels):
        recs.append({'title':'Check new competitor Reels','detail':f'You saved {len(new_reels)} competitor Reels from the last 2 days. Look at what made them work and try the same idea in your own style. Don\'t copy their video.','priority':'Medium'})
    recs.extend([{'title':'Film a real customer moment','detail':'Make a short Reel (10–18 seconds) of customers having fun. Start with the funniest moment and end with "Book your table" or a similar message.','priority':'Medium'},
                 {'title':'Post one special offer','detail':'Share an offer for birthdays, date nights or team outings, with a booking link so you can see how many people book from it.','priority':'Medium'}])
    report={'day':today(),'recs':recs[:5],'snapshot':{'views_7d':recent_views,'views_previous_7d':prior_views,'inquiries_7d':recent_inq,'bookings_7d':sumk(recent,'bookings'),'revenue_7d':sumk(recent,'revenue'),'competitor_reels_recent':len(new_reels),'distinct_competitors_recent':len({r['competitor_id'] for r in new_reels})},'method':'Rules-based analysis of imported data and manually entered, date-verified competitor Reel links. No automatic scraping.','demo':any(r['source']=='DEMO' for r in rows)}
    with db() as c:
        c.execute('INSERT INTO reports(workspace_id,day,data,created_at) VALUES(?,?,?,?) ON CONFLICT(workspace_id,day) DO UPDATE SET data=excluded.data, created_at=excluded.created_at',(wid,today(),json.dumps(report),utcnow()))
    return report

@app.post('/api/workspaces/{wid}/reports/ai')
def enhance_with_ai(wid:int,user=Depends(auth)):
    allow_workspace(wid,user)
    key=os.environ.get('OPENAI_API_KEY','').strip()
    if not key:raise HTTPException(400,'Set OPENAI_API_KEY in .env to enable optional AI recommendations')
    report=create_report(wid)
    # Send ONLY summary statistics and generalized recommendations, no raw personal data.
    payload={'snapshot':report['snapshot'],'rules_recommendations':report['recs'],'industry':'Social media marketing'}
    try:
        with httpx.Client(timeout=35) as client:
            r=client.post('https://api.openai.com/v1/responses',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},json={
                'model':os.environ.get('OPENAI_MODEL','gpt-4.1-mini'),
                'instructions':'You are a careful social media analytics assistant. Based solely on the provided aggregate data, return 3 concise actionable marketing recommendations. Do not invent trends, customers, competitor posts or performance. Output plain text as 3 numbered recommendations, each one or two sentences. Any absence of data must be noted.',
                'input':json.dumps(payload),'max_output_tokens':400})
            r.raise_for_status()
            data=r.json()
        message='\n'.join(item.get('text','') for out in data.get('output',[]) for item in out.get('content',[]) if item.get('type')=='output_text').strip()
        if not message:raise ValueError('API did not return text')
    except (httpx.HTTPError,ValueError) as ex:
        raise HTTPException(502,'AI request failed; rules-based report remains available') from ex
    report['ai_analysis']=message[:4000]
    report['ai_model']=os.environ.get('OPENAI_MODEL','gpt-4.1-mini')
    with db() as c:
        c.execute('UPDATE reports SET data=?,created_at=? WHERE workspace_id=? AND day=?',(json.dumps(report),utcnow(),wid,today()))
    return report

# ===== Instagram per-post results =====
def init_post_tables():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS ig_posts(workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, media_id TEXT NOT NULL, posted_at TEXT NOT NULL, media_type TEXT NOT NULL DEFAULT '', product_type TEXT NOT NULL DEFAULT '', caption TEXT NOT NULL DEFAULT '', permalink TEXT NOT NULL DEFAULT '', thumbnail TEXT NOT NULL DEFAULT '', views INTEGER DEFAULT 0, reach INTEGER DEFAULT 0, likes INTEGER DEFAULT 0, comments INTEGER DEFAULT 0, shares INTEGER DEFAULT 0, saves INTEGER DEFAULT 0, interactions INTEGER DEFAULT 0, synced_at TEXT NOT NULL, PRIMARY KEY(workspace_id,media_id));
        CREATE INDEX IF NOT EXISTS idx_ig_posts ON ig_posts(workspace_id,posted_at);
        ''')
init_post_tables()
IG_POST_METRICS=['views','reach','likes','comments','shares','saved','total_interactions']
def sync_instagram_posts(wid:int,conn:dict,token:str,days:int=90,max_posts:int=100):
    cutoff=datetime.now(timezone.utc)-timedelta(days=days)
    media=[];url=f"{IG_GRAPH}/{conn['external_account_id']}/media"
    params={'fields':'id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count,thumbnail_url,media_url','limit':50,'access_token':token}
    while url and len(media)<max_posts:
        r=httpx.get(url,params=params,timeout=25)
        if r.status_code!=200: raise ValueError(ig_error(r))
        j=r.json();page=j.get('data',[]);stop=False
        for m in page:
            try: ts=datetime.strptime(m['timestamp'],'%Y-%m-%dT%H:%M:%S%z')
            except Exception: continue
            if ts<cutoff: stop=True;break
            media.append((m,ts))
        url=None if stop else (j.get('paging') or {}).get('next');params=None
    def insights(item):
        m,ts=item
        vals={'likes':m.get('like_count') or 0,'comments':m.get('comments_count') or 0}
        if m.get('media_product_type')=='STORY': return m,ts,vals
        for metrics in (IG_POST_METRICS,['reach','likes','comments','shares','saved']):
            try:
                r=httpx.get(f"{IG_GRAPH}/{m['id']}/insights",params={'metric':','.join(metrics),'access_token':token},timeout=20)
                if r.status_code==200:
                    for x in r.json().get('data',[]):
                        v=(x.get('values') or [{}])[0].get('value',(x.get('total_value') or {}).get('value',0))
                        vals[x.get('name')]=int(v or 0)
                    break
            except httpx.HTTPError: pass
        return m,ts,vals
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex: done=list(ex.map(insights,media))
    rows=[]
    for m,ts,v in done:
        thumb=m.get('thumbnail_url') or (m.get('media_url') if m.get('media_type')!='VIDEO' else '') or ''
        rows.append((wid,m['id'],ts.astimezone(ZoneInfo('Asia/Dubai')).isoformat(timespec='minutes'),m.get('media_type',''),m.get('media_product_type',''),(m.get('caption') or '')[:2200],m.get('permalink',''),thumb,v.get('views',0),v.get('reach',0),v.get('likes',0),v.get('comments',0),v.get('shares',0),v.get('saved',0),v.get('total_interactions',0),utcnow()))
    with db() as c:
        for row in rows:
            c.execute('DELETE FROM ig_posts WHERE workspace_id=? AND media_id=?',(row[0],row[1]))
        c.executemany('INSERT INTO ig_posts(workspace_id,media_id,posted_at,media_type,product_type,caption,permalink,thumbnail,views,reach,likes,comments,shares,saves,interactions,synced_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
    return len(rows)

def post_kind(p):
    if p['product_type']=='REELS': return 'Reel'
    if p['media_type']=='CAROUSEL_ALBUM': return 'Carousel'
    if p['media_type']=='VIDEO': return 'Video'
    return 'Photo'

@app.get('/api/workspaces/{wid}/posts')
def instagram_posts(wid:int,days:int=90,user=Depends(auth)):
    allow_workspace(wid,user)
    days=min(max(days,7),365)
    start=(local_date()-timedelta(days=days-1)).isoformat()
    with db() as c:
        rows=[dict(r) for r in c.execute('SELECT * FROM ig_posts WHERE workspace_id=? AND posted_at>=? ORDER BY posted_at DESC',(wid,start))]
    for r in rows:
        r['kind']=post_kind(r)
        r['score']=r['views'] or r['reach'] or (r['likes']+r['comments'])
    by_kind={}
    for r in rows: by_kind.setdefault(r['kind'],[]).append(r['score'])
    kinds=sorted([{'kind':k,'posts':len(v),'avg_views':round(sum(v)/len(v))} for k,v in by_kind.items()],key=lambda x:-x['avg_views'])
    names=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];by_day={}
    for r in rows:
        try: by_day.setdefault(datetime.fromisoformat(r['posted_at']).weekday(),[]).append(r['score'])
        except ValueError: pass
    days_rank=sorted([{'day':names[d],'posts':len(v),'avg_views':round(sum(v)/len(v))} for d,v in by_day.items() if len(v)>=2],key=lambda x:-x['avg_views'])
    top=sorted(rows,key=lambda r:-r['score'])
    return {'count':len(rows),'top':top[:12],'recent':rows[:12],'by_kind':kinds,'best_days':days_rank[:3]}

# ===== AI Studio: content creation =====
AI_KINDS={
 'reel':'Instagram Reel script: a scroll-stopping hook for the first 3 seconds, then numbered shots (what to film, on-screen text), ideal length, and the caption',
 'caption':'Instagram feed post caption',
 'carousel':'Instagram carousel: slide-by-slide text (Slide 1 is the hook, last slide is the call to action)',
 'story':'Instagram Story sequence: frame-by-frame ideas using polls, questions or countdown stickers',
 'calendar':'7-day content plan: one post idea per day (Day 1 to Day 7), mixing Reels, carousels and Stories',
 'facebook':'Facebook Page post',
 'tiktok':'TikTok video script: hook, numbered shots, trending-style format suggestion and caption',
 'whatsapp':'WhatsApp broadcast message to existing customers who opted in: short, friendly, one clear call to action, no spammy wording',
}
AI_SCHEMA={'type':'object','additionalProperties':False,'required':['items'],'properties':{'items':{'type':'array','items':{'type':'object','additionalProperties':False,
  'required':['title','hook','body','caption','hashtags','call_to_action','why'],
  'properties':{'title':{'type':'string'},'hook':{'type':'string'},'body':{'type':'string'},'caption':{'type':'string'},'hashtags':{'type':'array','items':{'type':'string'}},'call_to_action':{'type':'string'},'why':{'type':'string'}}}}}}
class ContentIn(BaseModel):
    kind: str = Field(max_length=20)
    topic: str = Field(default='',max_length=600)
    tone: str = Field(default='Friendly',max_length=40)
    language: str = Field(default='English',max_length=40)
    count: int = Field(default=3,ge=1,le=5)
def init_ai_tables():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS ai_contents(id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, user_id INTEGER NOT NULL, kind TEXT NOT NULL, params TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_ai_contents ON ai_contents(workspace_id,created_at);
        ''')
init_ai_tables()
def ai_context(wid:int,ws:dict):
    start=(local_date()-timedelta(days=29)).isoformat()
    with db() as c:
        rows=[dict(r) for r in c.execute('SELECT * FROM metrics WHERE workspace_id=? AND day>=?',(wid,start))]
        comps=[r['name'] for r in c.execute('SELECT name FROM competitors WHERE workspace_id=?',(wid,))]
    tot={k:int(sum(float(r[k] or 0) for r in rows)) for k in ('views','reach','likes','comments','shares','saves','inquiries','bookings')}
    posts=sorted([r for r in rows if r['post_title'] and 'daily total' not in r['post_title'].lower()],key=lambda r:r['views'] or 0,reverse=True)
    top=[{'title':r['post_title'][:120],'platform':r['platform'],'views':r['views'],'likes':r['likes'],'saves':r['saves'],'shares':r['shares']} for r in posts[:5]]
    with db() as c:
        igp=[dict(r) for r in c.execute('SELECT * FROM ig_posts WHERE workspace_id=? AND posted_at>=? ORDER BY views DESC, reach DESC LIMIT 8',(wid,(local_date()-timedelta(days=90)).isoformat()))]
    if igp: top=[{'caption':p['caption'][:160],'type':post_kind(p),'posted':p['posted_at'][:10],'views':p['views'],'reach':p['reach'],'likes':p['likes'],'saves':p['saves'],'shares':p['shares'],'comments':p['comments']} for p in igp]
    return {'business':{'name':ws['name'],'industry':ws.get('industry',''),'location':ws.get('location','')},'last_30_days_totals':tot if rows else 'No data uploaded yet','best_posts':top,'competitors':comps[:10],'uses_demo_data':any(r['source']=='DEMO' for r in rows)}

@app.post('/api/workspaces/{wid}/ai/content')
def ai_content(wid:int,x:ContentIn,user=Depends(auth)):
    ws=allow_workspace(wid,user)
    if x.kind not in AI_KINDS: raise HTTPException(400,'Unknown content type')
    key=os.environ.get('OPENAI_API_KEY','').strip()
    if not key: raise HTTPException(400,'AI is not set up yet. Add OPENAI_API_KEY to the server settings.')
    limit=int(os.environ.get('AI_DAILY_LIMIT','40'))
    with db() as c:
        used=c.execute('SELECT COUNT(*) n FROM ai_contents WHERE workspace_id=? AND created_at>=?',(wid,(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(timespec='seconds'))).fetchone()['n']
    if used>=limit: raise HTTPException(429,f'Daily AI limit reached ({limit} per client per day). Try again tomorrow.')
    count=7 if x.kind=='calendar' else x.count
    instructions=('You are an expert social media strategist creating ready-to-post content for a business. '
        f'Write in {x.language}. Tone: {x.tone}. Create exactly {count} different item(s) of this type: {AI_KINDS[x.kind]}. '
        'Use the business details and performance data provided: lean into what their best posts have in common. '
        'Never invent statistics, prices, offers, awards or customer quotes that were not given; use clear placeholders like [price] or [date] instead. '
        'Do not copy competitors. Keep it suitable for the platform and its rules. '
        'Field guide: title = short name for the idea; hook = the first line or first 3 seconds; body = the full script, slides, frames or message (use line breaks and numbering); '
        'caption = the ready-to-paste caption (empty string for WhatsApp); hashtags = 5 to 12 relevant hashtags without spaces (empty list for WhatsApp); '
        'call_to_action = one clear action; why = one sentence on why this should work for this business.')
    payload={'request':{'content_type':x.kind,'topic_or_goal':x.topic or 'Choose the best topic for this business right now'},'context':ai_context(wid,ws)}
    model=os.environ.get('OPENAI_MODEL','gpt-4.1-mini')
    try:
        with httpx.Client(timeout=60) as client:
            r=client.post('https://api.openai.com/v1/responses',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},json={
                'model':model,'instructions':instructions,'input':json.dumps(payload,ensure_ascii=False),'max_output_tokens':3500,
                'text':{'format':{'type':'json_schema','name':'content_items','schema':AI_SCHEMA,'strict':True}}})
            r.raise_for_status()
            data=r.json()
        text=''.join(item.get('text','') for out in data.get('output',[]) for item in out.get('content',[]) if item.get('type')=='output_text').strip()
        items=json.loads(text)['items']
        if not items: raise ValueError('empty')
    except (httpx.HTTPError,ValueError,KeyError,TypeError) as ex:
        raise HTTPException(502,'The AI could not create content right now. Please try again.') from ex
    params={'kind':x.kind,'topic':x.topic,'tone':x.tone,'language':x.language,'count':count,'model':model}
    with db() as c:
        c.execute('INSERT INTO ai_contents(workspace_id,user_id,kind,params,data,created_at) VALUES(?,?,?,?,?,?)',(wid,user['id'],x.kind,json.dumps(params),json.dumps(items,ensure_ascii=False),utcnow()))
        cid=c.execute('SELECT MAX(id) FROM ai_contents WHERE workspace_id=?',(wid,)).fetchone()[0]
    return {'id':cid,'params':params,'items':items,'created_at':utcnow(),'used_today':used+1,'daily_limit':limit}

@app.get('/api/workspaces/{wid}/ai/content')
def ai_content_history(wid:int,user=Depends(auth)):
    allow_workspace(wid,user)
    with db() as c:
        rows=[dict(r) for r in c.execute('SELECT * FROM ai_contents WHERE workspace_id=? ORDER BY id DESC LIMIT 20',(wid,))]
    return {'enabled':bool(os.environ.get('OPENAI_API_KEY','').strip()),'history':[{'id':r['id'],'params':json.loads(r['params']),'items':json.loads(r['data']),'created_at':r['created_at']} for r in rows]}

@app.delete('/api/workspaces/{wid}/ai/content/{cid}')
def ai_content_delete(wid:int,cid:int,user=Depends(auth)):
    allow_workspace(wid,user)
    with db() as c: c.execute('DELETE FROM ai_contents WHERE workspace_id=? AND id=?',(wid,cid))
    return {'ok':True}

@app.get('/api/workspaces/{wid}/reports')
def reports(wid:int,user=Depends(auth)):
    allow_workspace(wid,user)
    with db() as c:
        r=c.execute('SELECT data FROM reports WHERE workspace_id=? ORDER BY day DESC LIMIT 1',(wid,)).fetchone()
    return json.loads(r['data']) if r else create_report(wid)

@app.post('/api/workspaces/{wid}/reports/generate')
def generate(wid:int,user=Depends(auth)):
    allow_workspace(wid,user)
    return create_report(wid)

@app.get('/api/health')
def health():
    with db() as c:
        c.execute('SELECT 1').fetchone()
    return {'ok':True,'date':today(),'database':'postgresql' if DATABASE_URL else 'sqlite'}

app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
@app.get('/')
def homepage():return FileResponse(ROOT/'static'/'index.html',headers={'Cache-Control':'no-cache, no-store, must-revalidate'})

@app.get('/download',include_in_schema=False)
def downloads():
    return FileResponse(ROOT/'static'/'download.html')

@app.get('/manifest.webmanifest',include_in_schema=False)
def manifest():
    return FileResponse(ROOT/'static'/'manifest.webmanifest',media_type='application/manifest+json')
