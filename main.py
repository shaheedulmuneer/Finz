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
from urllib.parse import urlparse
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
            if o.hostname != host: return Response('Origin not allowed',status_code=403)
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
    return {'days':len(rows),'warnings':errors[:3]}

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
