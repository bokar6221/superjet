# -*- coding: utf-8 -*-
from __future__ import annotations
import json, os, secrets
from pathlib import Path
from flask import Blueprint, jsonify, request, render_template, session, make_response, abort, send_file
from werkzeug.utils import secure_filename
from PIL import Image

from integrated_operations import *
# Explicitly import underscore-prefixed helper: wildcard imports intentionally omit it.
from integrated_operations import _verify_password, search_operations, list_shifts, add_shift, update_shift, delete_shift

BASE_DIR=Path(__file__).resolve().parent
TICKET_DIR=BASE_DIR/'data'/'staff_tickets'; TICKET_DIR.mkdir(parents=True,exist_ok=True)
PUBLIC_ORIGIN=os.getenv('SUPERJET_WEB_PUBLIC_ORIGIN','https://superjet.tail0f920c.ts.net:8443').strip().rstrip('/')

bp=Blueprint('staff_portal',__name__)
STAFF_CSRF_COOKIE='sj_web_csrf'

def _staff_csrf():
    return secrets.token_urlsafe(32)

def _ensure_admin_audit():
    with db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS admin_audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT NOT NULL, action TEXT NOT NULL, details_json TEXT DEFAULT '{}', created_at REAL NOT NULL)")

def _log_admin_audit(employee_id: str, action: str, details: dict|None=None):
    import time as _time, json as _json
    _ensure_admin_audit()
    with db() as con:
        con.execute("INSERT INTO admin_audit_events(employee_id,action,details_json,created_at) VALUES(?,?,?,?)",(employee_id,action,_json.dumps(details or {},ensure_ascii=False),_time.time()))


def _token_from_request(kind='web'):
    if kind=='mobile':
        a=request.headers.get('Authorization','')
        return a[7:].strip() if a.lower().startswith('bearer ') else ''
    return request.cookies.get('sj_staff_session','')


def _auth(required=True,mobile=False):
    emp=employee_from_token(_token_from_request('mobile' if mobile else 'web'))
    if required and not emp: return None, (jsonify({'ok':False,'error':'STAFF_AUTH_REQUIRED'}),401)
    if emp and mobile:
        device_id=request.headers.get('X-SuperJet-Device-Id','').strip()
        if device_id:
            touch_mobile_presence(emp['employee_id'],device_id)
            emp['mobile_online']=1; emp['mobile_last_seen']=__import__('time').time(); emp['mobile_device_id']=device_id
    return emp,None


def _is_manager(emp): return str(emp.get('role') or '')=='manager'


def _booking_sync(booking_id):
    # Read existing Web booking row without importing app.py to avoid circular side effects.
    from sqlite3 import connect
    p=BASE_DIR/'data'/'web_booking.db'; con=connect(p); con.row_factory=__import__('sqlite3').Row
    try:
        r=con.execute('SELECT * FROM booking_requests WHERE booking_id=?',(booking_id,)).fetchone()
        if not r:return None
        d=dict(r)
        d['trip']=json.loads(d.pop('trip_json') or '{}'); d['seats']=json.loads(d.pop('seats_json') or '[]')
        return d
    finally: con.close()

@bp.get('/staff/login')
def staff_login_page():
    # The global API guard requires a CSRF cookie for every state-changing
    # request. The original login page did not issue that cookie, so even a
    # correct username/password could never reach the login handler.
    from flask import make_response
    resp = make_response(render_template('staff_login.html'))
    resp.set_cookie(STAFF_CSRF_COOKIE, _staff_csrf(), httponly=False, secure=request.is_secure,
                    samesite='Lax', max_age=12*3600, path='/')
    return resp

@bp.get('/staff')
def staff_page():
    resp=make_response(render_template('staff_dashboard.html'))
    if not request.cookies.get(STAFF_CSRF_COOKIE):
        resp.set_cookie(STAFF_CSRF_COOKIE,_staff_csrf(),httponly=False,secure=request.is_secure,samesite='Lax',max_age=12*3600,path='/')
    return resp

@bp.post('/api/staff/login')
def staff_login():
    data=request.get_json(silent=True) or {}; u=str(data.get('username') or '').strip().lower(); p=str(data.get('password') or '')
    with db() as con: row=con.execute('SELECT * FROM employees WHERE username=? AND active=1',(u,)).fetchone()
    if not row or not _verify_password(p,str(row['password_hash'])): return jsonify({'ok':False,'error':'بيانات الدخول غير صحيحة'}),401
    token=create_session(str(row['employee_id']),'web')
    resp=make_response(jsonify({'ok':True,'employee':{k:row[k] for k in ('employee_id','username','name','mobile','whatsapp','role','shift_name','status')}}))
    resp.set_cookie('sj_staff_session',token,httponly=True,samesite='Lax',secure=request.is_secure,max_age=SESSION_TTL,path='/')
    return resp

@bp.post('/api/staff/logout')
def staff_logout():
    t=_token_from_request(); destroy_session(t); r=make_response(jsonify({'ok':True})); r.delete_cookie('sj_staff_session',path='/'); return r

@bp.post('/api/mobile/logout')
def mobile_logout():
    token=_token_from_request('mobile')
    emp,e=_auth(mobile=True)
    if e:return e
    device_id=request.headers.get('X-SuperJet-Device-Id','').strip()
    if device_id:
        set_mobile_presence(emp['employee_id'],device_id,False)
        with db() as con:
            con.execute("UPDATE employee_devices SET last_seen=? WHERE device_id=? AND employee_id=?",(__import__('time').time(),device_id,emp['employee_id']))
    destroy_session(token)
    return jsonify({'ok':True,'online':False})


@bp.post('/api/mobile/heartbeat')
def mobile_heartbeat():
    emp,e=_auth(mobile=True)
    if e:return e
    device_id=request.headers.get('X-SuperJet-Device-Id','').strip()
    if not device_id:return jsonify({'ok':False,'error':'DEVICE_ID_REQUIRED'}),400
    with db() as con:
        row=con.execute('SELECT employee_id,active FROM employee_devices WHERE device_id=?',(device_id,)).fetchone()
        if not row or not int(row['active']) or str(row['employee_id'])!=str(emp['employee_id']):return jsonify({'ok':False,'error':'DEVICE_BINDING_INVALID'}),403
        con.execute('UPDATE employee_devices SET last_seen=? WHERE device_id=?',(__import__('time').time(),device_id))
    return jsonify({'ok':True,'online':True,'employee_id':emp['employee_id'],'last_seen':__import__('time').time()})


@bp.get('/api/staff/me')
def staff_me():
    emp,e=_auth();
    if e:return e
    return jsonify({'ok':True,'employee':{k:emp.get(k) for k in ('employee_id','username','name','mobile','whatsapp','role','shift_name','status')}})

@bp.get('/api/staff/dashboard')
def staff_dashboard():
    emp,e=_auth();
    if e:return e
    return jsonify({'ok':True,'dashboard':dashboard(emp['employee_id'],_is_manager(emp))})

@bp.get('/api/staff/operations/<operation_id>/proof-image')
def staff_proof_image(operation_id):
    emp,e=_auth()
    if e:return e
    op=operation_details(operation_id,emp['employee_id'],_is_manager(emp))
    if not op:return jsonify({'ok':False,'error':'OPERATION_NOT_FOUND'}),404
    proof=op.get('proof') or {}; path=Path(str(proof.get('path') or '')).resolve()
    try:
        base=UPLOAD_DIR.resolve()
        if not path.is_file() or base not in path.parents:return jsonify({'ok':False,'error':'PROOF_FILE_NOT_FOUND'}),404
    except Exception:return jsonify({'ok':False,'error':'PROOF_FILE_NOT_FOUND'}),404
    return send_file(path,conditional=True,max_age=0)

@bp.get('/api/staff/operations/<operation_id>')
def staff_operation(operation_id):
    emp,e=_auth();
    if e:return e
    op=operation_details(operation_id,emp['employee_id'],_is_manager(emp),allow_shared=True)
    if not op:return jsonify({'ok':False,'error':'OPERATION_NOT_FOUND'}),404
    return jsonify({'ok':True,'operation':op})

def _reconcile_operation_proof(operation_id: str, employee_id: str, manager: bool=False):
    op=operation_details(operation_id,employee_id,manager)
    if not op:
        return None, {'ok':False,'error':'OPERATION_NOT_FOUND'}
    proof=op.get('proof') or {}
    path=str(proof.get('path') or '').strip()
    if not path:
        return op, {'ok':False,'error':'PROOF_NOT_FOUND','message':'لا توجد صورة إثبات محفوظة لهذه العملية.'}
    p=Path(path)
    if not p.is_file():
        return op, {'ok':False,'error':'PROOF_FILE_NOT_FOUND','message':'ملف إثبات الدفع غير موجود على الخادم.'}
    account=None
    try:
        aid=str(op.get('account_id') or '')
        for a in list_payment_accounts(active_only=False):
            if str(a.get('account_id') or '')==aid:
                account={'id':a.get('account_id'),'label':a.get('label'),'method':a.get('method'),'pay_to':a.get('pay_to')}
                break
    except Exception:
        pass
    from payment_reconciliation import reconcile_payment
    rec=reconcile_payment(op.get('amount'),op.get('customer_phone',''),p,account)
    try:
        with db() as con:
            con.execute("UPDATE payment_proofs SET vision_json=?,uploaded_at=? WHERE proof_id=?",(json.dumps(rec.get('vision') or {},ensure_ascii=False),time.time(),proof.get('proof_id')))
            con.execute("UPDATE booking_requests SET payment_reconciliation_json=?,note=? WHERE booking_id=?",(json.dumps(rec,ensure_ascii=False),"تمت إعادة قراءة إثبات الدفع بواسطة Gemini وعرض النتيجة للموظف.",op.get('booking_id')))
    except Exception:
        pass
    try:
        match=match_operation(operation_id)
    except Exception as ex:
        match={'status':'UNMATCHED','score':0,'reason':f'MATCH_ERROR:{type(ex).__name__}:{ex}'}
    return operation_details(operation_id,employee_id,manager) or op, {'ok':True,'reconciliation':rec,'match':match}

@bp.post('/api/staff/operations/<operation_id>/approve')
def staff_approve(operation_id):
    emp,e=_auth();
    if e:return e
    op=operation_details(operation_id,emp['employee_id'],_is_manager(emp))
    if not op:return jsonify({'ok':False,'error':'OPERATION_NOT_FOUND'}),404
    if op.get('status')=='TICKET_READY': return jsonify({'ok':True,'status':'TICKET_READY','ticket_ready':True,'message':'العملية معتمدة بالفعل والتذكرة موجودة في «تذكرتي».'})
    if op.get('status')=='PAYMENT_REJECTED': return jsonify({'ok':False,'error':'OPERATION_ALREADY_REJECTED','message':'العملية مرفوضة بالفعل. استخدم «تذكرتي» لإرسال إثبات جديد عند الحاجة.'}),409
    # Always refresh server-side Gemini evidence once before the final decision when a proof exists.
    try:
        if (op.get('proof') or {}).get('path'):
            refreshed, rr = _reconcile_operation_proof(operation_id,emp['employee_id'],_is_manager(emp))
            if refreshed: op = refreshed
    except Exception as ex:
        log_staff_action(operation_id,emp['employee_id'],'PAYMENT_VISION_REFRESH_FAILED',{'error':f'{type(ex).__name__}:{ex}'})
    try:
        from integrated_operations import match_operation
        match_operation(operation_id)
        op=operation_details(operation_id,emp['employee_id'],_is_manager(emp)) or op
    except Exception: pass
    status=(op.get('matches') or [{}])[0].get('status') or 'UNMATCHED'
    note=str((request.get_json(silent=True) or {}).get('note') or '').strip()
    if status in {'CONFLICT','SUSPICIOUS','UNMATCHED'} and not _is_manager(emp):
        return jsonify({'ok':False,'error':'MANAGER_REQUIRED','message':'هذه العملية تحتاج اعتماد المدير بسبب عدم اكتمال أو تعارض أدلة الدفع.'}),403
    import sqlite3,time
    now=time.time()
    with sqlite3.connect(BASE_DIR/'data'/'web_booking.db') as con:
        con.execute("UPDATE operations SET status='PAYMENT_APPROVED',approved_at=?,approved_by=?,updated_at=? WHERE operation_id=?",(now,emp['employee_id'],now,operation_id))
        con.execute("UPDATE booking_requests SET status='PAYMENT_APPROVED',approved_by_staff_id=?,approved_at=?,note=? WHERE booking_id=?",(emp['employee_id'],now,'تم اعتماد الدفع وجاري إصدار التذكرة تلقائيًا داخل حساب العميل.',op['booking_id']))
    log_staff_action(operation_id,emp['employee_id'],'PAYMENT_APPROVED',{'match_status':status,'note':note})
    try:
        from app import _attempt_final_booking
        result=_attempt_final_booking(op['booking_id'],emp['employee_id'])
    except Exception as ex:
        result={'ok':False,'error':f'FINALIZE_EXCEPTION:{type(ex).__name__}:{ex}'}
    if not result.get('ok'):
        return jsonify({'ok':False,'error':result.get('error') or 'FINAL_BOOKING_FAILED','status':'PAYMENT_APPROVED','finalize_details':result}),502
    try:
        with db() as con:
            con.execute("INSERT INTO chat_messages(booking_id,sender_type,sender_id,message,message_type,created_at) VALUES(?,?,?,?,?,?)",(op['booking_id'],'staff',str(emp['employee_id']),'تم اعتماد الدفع بنجاح ✅ وتم إصدار نموذج التذكرة. يمكنك فتح «تذكرتي» لعرض التذكرة وبيانات الحجز.','text',time.time()))
    except Exception: pass
    return jsonify({'ok':True,'status':result.get('status','TICKET_READY'),'ticket_ready':result.get('status')=='TICKET_READY','final_booking_ref':result.get('final_booking_ref',''),'delivery':result.get('delivery','WEB_READY'),'message':'تم تأكيد الدفع وإصدار نموذج التذكرة تلقائيًا داخل «تذكرتي».','payment_evidence':{'match_status':status,'vision':op.get('payment_vision') or {},'android':(op.get('android') or [])[:3]}})

@bp.post('/api/staff/operations/<operation_id>/reanalyze-proof')
def staff_reanalyze_proof(operation_id):
    emp,e=_auth()
    if e:return e
    op,result=_reconcile_operation_proof(operation_id,emp['employee_id'],_is_manager(emp))
    if not result.get('ok'):
        return jsonify(result),404 if result.get('error') in {'OPERATION_NOT_FOUND','PROOF_NOT_FOUND','PROOF_FILE_NOT_FOUND'} else 400
    return jsonify({'ok':True,'operation':op,'reconciliation':result.get('reconciliation') or {},'match':result.get('match') or {}})

@bp.post('/api/staff/operations/<operation_id>/reject')
def staff_reject(operation_id):
    emp,e=_auth();
    if e:return e
    op=operation_details(operation_id,emp['employee_id'],_is_manager(emp))
    if not op:return jsonify({'ok':False,'error':'OPERATION_NOT_FOUND'}),404
    data=request.get_json(silent=True) or {}; reason=str(data.get('reason') or 'رفض الموظف لإثبات الدفع').strip()
    import sqlite3,time
    con=sqlite3.connect(BASE_DIR/'data'/'web_booking.db')
    try:
        now=time.time(); con.execute("UPDATE operations SET status='PAYMENT_REJECTED',rejected_at=?,rejected_by=?,updated_at=? WHERE operation_id=?",(now,emp['employee_id'],now,operation_id)); con.execute("UPDATE booking_requests SET status='PAYMENT_REJECTED',rejected_by_staff_id=?,rejected_at=?,note=? WHERE booking_id=?",(emp['employee_id'],now,reason,op['booking_id'])); con.commit()
    finally: con.close()
    log_staff_action(operation_id,emp['employee_id'],'PAYMENT_REJECTED',{'reason':reason})
    try:
        with db() as con:
            con.execute("INSERT INTO chat_messages(booking_id,sender_type,sender_id,message,message_type,created_at) VALUES(?,?,?,?,?,?)",(op['booking_id'],'staff',str(emp['employee_id']),f'لم يتم اعتماد إثبات الدفع حاليًا ❌. السبب: {reason}. يمكنك فتح «تذكرتي» لإرسال إثبات جديد أو إدخال بيانات التحويل يدويًا.','text',time.time()))
    except Exception: pass
    return jsonify({'ok':True,'status':'PAYMENT_REJECTED'})

@bp.post('/api/staff/operations/<operation_id>/ticket')
def staff_ticket(operation_id):
    emp,e=_auth();
    if e:return e
    op=operation_details(operation_id,emp['employee_id'],_is_manager(emp))
    if not op:return jsonify({'ok':False,'error':'OPERATION_NOT_FOUND'}),404
    if op.get('status') not in {'PAYMENT_APPROVED','BOOKING_IN_PROGRESS','TICKET_READY'}:
        return jsonify({'ok':False,'error':'PAYMENT_NOT_APPROVED','message':'اعتمد الدفع أولًا.'}),409
    files=request.files.getlist('ticket'); files=[f for f in files if f and f.filename]
    if not files:return jsonify({'ok':False,'error':'TICKET_REQUIRED'}),400
    if len(files)>4:return jsonify({'ok':False,'error':'MAX_4_IMAGES'}),400
    paths=[]; urls=[]; errors=[]
    for f in files:
        ext=Path(secure_filename(f.filename)).suffix.lower()
        if ext not in {'.jpg','.jpeg','.png','.webp'}: return jsonify({'ok':False,'error':'INVALID_TICKET_TYPE'}),400
        name=f"{operation_id}_{secrets.token_hex(7)}{ext}"; path=TICKET_DIR/name; f.save(path)
        try:
            with Image.open(path) as im: im.verify()
        except Exception: path.unlink(missing_ok=True); return jsonify({'ok':False,'error':'INVALID_TICKET_IMAGE'}),400
        paths.append(str(path)); tok=media_token(operation_id,str(path)); urls.append(f"{PUBLIC_ORIGIN}/staff/media/{tok}")
    import sqlite3,time,json as _json
    final_ref=str(request.form.get('final_booking_ref') or '').strip()
    con=sqlite3.connect(BASE_DIR/'data'/'web_booking.db');
    try:
        now=time.time(); con.execute("UPDATE operations SET status='TICKET_READY',final_booking_ref=?,ticket_delivery='PENDING',updated_at=? WHERE operation_id=?",(final_ref,now,operation_id))
        con.execute("UPDATE booking_requests SET status='TICKET_READY',final_booking_ref=?,manual_ticket_path=?,manual_ticket_delivery=?,delivery_status='WEB_READY',note=? WHERE booking_id=?",(final_ref,_json.dumps(paths), 'UPLOADED', 'تم رفع التذكرة الفعلية بواسطة الموظف.',op['booking_id']))
        con.commit()
    finally:con.close()
    # Send all ticket images through the existing El Mujib customer media adapter.
    from payment_workflow import send_customer_media_public, send_customer_text
    sent=[]
    for u in urls:
        try: sent.append(send_customer_media_public(op['customer_phone'],u,f"تذكرة SuperJet — {op['booking_id']}"))
        except Exception as ex: sent.append({'ok':False,'error':str(ex)})
    ok=all(bool(x.get('ok') or x.get('status') in {200,201}) for x in sent) if sent else False
    con=sqlite3.connect(BASE_DIR/'data'/'web_booking.db')
    try:
        con.execute("UPDATE operations SET ticket_delivery=?,updated_at=? WHERE operation_id=?",('SENT' if ok else 'FAILED',time.time(),operation_id)); con.execute("UPDATE booking_requests SET manual_ticket_delivery=?,delivery_status=? WHERE booking_id=?",('SENT' if ok else 'FAILED','SENT' if ok else 'WEB_READY',op['booking_id']));con.commit()
    finally:con.close()
    log_staff_action(operation_id,emp['employee_id'],'TICKET_UPLOADED',{'paths':paths,'sent':sent,'final_booking_ref':final_ref})
    return jsonify({'ok':True,'status':'TICKET_READY','delivery':'SENT' if ok else 'FAILED','sent':sent})

@bp.get('/staff/media/<token>')
def staff_media(token):
    path=resolve_media_token(token)
    if not path or not Path(path).is_file(): abort(404)
    return send_file(path)

@bp.post('/api/staff/admin/employees')
def admin_create_employee():
    emp,e=_auth();
    if e:return e
    if not _is_manager(emp):return jsonify({'ok':False,'error':'MANAGER_REQUIRED'}),403
    d=request.get_json(silent=True) or {}
    try: created,_=create_employee(d.get('name'),d.get('username'),d.get('password'),d.get('mobile'),d.get('whatsapp'),d.get('role','customer_service'),d.get('shift_name',''))
    except Exception as ex:return jsonify({'ok':False,'error':str(ex)}),400
    _log_admin_audit(emp['employee_id'],'EMPLOYEE_CREATED',{'employee_id':created['employee_id']})
    return jsonify({'ok':True,'employee':created})

@bp.get('/api/staff/admin/employees')
def admin_employees():
    emp,e=_auth();
    if e:return e
    if not _is_manager(emp):return jsonify({'ok':False,'error':'MANAGER_REQUIRED'}),403
    return jsonify({'ok':True,'employees':list_employees(),'accounts':list_payment_accounts()})

@bp.get('/api/staff/admin/shifts')
def admin_shifts():
    emp,e=_auth()
    if e:return e
    if not _is_manager(emp):return jsonify({'ok':False,'error':'MANAGER_REQUIRED'}),403
    return jsonify({'ok':True,'shifts':list_shifts(active_only=False)})

@bp.post('/api/staff/admin/shifts')
def admin_shift_create():
    emp,e=_auth()
    if e:return e
    if not _is_manager(emp):return jsonify({'ok':False,'error':'MANAGER_REQUIRED'}),403
    try: sh=add_shift((request.get_json(silent=True) or {}).get('name'))
    except Exception as ex:return jsonify({'ok':False,'error':str(ex)}),400
    _log_admin_audit(emp['employee_id'],'SHIFT_CREATED',{'shift_id':sh['shift_id'],'name':sh['name']})
    return jsonify({'ok':True,'shift':sh})

@bp.post('/api/staff/admin/shifts/<shift_id>')
def admin_shift_update(shift_id):
    emp,e=_auth()
    if e:return e
    if not _is_manager(emp):return jsonify({'ok':False,'error':'MANAGER_REQUIRED'}),403
    d=request.get_json(silent=True) or {}
    try:
        old=next((x for x in list_shifts(active_only=False) if x.get('shift_id')==shift_id),None)
        if not old:return jsonify({'ok':False,'error':'SHIFT_NOT_FOUND'}),404
        name=str(d.get('name') or '').strip()
        if name and name != old.get('name'):
            from integrated_operations import replace_shift_name
            replace_shift_name(str(old.get('name')),name)
        sh=update_shift(shift_id,name=name or old.get('name',''),active=d.get('active'))
    except Exception as ex:return jsonify({'ok':False,'error':str(ex)}),400
    return jsonify({'ok':True,'shift':sh})

@bp.delete('/api/staff/admin/shifts/<shift_id>')
def admin_shift_delete(shift_id):
    emp,e=_auth()
    if e:return e
    if not _is_manager(emp):return jsonify({'ok':False,'error':'MANAGER_REQUIRED'}),403
    try:r=delete_shift(shift_id)
    except Exception as ex:return jsonify({'ok':False,'error':str(ex)}),409
    _log_admin_audit(emp['employee_id'],'SHIFT_DELETED',r)
    return jsonify(r)

@bp.post('/api/staff/admin/employees/<employee_id>')
def admin_update_employee(employee_id):
    emp,e=_auth();
    if e:return e
    if not _is_manager(emp):return jsonify({'ok':False,'error':'MANAGER_REQUIRED'}),403
    d=request.get_json(silent=True) or {}; update_employee(employee_id,**d)
    return jsonify({'ok':True,'employee':get_employee(employee_id)})

@bp.delete('/api/staff/admin/employees/<employee_id>')
def admin_delete_employee(employee_id):
    emp,e=_auth();
    if e:return e
    if not _is_manager(emp):return jsonify({'ok':False,'error':'MANAGER_REQUIRED'}),403
    if str(employee_id)==str(emp['employee_id']):return jsonify({'ok':False,'error':'CANNOT_DELETE_SELF','message':'لا يمكن حذف حساب المدير الحالي.'}),409
    import sqlite3,time
    with db() as con:
        dep=con.execute('SELECT (SELECT COUNT(*) FROM payment_accounts WHERE employee_id=?),(SELECT COUNT(*) FROM operations WHERE employee_id=?),(SELECT COUNT(*) FROM staff_sessions WHERE employee_id=? )',(employee_id,employee_id,employee_id)).fetchone()
        if int(dep[0] or 0) or int(dep[1] or 0):
            con.execute("UPDATE employees SET active=0,status='OFF_SHIFT',updated_at=? WHERE employee_id=?",(time.time(),employee_id))
            con.execute('UPDATE payment_accounts SET active=0,updated_at=? WHERE employee_id=?',(time.time(),employee_id))
            con.execute('UPDATE employee_devices SET active=0 WHERE employee_id=?',(employee_id,))
            con.execute('DELETE FROM staff_sessions WHERE employee_id=?',(employee_id,))
            return jsonify({'ok':True,'mode':'soft_delete','message':'للموظف عمليات/حسابات مرتبطة، تم تعطيله وحفظ سجله بدل الحذف النهائي.'})
        con.execute('DELETE FROM employee_devices WHERE employee_id=?',(employee_id,))
        con.execute('DELETE FROM staff_sessions WHERE employee_id=?',(employee_id,))
        con.execute('DELETE FROM payment_accounts WHERE employee_id=?',(employee_id,))
        con.execute('DELETE FROM employees WHERE employee_id=?',(employee_id,))
    return jsonify({'ok':True,'mode':'hard_delete','message':'تم حذف الموظف.'})

@bp.post('/api/staff/admin/employees/<employee_id>/restore')
def admin_restore_employee(employee_id):
    emp,e=_auth();
    if e:return e
    if not _is_manager(emp):return jsonify({'ok':False,'error':'MANAGER_REQUIRED'}),403
    update_employee(employee_id,active=1,status='OFF_SHIFT')
    return jsonify({'ok':True,'employee':get_employee(employee_id)})

@bp.post('/api/staff/admin/employees/<employee_id>/password')
def admin_password(employee_id):
    emp,e=_auth();
    if e:return e
    if not _is_manager(emp):return jsonify({'ok':False,'error':'MANAGER_REQUIRED'}),403
    try:set_password(employee_id,str((request.get_json(silent=True) or {}).get('password') or ''))
    except Exception as ex:return jsonify({'ok':False,'error':str(ex)}),400
    return jsonify({'ok':True})

@bp.post('/api/staff/admin/accounts')
def admin_account():
    emp,e=_auth();
    if e:return e
    if not _is_manager(emp):return jsonify({'ok':False,'error':'MANAGER_REQUIRED'}),403
    d=request.get_json(silent=True) or {}
    try:a=add_payment_account(str(d.get('employee_id') or ''),str(d.get('method') or ''),str(d.get('label') or ''),str(d.get('pay_to') or ''),str(d.get('account_type') or ''),int(d.get('priority') or 0),bool(d.get('shared_for_staff') or str(d.get('method') or '')=='إنستا باي'))
    except Exception as ex:return jsonify({'ok':False,'error':str(ex)}),400
    return jsonify({'ok':True,'account':a})

@bp.post('/api/staff/admin/devices/<device_id>/revoke')
def admin_revoke_device(device_id):
    emp,e=_auth();
    if e:return e
    if not _is_manager(emp):return jsonify({'ok':False,'error':'MANAGER_REQUIRED'}),403
    import sqlite3
    with db() as con: con.execute('UPDATE employee_devices SET active=0 WHERE device_id=?',(device_id,))
    return jsonify({'ok':True})

@bp.post('/api/staff/admin/employees/<employee_id>/revoke-device')
def admin_revoke_employee_devices(employee_id):
    emp,e=_auth()
    if e:return e
    if not _is_manager(emp):return jsonify({'ok':False,'error':'MANAGER_REQUIRED'}),403
    with db() as con:
        cur=con.execute('UPDATE employee_devices SET active=0 WHERE employee_id=?',(employee_id,))
        con.execute("DELETE FROM staff_sessions WHERE employee_id=? AND kind='mobile'",(employee_id,))
    return jsonify({'ok':True,'revoked':cur.rowcount})

@bp.get('/api/staff/search')
def staff_search():
    emp,e=_auth()
    if e:return e
    q=str(request.args.get('q') or '').strip()
    if len(q)<2:return jsonify({'ok':False,'error':'SEARCH_QUERY_REQUIRED','message':'اكتب كود الحجز أو رقم الموبايل أو اسم العميل أو المرجع.'}),400
    rows=search_operations(q,employee_id=emp['employee_id'],manager=_is_manager(emp),limit=30)
    return jsonify({'ok':True,'results':rows})

@bp.get('/api/mobile/search')
def mobile_search():
    emp,e=_auth(mobile=True)
    if e:return e
    q=str(request.args.get('q') or '').strip()
    if len(q)<2:return jsonify({'ok':False,'error':'SEARCH_QUERY_REQUIRED','message':'اكتب كود الحجز أو رقم الموبايل أو الاسم أو المرجع.'}),400
    return jsonify({'ok':True,'results':search_operations(q,employee_id=emp['employee_id'],manager=_is_manager(emp),limit=20)})

@bp.get('/api/staff/chat/unread')
def staff_chat_unread():
    emp,e=_auth()
    if e:return e
    with db() as con:
        if _is_manager(emp):
            rows=con.execute("SELECT cm.id,cm.booking_id,cm.message,cm.created_at,o.customer_name,o.operation_id FROM chat_messages cm JOIN operations o ON o.booking_id=cm.booking_id WHERE cm.sender_type='customer' AND (cm.read_at IS NULL OR cm.read_at=0) ORDER BY cm.id DESC LIMIT 50").fetchall()
        else:
            rows=con.execute("SELECT cm.id,cm.booking_id,cm.message,cm.created_at,o.customer_name,o.operation_id FROM chat_messages cm JOIN operations o ON o.booking_id=cm.booking_id WHERE cm.sender_type='customer' AND (cm.read_at IS NULL OR cm.read_at=0) AND o.employee_id=? ORDER BY cm.id DESC LIMIT 50",(emp['employee_id'],)).fetchall()
    return jsonify({'ok':True,'count':len(rows),'latest_message_id':(int(rows[0]['id']) if rows else 0),'messages':[dict(r) for r in rows]})

@bp.get('/api/mobile/chat/notifications')
def mobile_chat_notifications():
    emp,e=_auth(mobile=True)
    if e:return e
    try: since=max(0,int(request.args.get('since_id') or 0)); wait=max(0,min(20,int(request.args.get('wait') or 20)))
    except Exception: since,wait=0,20
    deadline=__import__('time').time()+wait
    while True:
        with db() as con:
            rows=con.execute("SELECT cm.id,cm.booking_id,cm.message,cm.created_at,o.customer_name,o.operation_id FROM chat_messages cm JOIN operations o ON o.booking_id=cm.booking_id WHERE cm.sender_type='customer' AND cm.id>? AND o.employee_id=? ORDER BY cm.id ASC LIMIT 30",(since,emp['employee_id'])).fetchall()
        if rows or __import__('time').time()>=deadline: break
        __import__('time').sleep(1)
    return jsonify({'ok':True,'messages':[dict(r) for r in rows]})

@bp.get('/api/mobile/operations/<operation_id>/chat')
def mobile_chat_get(operation_id):
    emp,e=_auth(mobile=True)
    if e:return e
    op=operation_details(operation_id,emp['employee_id'],_is_manager(emp))
    if not op:return jsonify({'ok':False,'error':'OPERATION_NOT_FOUND'}),404
    import sqlite3
    webdb=BASE_DIR/'data'/'web_booking.db'; con=sqlite3.connect(webdb); con.row_factory=sqlite3.Row
    try:
        con.execute("UPDATE chat_messages SET read_at=? WHERE booking_id=? AND sender_type='customer' AND (read_at IS NULL OR read_at=0)",(__import__('time').time(),op['booking_id']))
        msgs=[dict(r) for r in con.execute("SELECT id,sender_type,sender_id,message,message_type,media_path,media_token,media_name,created_at,read_at FROM chat_messages WHERE booking_id=? ORDER BY id ASC LIMIT 300",(op['booking_id'],)).fetchall()]
        for m in msgs: m['media_url']='/api/chat/media/'+str(m.get('media_token') or '') if m.get('media_token') else ''
        con.commit()
    finally: con.close()
    return jsonify({'ok':True,'booking_id':op['booking_id'],'messages':msgs})

@bp.post('/api/mobile/operations/<operation_id>/chat')
def mobile_chat_post(operation_id):
    emp,e=_auth(mobile=True)
    if e:return e
    op=operation_details(operation_id,emp['employee_id'],_is_manager(emp))
    if not op:return jsonify({'ok':False,'error':'OPERATION_NOT_FOUND'}),404
    json_body=request.get_json(silent=True) if request.is_json else {}
    msg_source=json_body.get('message') if isinstance(json_body,dict) else None
    msg=re.sub(r'\s+',' ',str(msg_source if msg_source is not None else (request.form.get('message') or '')).strip())[:1000]
    media_path=media_token=media_name=''
    try:
        from app import _save_chat_upload, _chat_message_dict
        f=request.files.get('media')
        if f and f.filename: media_path,media_token,media_name=_save_chat_upload(op['booking_id'],f)
    except ValueError as ex: return jsonify({'ok':False,'error':str(ex)}),400
    except Exception as ex: return jsonify({'ok':False,'error':f'CHAT_MEDIA_ERROR:{type(ex).__name__}:{ex}'}),400
    if not msg and not media_path:return jsonify({'ok':False,'error':'MESSAGE_OR_MEDIA_REQUIRED'}),400
    import sqlite3
    webdb=BASE_DIR/'data'/'web_booking.db'; con=sqlite3.connect(webdb); con.row_factory=sqlite3.Row
    try:
        now=__import__('time').time(); mtype='image' if media_path else 'text'; cur=con.execute("INSERT INTO chat_messages(booking_id,sender_type,sender_id,message,message_type,media_path,media_token,media_name,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(op['booking_id'],'staff',emp['employee_id'],msg,mtype,media_path,media_token,media_name,now)); con.commit(); mid=cur.lastrowid
    finally: con.close()
    return jsonify({'ok':True,'message':{'id':mid,'sender_type':'staff','sender_id':emp['employee_id'],'message':msg,'message_type':mtype,'media_name':media_name,'media_url':('/api/chat/media/'+media_token if media_token else ''),'created_at':now}})

@bp.post('/api/mobile/login')
def mobile_login():
    data=request.get_json(silent=True) or {}; u=str(data.get('username') or '').strip().lower(); p=str(data.get('password') or ''); device_id=str(data.get('device_id') or '').strip()
    if not u or not p or not device_id:return jsonify({'ok':False,'error':'MOBILE_LOGIN_DATA_REQUIRED'}),400
    with db() as con: row=con.execute('SELECT * FROM employees WHERE username=? AND active=1',(u,)).fetchone()
    if not row or not _verify_password(p,str(row['password_hash'])):return jsonify({'ok':False,'error':'بيانات الدخول غير صحيحة'}),401
    with db() as con:
        bound=con.execute('SELECT * FROM employee_devices WHERE device_id=? AND active=1',(device_id,)).fetchone()
        other=con.execute('SELECT * FROM employee_devices WHERE employee_id=? AND active=1 AND device_id<>?',(row['employee_id'],device_id)).fetchone()
        if other and not bound:return jsonify({'ok':False,'error':'DEVICE_ALREADY_BOUND','message':'هذا الموظف مرتبط بجهاز آخر. اطلب من المدير إعادة ربط الجهاز.'}),409
        raw=secrets.token_urlsafe(36); th=__import__('hashlib').sha256(raw.encode()).hexdigest(); now=__import__('time').time()
        if bound:
            old_employee=str(bound['employee_id'] or '')
            if old_employee and old_employee!=str(row['employee_id']):
                con.execute("DELETE FROM staff_sessions WHERE token_hash=? AND kind='mobile'",(bound['token_hash'],))
                con.execute("UPDATE employees SET mobile_online=0,mobile_last_seen=?,mobile_device_id='',updated_at=? WHERE employee_id=?",(now,now,old_employee))
            con.execute('UPDATE employee_devices SET token_hash=?,last_seen=?,employee_id=?,active=1 WHERE device_id=?',(th,now,row['employee_id'],device_id))
        else:
            con.execute('INSERT INTO employee_devices(device_id,employee_id,token_hash,active,first_seen,last_seen) VALUES(?,?,?,?,?,?)',(device_id,row['employee_id'],th,1,now,now))
        con.execute("DELETE FROM staff_sessions WHERE employee_id=? AND kind='mobile' AND token_hash<>?",(row['employee_id'],th))
        con.execute('INSERT OR REPLACE INTO staff_sessions(token_hash,employee_id,kind,expires_at,created_at) VALUES(?,?,?,?,?)',(th,row['employee_id'],'mobile',now+30*24*3600,now))
        con.execute("UPDATE employees SET mobile_online=1,mobile_last_seen=?,mobile_device_id=?,updated_at=? WHERE employee_id=?",(now,device_id,now,row['employee_id']))
    with db() as con:
        alert_event_id=int(con.execute('SELECT COALESCE(MAX(id),0) FROM operation_events').fetchone()[0] or 0)
        alert_chat_id=int(con.execute('SELECT COALESCE(MAX(id),0) FROM chat_messages').fetchone()[0] or 0)
    return jsonify({'ok':True,'token':raw,'employee':{k:row[k] for k in ('employee_id','username','name','mobile','whatsapp','role','shift_name','status')},'online':True,'alert_cursor':{'event_id':alert_event_id,'chat_id':alert_chat_id}})

@bp.get('/api/mobile/dashboard')
def mobile_dashboard():
    emp,e=_auth(mobile=True)
    if e:return e
    return jsonify({'ok':True,'dashboard':dashboard(emp['employee_id'],_is_manager(emp)),'employee':{k:emp.get(k) for k in ('employee_id','name','role','shift_name','status')}})

@bp.get('/api/mobile/alerts')
def mobile_alerts():
    emp,e=_auth(mobile=True)
    if e:return e
    try:
        since_event=max(0,int(request.args.get('since_event_id') or 0)); since_chat=max(0,int(request.args.get('since_chat_id') or 0)); wait=max(0,min(int(request.args.get('wait') or 20),25)); initialize=str(request.args.get('initialize') or '')=='1'
    except Exception:return jsonify({'ok':False,'error':'INVALID_CURSOR'}),400
    manager=_is_manager(emp)
    if initialize:
        with db() as con:
            le=int(con.execute('SELECT COALESCE(MAX(id),0) FROM operation_events').fetchone()[0] or 0)
            lc=int(con.execute('SELECT COALESCE(MAX(id),0) FROM chat_messages').fetchone()[0] or 0)
        return jsonify({'ok':True,'events':[],'messages':[],'last_event_id':le,'last_chat_id':lc,'online':True})
    deadline=__import__('time').time()+wait
    while True:
        with db() as con:
            if manager:
                events=con.execute("""SELECT e.id,e.operation_id,e.event_type,e.actor,e.details_json,e.created_at,o.booking_id,o.customer_name,o.from_name,o.to_name,o.travel_date,o.travel_time,o.amount,o.payment_method,o.status
                                  FROM operation_events e LEFT JOIN operations o ON o.operation_id=e.operation_id
                                  WHERE e.id>? AND e.event_type IN ('BOOKING_ASSIGNED','BOOKING_REASSIGNED','PROOF_RECEIVED','ANDROID_PAYMENT_RECEIVED')
                                  ORDER BY e.id ASC LIMIT 50""",(since_event,)).fetchall()
                chats=con.execute("""SELECT cm.id,cm.booking_id,o.operation_id,cm.sender_type,cm.sender_id,cm.message,cm.message_type,cm.media_token,cm.created_at
                                  FROM chat_messages cm LEFT JOIN operations o ON o.booking_id=cm.booking_id WHERE cm.id>? AND cm.sender_type='customer' ORDER BY cm.id ASC LIMIT 50""",(since_chat,)).fetchall()
            else:
                events=con.execute("""SELECT e.id,e.operation_id,e.event_type,e.actor,e.details_json,e.created_at,o.booking_id,o.customer_name,o.from_name,o.to_name,o.travel_date,o.travel_time,o.amount,o.payment_method,o.status
                                  FROM operation_events e JOIN operations o ON o.operation_id=e.operation_id
                                  LEFT JOIN payment_accounts pa ON pa.account_id=o.account_id
                                  WHERE e.id>? AND e.event_type IN ('BOOKING_ASSIGNED','BOOKING_REASSIGNED','PROOF_RECEIVED','ANDROID_PAYMENT_RECEIVED')
                                    AND (o.employee_id=? OR (e.event_type='ANDROID_PAYMENT_RECEIVED' AND o.payment_method='إنستا باي' AND COALESCE(pa.shared_for_staff,0)=1 AND EXISTS (SELECT 1 FROM employees ee WHERE ee.employee_id=? AND ee.active=1 AND ee.mobile_online=1 AND ee.mobile_last_seen>=?)))
                                  ORDER BY e.id ASC LIMIT 50""",(since_event,emp['employee_id'],emp['employee_id'],__import__('time').time()-90)).fetchall()
                chats=con.execute("""SELECT cm.id,cm.booking_id,o.operation_id,cm.sender_type,cm.sender_id,cm.message,cm.message_type,cm.media_token,cm.created_at
                                  FROM chat_messages cm JOIN operations o ON o.booking_id=cm.booking_id
                                  WHERE cm.id>? AND cm.sender_type='customer' AND o.employee_id=? ORDER BY cm.id ASC LIMIT 50""",(since_chat,emp['employee_id'])).fetchall()
        if events or chats or __import__('time').time()>=deadline: break
        __import__('time').sleep(0.8)
    ev=[dict(r) for r in events]; ch=[dict(r) for r in chats]
    for x in ch: x['media_url']=f"/api/chat/media/{x['media_token']}" if x.get('media_token') else ''
    return jsonify({'ok':True,'events':ev,'messages':ch,'last_event_id':max([since_event]+[int(x['id']) for x in ev]),'last_chat_id':max([since_chat]+[int(x['id']) for x in ch]),'online':True})


@bp.get('/api/mobile/notifications')
def mobile_notifications():
    emp,e=_auth(mobile=True)
    if e:return e
    known=('VODAFONE_CASH','ORANGE_CASH','ETISALAT_CASH','WE_PAY','INSTAPAY')
    marks=','.join('?' for _ in known)
    with db() as con:
        rows=con.execute(f"""SELECT event_id,provider,transaction_type,amount,reference,sender_phone,recipient_account,transaction_date,transaction_time,received_at,raw_json
            FROM android_transactions WHERE upper(COALESCE(provider,'')) IN ({marks})
              AND (employee_id=? OR upper(COALESCE(provider,''))='INSTAPAY')
            ORDER BY received_at DESC LIMIT 150""",(*known,emp['employee_id'])).fetchall()
    return jsonify({'ok':True,'notifications':[dict(r) for r in rows]})

@bp.get('/api/mobile/chats')
def mobile_chats():
    emp,e=_auth(mobile=True)
    if e:return e
    manager=_is_manager(emp)
    with db() as con:
        where='' if manager else 'AND o.employee_id=?'
        params=[] if manager else [emp['employee_id']]
        rows=con.execute(f"""SELECT o.operation_id,o.booking_id,o.status,o.customer_name,o.customer_phone,o.from_name,o.to_name,o.travel_date,o.travel_time,o.amount,
          (SELECT cm.message FROM chat_messages cm WHERE cm.booking_id=o.booking_id ORDER BY cm.id DESC LIMIT 1) last_message,
          (SELECT cm.created_at FROM chat_messages cm WHERE cm.booking_id=o.booking_id ORDER BY cm.id DESC LIMIT 1) last_message_at,
          (SELECT COUNT(*) FROM chat_messages cm WHERE cm.booking_id=o.booking_id AND cm.sender_type='customer' AND (cm.read_at IS NULL OR cm.read_at=0)) unread_count
          FROM operations o WHERE EXISTS(SELECT 1 FROM chat_messages c2 WHERE c2.booking_id=o.booking_id) {where}
          ORDER BY COALESCE(last_message_at,o.updated_at) DESC LIMIT 200""",params).fetchall()
    return jsonify({'ok':True,'chats':[dict(r) for r in rows]})

@bp.get('/api/mobile/payments')
def mobile_payments():
    emp,e=_auth(mobile=True)
    if e:return e
    manager=_is_manager(emp)
    with db() as con:
        where='' if manager else "WHERE (o.employee_id=? OR (o.payment_method='إنستا باي' AND EXISTS (SELECT 1 FROM payment_accounts pa WHERE pa.account_id=o.account_id AND pa.shared_for_staff=1)))"
        params=[] if manager else [emp['employee_id']]
        rows=con.execute(f"SELECT * FROM operations o {where} ORDER BY updated_at DESC LIMIT 250",params).fetchall()
        ops=[]
        for r in rows:
            op=dict(r)
            mm=con.execute("SELECT status,score,reason,checks_json,created_at FROM payment_matches WHERE operation_id=? ORDER BY created_at DESC LIMIT 1",(op['operation_id'],)).fetchone()
            op['match']=dict(mm) if mm else None
            if op.get('match'):
                try: op['match']['checks']=json.loads(op['match'].get('checks_json') or '{}')
                except Exception: op['match']['checks']={}
            br=con.execute("SELECT payment_reconciliation_json FROM booking_requests WHERE booking_id=?",(op['booking_id'],)).fetchone()
            op['reconciliation']=json.loads(br['payment_reconciliation_json'] or '{}') if br and br['payment_reconciliation_json'] else {}
            op['payment_vision']=(op.get('reconciliation') or {}).get('vision') or {}
            tx=con.execute("SELECT provider,amount,reference,sender_phone,recipient_account,transaction_date,transaction_time,received_at FROM android_transactions WHERE operation_id=? ORDER BY received_at DESC LIMIT 5",(op['operation_id'],)).fetchall()
            op['android']=[dict(x) for x in tx]
        success=con.execute("SELECT COUNT(*) c,COALESCE(SUM(amount),0) total FROM operations o WHERE o.status='TICKET_READY' AND (1=1)" + (" AND o.employee_id=?" if not manager else ""),( [emp['employee_id']] if not manager else [])).fetchone()
        pending=con.execute("SELECT COUNT(*) c,COALESCE(SUM(amount),0) total FROM operations o WHERE o.status IN ('PAYMENT_PENDING','PROOF_RECEIVED','PAYMENT_REVIEW')" + (" AND o.employee_id=?" if not manager else ""),( [emp['employee_id']] if not manager else [])).fetchone()
        rejected=con.execute("SELECT COUNT(*) c,COALESCE(SUM(amount),0) total FROM operations o WHERE o.status='PAYMENT_REJECTED'" + (" AND o.employee_id=?" if not manager else ""),( [emp['employee_id']] if not manager else [])).fetchone()
        actual_sql="""SELECT COALESCE(SUM(CASE WHEN upper(COALESCE(t.transaction_type,'')) NOT IN ('TRANSFER_OUT','PAYMENT_OUT') THEN COALESCE(t.amount,0) ELSE 0 END),0)
          FROM android_transactions t JOIN operations o ON o.operation_id=t.operation_id
          WHERE o.status='TICKET_READY' AND t.amount IS NOT NULL"""
        actual_args=() if manager else (emp['employee_id'],)
        if not manager:
            actual_sql += " AND o.employee_id=?"
        actual=con.execute(actual_sql,actual_args).fetchone()[0]
    return jsonify({'ok':True,'summary':{'successful_count':int(success[0]),'successful_total':float(success[1]),'pending_count':int(pending[0]),'pending_total':float(pending[1]),'rejected_count':int(rejected[0]),'rejected_total':float(rejected[1]),'actual_received_balance':float(actual)},'payments':ops})

@bp.get('/api/mobile/booking-operations')
def mobile_booking_operations():
    emp,e=_auth(mobile=True)
    if e:return e
    manager=_is_manager(emp)
    terminal={"TICKET_READY","PAYMENT_REJECTED"}
    with db() as con:
        where='' if manager else "WHERE (employee_id=? OR (payment_method='إنستا باي' AND EXISTS (SELECT 1 FROM payment_accounts pa WHERE pa.account_id=operations.account_id AND pa.shared_for_staff=1)))"
        params=[] if manager else [emp['employee_id']]
        rows=[dict(r) for r in con.execute(f"SELECT * FROM operations {where} ORDER BY updated_at DESC LIMIT 300",params).fetchall()]
        current=[o for o in rows if str(o.get('status') or '') not in terminal]
        completed=[o for o in rows if str(o.get('status') or '') in terminal]
        for group in (current,completed):
            for op in group:
                mm=con.execute("SELECT status,score,reason,checks_json,created_at FROM payment_matches WHERE operation_id=? ORDER BY created_at DESC LIMIT 1",(op['operation_id'],)).fetchone()
                op['match']=dict(mm) if mm else None
                if op.get('match'):
                    try: op['match']['checks']=json.loads(op['match'].get('checks_json') or '{}')
                    except Exception: op['match']['checks']={}
                br=con.execute("SELECT payment_reconciliation_json FROM booking_requests WHERE booking_id=?",(op['booking_id'],)).fetchone()
                op['reconciliation']=json.loads(br['payment_reconciliation_json'] or '{}') if br and br['payment_reconciliation_json'] else {}
                op['payment_vision']=(op.get('reconciliation') or {}).get('vision') or {}
    return jsonify({'ok':True,'current':current,'completed':completed,'counts':{'current':len(current),'completed':len(completed)}})

@bp.get('/api/mobile/operations/<operation_id>')
def mobile_operation(operation_id):
    emp,e=_auth(mobile=True)
    if e:return e
    op=operation_details(operation_id,emp['employee_id'],_is_manager(emp))
    if not op:return jsonify({'ok':False,'error':'OPERATION_NOT_FOUND'}),404
    return jsonify({'ok':True,'operation':op})

@bp.post('/api/mobile/operations/<operation_id>/approve')
def mobile_approve(operation_id): return _proxy_action(None,operation_id,'approve')

def _proxy_action(view,operation_id,kind):
    # Re-use the same rules while honoring bearer auth.
    emp,e=_auth(mobile=True)
    if e:return e
    op=operation_details(operation_id,emp['employee_id'],_is_manager(emp))
    if not op:return jsonify({'ok':False,'error':'OPERATION_NOT_FOUND'}),404
    if kind=='approve' and op.get('status')=='TICKET_READY': return jsonify({'ok':True,'status':'TICKET_READY','ticket_ready':True,'message':'العملية معتمدة بالفعل والتذكرة موجودة في «تذكرتي».'})
    if kind=='approve' and op.get('status')=='PAYMENT_REJECTED': return jsonify({'ok':False,'error':'OPERATION_ALREADY_REJECTED'}),409
    try:
        from integrated_operations import match_operation
        match_operation(operation_id)
        op=operation_details(operation_id,emp['employee_id'],_is_manager(emp)) or op
    except Exception: pass
    status=(op.get('matches') or [{}])[0].get('status') or 'UNMATCHED'
    data=request.get_json(silent=True) or {}
    import sqlite3,time
    if kind=='approve':
        if status in {'CONFLICT','SUSPICIOUS','UNMATCHED'} and not _is_manager(emp):return jsonify({'ok':False,'error':'MANAGER_REQUIRED','message':'هذه العملية تحتاج اعتماد المدير بسبب عدم اكتمال المطابقة.'}),403
        now=time.time()
        con=sqlite3.connect(BASE_DIR/'data'/'web_booking.db')
        try:
            con.execute("UPDATE operations SET status='PAYMENT_APPROVED',approved_at=?,approved_by=?,updated_at=? WHERE operation_id=?",(now,emp['employee_id'],now,operation_id))
            con.execute("UPDATE booking_requests SET status='PAYMENT_APPROVED',approved_by_staff_id=?,approved_at=?,note=? WHERE booking_id=?",(emp['employee_id'],now,'تم اعتماد الدفع وجاري إصدار التذكرة تلقائيًا داخل حساب العميل.',op['booking_id']))
            con.commit()
        finally: con.close()
        log_staff_action(operation_id,emp['employee_id'],'PAYMENT_APPROVED',{'match_status':status})
        try:
            from app import _attempt_final_booking
            result=_attempt_final_booking(op['booking_id'],emp['employee_id'])
        except Exception as ex:
            result={'ok':False,'error':f'FINALIZE_EXCEPTION:{type(ex).__name__}:{ex}'}
        if not result.get('ok'):
            return jsonify({'ok':False,'error':result.get('error') or 'FINAL_BOOKING_FAILED','finalize_details':result}),502
        try:
            with db() as con:
                con.execute("INSERT INTO chat_messages(booking_id,sender_type,sender_id,message,message_type,created_at) VALUES(?,?,?,?,?,?)",(op['booking_id'],'staff',str(emp['employee_id']),'تم اعتماد الدفع بنجاح ✅ وتم إصدار نموذج التذكرة. افتح «تذكرتي» لعرضها.','text',time.time()))
        except Exception: pass
        return jsonify({'ok':True,'status':result.get('status','TICKET_READY'),'ticket_ready':result.get('status')=='TICKET_READY','final_booking_ref':result.get('final_booking_ref',''),'delivery':result.get('delivery','WEB_READY'),'message':'تم تأكيد الدفع وإصدار نموذج التذكرة تلقائيًا داخل «تذكرتي».','payment_evidence':{'match_status':status,'vision':op.get('payment_vision') or {},'android':(op.get('android') or [])[:3]}})
    now=time.time(); reason=str(data.get('reason') or 'رفض الموظف لإثبات الدفع'); con=sqlite3.connect(BASE_DIR/'data'/'web_booking.db');con.execute("UPDATE operations SET status='PAYMENT_REJECTED',rejected_at=?,rejected_by=?,updated_at=? WHERE operation_id=?",(now,emp['employee_id'],now,operation_id));con.execute("UPDATE booking_requests SET status='PAYMENT_REJECTED',rejected_by_staff_id=?,rejected_at=?,note=? WHERE booking_id=?",(emp['employee_id'],now,reason,op['booking_id']));con.commit();con.close();log_staff_action(operation_id,emp['employee_id'],'PAYMENT_REJECTED',{'reason':reason});return jsonify({'ok':True,'status':'PAYMENT_REJECTED'})

@bp.post('/api/mobile/operations/<operation_id>/reanalyze-proof')
def mobile_reanalyze_proof(operation_id):
    emp,e=_auth(mobile=True)
    if e:return e
    op,result=_reconcile_operation_proof(operation_id,emp['employee_id'],_is_manager(emp))
    if not result.get('ok'):
        return jsonify(result),404 if result.get('error') in {'OPERATION_NOT_FOUND','PROOF_NOT_FOUND','PROOF_FILE_NOT_FOUND'} else 400
    return jsonify({'ok':True,'operation':op,'reconciliation':result.get('reconciliation') or {},'match':result.get('match') or {}})

@bp.post('/api/mobile/operations/<operation_id>/reject')
def mobile_reject(operation_id): return _proxy_action(None,operation_id,'reject')

@bp.post('/api/mobile/operations/<operation_id>/ticket')
def mobile_ticket(operation_id):
    emp,e=_auth(mobile=True)
    if e:return e
    # We duplicate the small upload body here because Flask endpoint auth source differs.
    op=operation_details(operation_id,emp['employee_id'],_is_manager(emp));
    if not op:return jsonify({'ok':False,'error':'OPERATION_NOT_FOUND'}),404
    if op.get('status')!='PAYMENT_APPROVED':return jsonify({'ok':False,'error':'PAYMENT_NOT_APPROVED'}),409
    files=request.files.getlist('ticket');
    if not files:return jsonify({'ok':False,'error':'TICKET_REQUIRED'}),400
    paths=[];urls=[]
    for f in files[:4]:
        ext=Path(secure_filename(f.filename)).suffix.lower()
        if ext not in {'.jpg','.jpeg','.png','.webp'}:return jsonify({'ok':False,'error':'INVALID_TICKET_TYPE'}),400
        path=TICKET_DIR/f"{operation_id}_{secrets.token_hex(7)}{ext}";f.save(path)
        try:
            with Image.open(path) as im: im.verify()
        except: path.unlink(missing_ok=True);return jsonify({'ok':False,'error':'INVALID_TICKET_IMAGE'}),400
        paths.append(str(path));urls.append(f"{PUBLIC_ORIGIN}/staff/media/{media_token(operation_id,str(path))}")
    from payment_workflow import send_customer_media_public
    sent=[send_customer_media_public(op['customer_phone'],u,f"تذكرة SuperJet — {op['booking_id']}") for u in urls]
    ok=all(bool(x.get('ok') or x.get('status') in {200,201}) for x in sent)
    import sqlite3,time,json as _json
    con=sqlite3.connect(BASE_DIR/'data'/'web_booking.db');now=time.time();final_ref=str(request.form.get('final_booking_ref') or '')
    con.execute("UPDATE operations SET status='TICKET_READY',final_booking_ref=?,ticket_delivery=?,updated_at=? WHERE operation_id=?",(final_ref,'SENT' if ok else 'FAILED',now,operation_id));con.execute("UPDATE booking_requests SET status='TICKET_READY',final_booking_ref=?,manual_ticket_path=?,manual_ticket_delivery=?,delivery_status=? WHERE booking_id=?",(final_ref,_json.dumps(paths),'SENT' if ok else 'FAILED','SENT' if ok else 'WEB_READY',op['booking_id']));con.commit();con.close();log_staff_action(operation_id,emp['employee_id'],'TICKET_UPLOADED',{'sent':sent,'final_booking_ref':final_ref});return jsonify({'ok':True,'status':'TICKET_READY','delivery':'SENT' if ok else 'FAILED','sent':sent})

@bp.post('/api/mobile/android-notification')
def mobile_android_notification():
    emp,e=_auth(mobile=True)
    if e:return e
    device_id=request.headers.get('X-SuperJet-Device-Id','').strip() or str((request.get_json(silent=True) or {}).get('device_id') or '').strip()
    if not device_id:return jsonify({'ok':False,'error':'DEVICE_ID_REQUIRED'}),400
    with db() as con:
        row=con.execute('SELECT employee_id,active FROM employee_devices WHERE device_id=?',(device_id,)).fetchone()
        if not row or not row['active'] or row['employee_id']!=emp['employee_id']:return jsonify({'ok':False,'error':'DEVICE_BINDING_INVALID'}),403
        con.execute('UPDATE employee_devices SET last_seen=? WHERE device_id=?',(__import__('time').time(),device_id))
    payload=request.get_json(silent=True) or {}
    try: out=record_android(payload,emp,device_id)
    except Exception as ex:return jsonify({'ok':False,'error':str(ex)}),400
    matched_operation=str(out.get('matched_operation') or '')
    match=out.get('match') or {}
    if matched_operation:
        import sqlite3,time,json as _json
        con=sqlite3.connect(BASE_DIR/'data'/'web_booking.db'); con.row_factory=sqlite3.Row
        try:
            row=con.execute('SELECT booking_id,payment_reconciliation_json FROM booking_requests WHERE booking_id=(SELECT booking_id FROM operations WHERE operation_id=?)',(matched_operation,)).fetchone()
            if row:
                try: rec=_json.loads(row['payment_reconciliation_json'] or '{}')
                except Exception: rec={}
                rec['integrated_match']=match
                rec['android']=match.get('transaction') or payload
                con.execute('UPDATE booking_requests SET status=?,payment_reconciliation_json=?,note=? WHERE booking_id=?',('PAYMENT_REVIEW',_json.dumps(rec,ensure_ascii=False),'وصل إشعار الدفع من هاتف الموظف وتم ربطه بالحجز؛ النتيجة تحت مراجعة الموظف.',row['booking_id']))
                con.commit()
        finally: con.close()
    # Best-effort shadow forwarding to the legacy Core for backward compatibility.
    try:
        import requests
        core=os.getenv('SUPERJET_WEB_CORE_URL','http://127.0.0.1:5013').rstrip('/')
        tok=os.getenv('SUPERJET_ANDROID_PAYMENT_TOKEN','').strip()
        if tok: requests.post(core+'/payment/android-notification',headers={'X-SuperJet-Android-Token':tok,'Content-Type':'application/json'},json=payload,timeout=5)
    except Exception: pass
    return jsonify({'ok':True,'result':out})


def register_staff_portal(app):
    init_db()
    app.register_blueprint(bp)