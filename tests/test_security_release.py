"""Security regressions with no network and isolated DB doubles.
SQL placeholder validation mirrors Psycopg percent-placeholder rules without importing
private Psycopg internals; deployed PostgreSQL checks remain in checking.sh.
"""
import base64
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch,MagicMock

os.environ.setdefault('BVMAC_AUTH_PEPPER','local-tests-only-not-production')
os.environ.setdefault('BVMAC_LOOKUP_PEPPER','local-tests-only-not-production')
os.environ.setdefault('BVMAC_PII_KEY',base64.urlsafe_b64encode(bytes(range(32))).decode())
os.environ.setdefault('BVMAC_COOKIE_SECURE','0')
os.environ.setdefault('BVMAC_PUBLIC_ORIGIN','https://testserver')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'/'api'))
# The build container does not need a live Psycopg install for these isolated
# unit tests. Application DB calls are patched below, so provide only the public
# symbols evaluated at import time. Production uses the real dependency from
# requirements.txt and checking.sh talks to PostgreSQL directly.
import types
if 'psycopg' not in sys.modules:
    _pg=types.ModuleType('psycopg')
    class _Connection: pass
    class _UniqueViolation(Exception): pass
    _pg.Connection=_Connection
    _pg.errors=types.SimpleNamespace(UniqueViolation=_UniqueViolation)
    _pg.connect=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('psycopg stub: DB access must be mocked'))
    sys.modules['psycopg']=_pg
from fastapi import HTTPException,Request,Response
from fastapi.testclient import TestClient
import admin_module as admin
import admin_security as sec
import auth_module as auth
import bvmac_api as api
import email_auth as mail
import launch_support as support
from common import safe_json,sha256_text,hmac_hex,AUTH_PEPPER

NOW=datetime(2026,9,20,9,tzinfo=timezone.utc)

def _split_query(query: bytes):
    """Minimal validator for Psycopg-style percent placeholders used by test doubles.

    %% is a literal percent; %s/%b/%t are bind placeholders; any other percent
    sequence is invalid. The return shape only needs len(parts)-1 == bind count.
    """
    text=query.decode('utf-8')
    count=0;i=0
    while i < len(text):
        if text[i] != '%': i+=1; continue
        if i+1 >= len(text): raise ValueError('incomplete percent placeholder')
        nxt=text[i+1]
        if nxt == '%': i+=2; continue
        if nxt in 'sbt': count+=1;i+=2;continue
        raise ValueError(f'invalid percent placeholder %{nxt}')
    return [None]*(count+1)

def req(cookie='a'*48,headers=None):
    values={'host':'testserver','origin':'https://testserver','cookie':mail.DEVICE+'='+cookie,**(headers or {})}
    return Request({'type':'http','method':'POST','path':'/','scheme':'https','server':('testserver',443),'client':('127.0.0.1',12),'headers':[(k.encode(),v.encode()) for k,v in values.items()]})

class Cursor:
    def __init__(self,one=None,rows=()):self.one=one;self.rows=rows
    def fetchone(self):return self.one
    def fetchall(self):return self.rows

class Conn:
    def __init__(self,callback):self.callback=callback;self.calls=[];self.commits=0
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def commit(self):self.commits+=1
    def execute(self,query,params=()):
        parts=_split_query(query.encode())
        count=len(parts)-1
        if count!=len(params):raise AssertionError((query,count,params))
        self.calls.append((query,params))
        return self.callback(query,params)

class Quotas(Conn):
    def __init__(self):self.data={};super().__init__(self.run)
    def run(self,q,p):
        if q.startswith('INSERT'):self.data.setdefault(p[0],(p[1],0,0,p[2]));return Cursor()
        if q.startswith('SELECT'):return Cursor(self.data[p[0]])
        if q.startswith('UPDATE'):self.data[p[-1]]=p[:-1];return Cursor()
        raise AssertionError(q)

class SecurityTests(unittest.TestCase):
    def setUp(self):support._buckets.clear()
    def test_guest_server_gate_not_just_navigation(self):
        with TestClient(api.app,base_url='https://testserver') as c:
            for url in ['/api/v3/market/companies','/api/v3/market/technical/1','/api/v3/market/trace/fact_prices/1','/api/v3/portfolio/list','/api/v3/user-tools/watchlist','/api/access']:
                self.assertEqual(c.get(url).status_code,401,url)
            self.assertEqual(c.post('/api/v3/portfolio/optimize',json={}).status_code,401)
            self.assertEqual(c.post('/api/stat/login',json={'password':'anything'}).status_code,403)
            self.assertEqual(c.get('/api/stat/me').status_code,401)
    def test_auth_requires_a_server_challenge(self):
        with TestClient(api.app,base_url='https://testserver') as c:
            for path,data in [('login',{'pseudo':'x','password':'x'}),('register',{'pseudo':'test','email':'x@example.test','password':'LongPassword12!','country':'CM'}),('forgot-password',{'pseudo':'x','email':'x@example.test'})]:
                self.assertEqual(c.post('/api/v3/auth/'+path,json=data).status_code,422)
    def test_guest_full_dataset_is_blocked_and_landing_is_reduced(self):
        payload={
            'meta':{'excel_snapshot':{'import_id':999}},
            'societes':[{'company_id':1,'ticker':'AAA','short_name':'Alpha','full_name':'Alpha SA','sector':'Secret sector'}],
            'fonds':[{'fund_id':7,'fund_name':'Fonds Test','manager':'Secret manager'}],
            'prix':[{'company_id':1,'bulletin_date_id':20260918,'close_price':12500,'variation_pct':1.25,'vol_traded':999}],
            'vl':[{'fund_id':7,'bulletin_date_id':20260918,'nav_date_id':20260917,'nav':101.4,'var_prev_pct':.4}],
            'fin':[{'secret':'financial'}]
        }
        cached=(json.dumps(payload).encode(),'"member"','2026-09-20T17:22:14+00:00',NOW)
        with patch.object(api,'_current_payload',return_value=cached),TestClient(api.app,base_url='https://testserver') as c:
            self.assertEqual(c.get('/api/public-data').status_code,401)
            response=c.get('/api/landing');self.assertEqual(response.status_code,200)
            data=response.json();self.assertEqual(data['meta']['last_session'],20260918)
            self.assertEqual(data['actions'],[{'ticker':'AAA','name':'Alpha','close_price':12500,'variation_pct':1.25}])
            self.assertEqual(data['opcvm'][0]['name'],'Fonds Test');self.assertNotIn('manager',data['opcvm'][0])
            self.assertNotIn('fin',data);self.assertNotIn('excel_snapshot',data['meta'])
    def test_audience_old_percent_crashes_driver_new_queries_bind(self):
        with self.assertRaises(Exception):_split_query(b"SELECT count(*) FILTER (WHERE path NOT LIKE '/api/%') WHERE host=%s")
        def result(q,p):
            if 'avg(request_time_ms)' in q:return Cursor((0,0,0,0))
            if 'sum(CASE WHEN' in q:return Cursor((0,0,0,0,0))
            if q.strip().startswith('SELECT count(*) FROM'):return Cursor((0,))
            return Cursor(rows=[])
        conn=Conn(result)
        with patch.object(admin,'_admin_required'),patch.object(admin,'db_connect',return_value=conn):
            for days in [0,1,30,3650]:
                response=admin.stat_dashboard(req(),days);self.assertEqual(response['overview']['page_views'],0)
        self.assertGreater(len(conn.calls),40)
    def test_quota_three_sends_then_cooldown_then_five_cycles(self):
        conn=Quotas()
        for cycle in range(5):
            start=NOW+timedelta(minutes=20*cycle)
            with patch.object(mail,'utcnow',return_value=start):
                for send in range(3):self.assertEqual(mail.reserve_quota(conn,['email:one','device:one']),start+timedelta(minutes=10))
                with self.assertRaises(HTTPException) as error:mail.reserve_quota(conn,['email:one','device:one'])
                self.assertEqual(error.exception.status_code,429)
        with patch.object(mail,'utcnow',return_value=NOW+timedelta(minutes=100)):
            with self.assertRaises(HTTPException) as error:mail.reserve_quota(conn,['email:one','device:one'])
            self.assertIn('demain',error.exception.detail)
        with patch.object(mail,'utcnow',return_value=NOW+timedelta(days=1)):
            self.assertEqual(mail.reserve_quota(conn,['email:one','device:one']),NOW+timedelta(days=1,minutes=10))
    def test_quota_email_survives_device_change_and_device_survives_email_change(self):
        for old,new in [(['email:a','device:a'],['email:a','device:b']),(['email:a','device:a'],['email:b','device:a'])]:
            conn=Quotas()
            with patch.object(mail,'utcnow',return_value=NOW):
                for i in range(3):mail.reserve_quota(conn,old)
                with self.assertRaises(HTTPException):mail.reserve_quota(conn,new)
    def test_calculation_single_use_correct_wrong_and_expired(self):
        ticket='b'*48
        for answer,expires,success in [('42',NOW+timedelta(minutes=1),True),('41',NOW+timedelta(minutes=1),False),('42',NOW,False)]:
            rows=[(sha256_text('a'*48),'login',hmac_hex(AUTH_PEPPER,ticket+'|42'),expires)]
            conn=Conn(lambda q,p:Cursor(rows.pop() if rows else None))
            data=mail.HumanIn(captcha_id=ticket,captcha_answer=answer)
            with patch.object(mail,'db_connect',return_value=conn),patch.object(mail,'utcnow',return_value=NOW):
                if success:mail.check_human(data,req(),'login')
                else:
                    with self.assertRaises(HTTPException):mail.check_human(data,req(),'login')
                with self.assertRaises(HTTPException):mail.check_human(data,req(),'login')
            self.assertEqual(conn.commits,2)
    def test_verification_wrong_code_consumes_attempt_and_never_logs_in(self):
        conn=Conn(lambda q,p:Cursor((1,sha256_text('a'*48),hmac_hex(AUTH_PEPPER,'123456'),NOW+timedelta(minutes=1),0)) if q.startswith('SELECT') else Cursor())
        with patch.object(mail,'db_connect',return_value=conn),patch.object(mail,'utcnow',return_value=NOW),patch.object(auth,'_new_session') as session:
            with self.assertRaises(HTTPException):mail.verify(mail.VerifyIn(ticket='b'*48,code='999999'),req(),Response())
            session.assert_not_called();self.assertEqual(conn.commits,1)
    def test_verification_expired_and_exhausted(self):
        for expires,attempts in [(NOW,0),(NOW+timedelta(minutes=1),5)]:
            conn=Conn(lambda q,p:Cursor((1,sha256_text('a'*48),hmac_hex(AUTH_PEPPER,'123456'),expires,attempts)))
            with patch.object(mail,'db_connect',return_value=conn),patch.object(mail,'utcnow',return_value=NOW):
                with self.assertRaises(HTTPException):mail.verify(mail.VerifyIn(ticket='b'*48,code='123456'),req(),Response())
                self.assertEqual(len(conn.calls),1)
    def test_verification_success_marks_verified_consumes_code_and_creates_session(self):
        def result(q,p):
            if 'SELECT user_id,device_hash' in q:return Cursor((1,sha256_text('a'*48),hmac_hex(AUTH_PEPPER,'123456'),NOW+timedelta(minutes=1),0))
            if 'SELECT status' in q:return Cursor(('active',))
            return Cursor()
        conn=Conn(result)
        with patch.object(mail,'db_connect',return_value=conn),patch.object(mail,'utcnow',return_value=NOW),patch.object(auth,'_new_session',return_value=('token','csrf',NOW)):
            response=Response();out=mail.verify(mail.VerifyIn(ticket='b'*48,code='123456'),req(),response)
        self.assertTrue(out['ok']);self.assertIn('httponly',response.headers['set-cookie'].lower())
        self.assertTrue(any('email_verified_at=now()' in q for q,p in conn.calls))
        self.assertTrue(any('consumed_at=now()' in q for q,p in conn.calls))
    def test_invalid_reset_link_cannot_change_password(self):
        conn=Conn(lambda q,p:Cursor(None))
        with patch.object(mail,'check_human'),patch.object(mail,'db_connect',return_value=conn),patch.object(auth,'_hash_password') as hashing:
            with self.assertRaises(HTTPException):mail.reset_password(mail.ResetIn(token='x'*48,new_password='ValidPassword12!',captcha_id='c'*48,captcha_answer='1'),req())
            hashing.assert_not_called()
    def test_reset_consumes_all_codes_and_revokes_sessions(self):
        def result(q,p):
            if q.startswith('SELECT user_id'):return Cursor((1,'ticket'))
            if q.startswith('SELECT status'):return Cursor(('active',NOW))
            return Cursor()
        conn=Conn(result)
        with patch.object(mail,'check_human'),patch.object(mail,'db_connect',return_value=conn),patch.object(auth,'_hash_password',return_value='hash'):
            self.assertTrue(mail.reset_password(mail.ResetIn(token='x'*48,new_password='ValidPassword12!',captcha_id='c'*48,captcha_answer='1'),req())['ok'])
        self.assertTrue(any('revoked_at=now()' in q for q,p in conn.calls));self.assertTrue(any('consumed_at=now()' in q for q,p in conn.calls))
    def test_admin_wrong_tab_cannot_access_or_revoke_owner(self):
        conn=Conn(lambda q,p:Cursor((NOW+timedelta(hours=1),sha256_text('owner'*16),NOW)))
        with patch.object(admin,'db_connect',return_value=conn),patch.object(admin,'utcnow',return_value=NOW):
            with self.assertRaises(HTTPException):admin._admin_required(req(headers={'cookie':admin.STAT_COOKIE+'=session','x-admin-tab':'intruder'*8}))
        self.assertEqual(len(conn.calls),1)
    def test_admin_stale_heartbeat_invalidates_session(self):
        conn=Conn(lambda q,p:Cursor((NOW+timedelta(hours=1),sha256_text('owner'*16),NOW-timedelta(seconds=91))))
        with patch.object(admin,'db_connect',return_value=conn),patch.object(admin,'utcnow',return_value=NOW):
            with self.assertRaises(HTTPException):admin._admin_required(req(headers={'cookie':admin.STAT_COOKIE+'=session','x-admin-tab':'owner'*16}))
        self.assertTrue(any(q.startswith('DELETE') for q,p in conn.calls))
    def test_email_template_escape_and_no_active_content(self):
        sec.validate_template(mail.DEFAULT_TEMPLATE)
        rendered=mail.render(mail.DEFAULT_TEMPLATE,{'pseudo':'<img src=x onerror=alert(1)>','code':'123456'})
        self.assertIn('&lt;img',rendered);self.assertNotIn('<img',rendered)
        for bad in ['<script>alert(1)</script>','<img src="https://evil.test">','<style>@import "https://evil.test";</style>','<a href="javascript:alert(1)">x</a>']:
            with self.assertRaises(HTTPException):sec.validate_template(mail.DEFAULT_TEMPLATE+bad)
    def test_gmail_smtp_mocked_no_real_email_or_secret_in_message(self):
        smtp=MagicMock();smtp.__enter__.return_value=smtp
        config={'password':'abcdefghijklmnop','sender':'sender@gmail.com','template':mail.DEFAULT_TEMPLATE}
        with patch.object(mail.smtplib,'SMTP',return_value=smtp) as smtp_factory,patch.object(mail.ssl,'create_default_context',return_value=MagicMock()),patch.object(mail,'utcnow',return_value=NOW):
            mail.deliver(config,'recipient@example.test','Test','reset','x'*43,NOW+timedelta(minutes=10),'delivery-test')
        smtp_factory.assert_called_once_with('smtp.gmail.com',587,timeout=15)
        smtp.starttls.assert_called_once();smtp.login.assert_called_once_with('sender@gmail.com','abcdefghijklmnop');smtp.send_message.assert_called_once()
        message=smtp.send_message.call_args.args[0]
        self.assertEqual(message['To'],'recipient@example.test');self.assertEqual(message['From'],'sender@gmail.com')
        html_part=message.get_payload()[0].get_payload(decode=True).decode('utf-8')
        self.assertIn('#reset=',html_part);self.assertNotIn(config['password'],html_part)
    def test_nonfinite_numbers_and_invalid_dates(self):
        self.assertIsNone(safe_json(float('nan')));self.assertIsNone(safe_json(Decimal('Infinity')))
        self.assertIsNone(api._date_id(20260231));self.assertIsNone(api._date_id(float('inf')))
        self.assertEqual(api._date_id(20260228),20260228)
    def test_limiter_boundary(self):
        self.assertTrue(support.allowed(('test','x'),2,0));self.assertTrue(support.allowed(('test','x'),2,1))
        self.assertFalse(support.allowed(('test','x'),2,59));self.assertTrue(support.allowed(('test','x'),2,60))

if __name__=='__main__':unittest.main(verbosity=2)
