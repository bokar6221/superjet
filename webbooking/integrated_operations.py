# -*- coding: utf-8 -*-
"""Integrated SuperJet operations/employee/payment ledger.

Single source for employee accounts, payment accounts, device sessions,
payment evidence, matching decisions and audit events. It deliberately lives
inside the existing Web SQLite database so the booking workflow remains the
source of truth for real SuperJet bookings.
"""
from __future__ import annotations

import hashlib, hmac, json, os, re, secrets, sqlite3, time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "web_booking.db"
PUBLIC_ORIGIN_FALLBACK = "https://superjet.tail0f920c.ts.net:8443"
SESSION_TTL = 12 * 3600
DEVICE_TOKEN_TTL = 30 * 24 * 3600


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _norm_phone(v: Any) -> str:
    d = re.sub(r"\D", "", str(v or ""))
    if d.startswith("20") and len(d) >= 12: d = "0" + d[2:]
    return d


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 210000)
    return f"pbkdf2_sha256$210000${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, digest_hex = stored.split("$", 3)
        if algo != "pbkdf2_sha256": return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), digest_hex)
    except Exception:
        return False


def init_db() -> dict[str, str]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    bootstrap = {"username": "manager", "password": ""}
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS employees (
          employee_id TEXT PRIMARY KEY,
          username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          name TEXT NOT NULL,
          mobile TEXT DEFAULT '',
          whatsapp TEXT DEFAULT '',
          role TEXT NOT NULL DEFAULT 'customer_service',
          shift_name TEXT DEFAULT '',
          status TEXT NOT NULL DEFAULT 'OFF_SHIFT',
          active INTEGER NOT NULL DEFAULT 1,
          mobile_online INTEGER NOT NULL DEFAULT 0,
          mobile_last_seen REAL NOT NULL DEFAULT 0,
          mobile_device_id TEXT DEFAULT '',
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payment_accounts (
          account_id TEXT PRIMARY KEY,
          employee_id TEXT NOT NULL,
          method TEXT NOT NULL,
          label TEXT NOT NULL,
          pay_to TEXT NOT NULL,
          account_type TEXT DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1,
          priority INTEGER NOT NULL DEFAULT 0,
          shared_for_staff INTEGER NOT NULL DEFAULT 0,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL,
          FOREIGN KEY(employee_id) REFERENCES employees(employee_id)
        );
        CREATE INDEX IF NOT EXISTS idx_payment_accounts_method_active ON payment_accounts(method, active);
        CREATE TABLE IF NOT EXISTS shifts (
          shift_id TEXT PRIMARY KEY,
          name TEXT UNIQUE NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_shifts_active ON shifts(active, name);
        CREATE TABLE IF NOT EXISTS employee_devices (
          device_id TEXT PRIMARY KEY,
          employee_id TEXT NOT NULL,
          token_hash TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,
          first_seen REAL NOT NULL,
          last_seen REAL NOT NULL,
          FOREIGN KEY(employee_id) REFERENCES employees(employee_id)
        );
        CREATE INDEX IF NOT EXISTS idx_devices_employee ON employee_devices(employee_id);
        CREATE TABLE IF NOT EXISTS staff_sessions (
          token_hash TEXT PRIMARY KEY,
          employee_id TEXT NOT NULL,
          kind TEXT NOT NULL DEFAULT 'web',
          expires_at REAL NOT NULL,
          created_at REAL NOT NULL,
          FOREIGN KEY(employee_id) REFERENCES employees(employee_id)
        );
        CREATE INDEX IF NOT EXISTS idx_staff_sessions_employee ON staff_sessions(employee_id);
        CREATE TABLE IF NOT EXISTS operations (
          operation_id TEXT PRIMARY KEY,
          booking_id TEXT UNIQUE NOT NULL,
          employee_id TEXT DEFAULT '',
          account_id TEXT DEFAULT '',
          status TEXT NOT NULL DEFAULT 'PAYMENT_PENDING',
          customer_name TEXT DEFAULT '',
          customer_phone TEXT DEFAULT '',
          from_name TEXT DEFAULT '',
          to_name TEXT DEFAULT '',
          travel_date TEXT DEFAULT '',
          travel_time TEXT DEFAULT '',
          seats_json TEXT DEFAULT '[]',
          amount REAL DEFAULT 0,
          payment_method TEXT DEFAULT '',
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL,
          approved_at REAL DEFAULT 0,
          approved_by TEXT DEFAULT '',
          rejected_at REAL DEFAULT 0,
          rejected_by TEXT DEFAULT '',
          final_booking_ref TEXT DEFAULT '',
          ticket_delivery TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_ops_employee_status ON operations(employee_id, status, updated_at);
        CREATE TABLE IF NOT EXISTS payment_proofs (
          proof_id TEXT PRIMARY KEY,
          operation_id TEXT NOT NULL,
          path TEXT NOT NULL,
          vision_json TEXT DEFAULT '{}',
          uploaded_at REAL NOT NULL,
          FOREIGN KEY(operation_id) REFERENCES operations(operation_id)
        );
        CREATE TABLE IF NOT EXISTS android_transactions (
          event_id TEXT PRIMARY KEY,
          operation_id TEXT DEFAULT '',
          device_id TEXT DEFAULT '',
          employee_id TEXT DEFAULT '',
          provider TEXT DEFAULT '',
          transaction_type TEXT DEFAULT '',
          amount REAL DEFAULT NULL,
          reference TEXT DEFAULT '',
          sender_phone TEXT DEFAULT '',
          recipient_account TEXT DEFAULT '',
          transaction_date TEXT DEFAULT '',
          transaction_time TEXT DEFAULT '',
          received_at REAL NOT NULL,
          raw_json TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_android_received ON android_transactions(received_at DESC);
        CREATE TABLE IF NOT EXISTS payment_matches (
          match_id TEXT PRIMARY KEY,
          operation_id TEXT NOT NULL,
          event_id TEXT DEFAULT '',
          status TEXT NOT NULL,
          score REAL NOT NULL DEFAULT 0,
          checks_json TEXT DEFAULT '{}',
          reason TEXT DEFAULT '',
          created_at REAL NOT NULL,
          FOREIGN KEY(operation_id) REFERENCES operations(operation_id)
        );
        CREATE INDEX IF NOT EXISTS idx_matches_operation ON payment_matches(operation_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS staff_actions (
          action_id TEXT PRIMARY KEY,
          operation_id TEXT NOT NULL,
          employee_id TEXT NOT NULL,
          action TEXT NOT NULL,
          details_json TEXT DEFAULT '{}',
          created_at REAL NOT NULL,
          FOREIGN KEY(operation_id) REFERENCES operations(operation_id)
        );
        CREATE TABLE IF NOT EXISTS operation_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          operation_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          actor TEXT DEFAULT '',
          details_json TEXT DEFAULT '{}',
          created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_operation ON operation_events(operation_id, created_at);
        CREATE TABLE IF NOT EXISTS media_tokens (
          token TEXT PRIMARY KEY,
          operation_id TEXT NOT NULL,
          path TEXT NOT NULL,
          expires_at REAL NOT NULL,
          created_at REAL NOT NULL
        );
        """ )

        # V35 presence/shared-InstaPay migration for existing databases.
        employee_cols={row[1] for row in con.execute("PRAGMA table_info(employees)").fetchall()}
        for col,sql in {
            "mobile_online":"ALTER TABLE employees ADD COLUMN mobile_online INTEGER NOT NULL DEFAULT 0",
            "mobile_last_seen":"ALTER TABLE employees ADD COLUMN mobile_last_seen REAL NOT NULL DEFAULT 0",
            "mobile_device_id":"ALTER TABLE employees ADD COLUMN mobile_device_id TEXT DEFAULT ''",
        }.items():
            if col not in employee_cols: con.execute(sql)
        account_cols={row[1] for row in con.execute("PRAGMA table_info(payment_accounts)").fetchall()}
        if "shared_for_staff" not in account_cols: con.execute("ALTER TABLE payment_accounts ADD COLUMN shared_for_staff INTEGER NOT NULL DEFAULT 0")
        con.execute("UPDATE payment_accounts SET shared_for_staff=1 WHERE method='إنستا باي'")

        # One safe manager bootstrap. A random password is generated once and
        # returned to the packaging script; it is never hard-coded into source.
        row = con.execute("SELECT employee_id FROM employees WHERE username='manager'").fetchone()
        if not row:
            now = time.time()
            pw = str(os.getenv("SUPERJET_MANAGER_BOOTSTRAP_PASSWORD") or "").strip() or ("SJm-" + secrets.token_urlsafe(12))
            con.execute("INSERT INTO employees(employee_id,username,password_hash,name,role,status,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        ("MGR-001", "manager", _hash_password(pw), "مدير النظام", "manager", "ON_SHIFT", 1, now, now))
            bootstrap["password"] = pw
            # Import current legacy payment destinations when available.
            cfg = BASE_DIR.parent / "core" / "config.json"
            if cfg.is_file():
                try:
                    c = json.loads(cfg.read_text(encoding="utf-8"))
                    vf = str(c.get("vodafone_cash_phone") or c.get("payment_phone") or "").strip()
                    ip = str(c.get("instapay_link") or "").strip()
                    if vf:
                        con.execute("INSERT OR IGNORE INTO payment_accounts(account_id,employee_id,method,label,pay_to,account_type,active,priority,shared_for_staff,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                    ("ACC-MGR-VF", "MGR-001", "محفظة إلكترونية", "Vodafone Cash", vf, "VODAFONE_CASH", 1, 10, 0, now, now))
                    if ip:
                        con.execute("INSERT OR IGNORE INTO payment_accounts(account_id,employee_id,method,label,pay_to,account_type,active,priority,shared_for_staff,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                    ("ACC-MGR-IP", "MGR-001", "إنستا باي", "InstaPay", ip, "INSTAPAY", 1, 10, 1, now, now))
                except Exception:
                    pass
        else:
            # Manager already exists; do not expose or regenerate its password.
            bootstrap["password"] = "SEE_EXISTING_MANAGER_PASSWORD"
    return bootstrap


def seed_info() -> dict[str, str]:
    return init_db()


def create_session(employee_id: str, kind: str = "web", ttl: int = SESSION_TTL) -> str:
    raw = secrets.token_urlsafe(40)
    th = hashlib.sha256(raw.encode()).hexdigest()
    now = time.time()
    with db() as con:
        con.execute("INSERT INTO staff_sessions(token_hash,employee_id,kind,expires_at,created_at) VALUES(?,?,?,?,?)",
                    (th, employee_id, kind, now + ttl, now))
    return raw


def employee_from_token(token: str) -> dict[str, Any] | None:
    token = str(token or "").strip()
    if not token: return None
    th = hashlib.sha256(token.encode()).hexdigest()
    with db() as con:
        row = con.execute("""SELECT e.* FROM staff_sessions s JOIN employees e ON e.employee_id=s.employee_id
                             WHERE s.token_hash=? AND s.expires_at>? AND e.active=1""", (th, time.time())).fetchone()
    return dict(row) if row else None


def destroy_session(token: str) -> None:
    th = hashlib.sha256(str(token or "").encode()).hexdigest()
    with db() as con: con.execute("DELETE FROM staff_sessions WHERE token_hash=?", (th,))


def set_mobile_presence(employee_id: str, device_id: str, online: bool = True) -> None:
    eid=str(employee_id or '').strip(); did=str(device_id or '').strip()
    if not eid or not did: return
    now=time.time()
    with db() as con:
        con.execute("UPDATE employees SET mobile_online=?,mobile_last_seen=?,mobile_device_id=?,updated_at=? WHERE employee_id=? AND active=1",(1 if online else 0,now,did if online else '',now,eid))


def touch_mobile_presence(employee_id: str, device_id: str) -> None:
    set_mobile_presence(employee_id,device_id,True)


def is_mobile_online(employee_id: str, con=None, max_age: int = 90) -> bool:
    own=False
    if con is None: con=db(); own=True
    try:
        row=con.execute("SELECT active,mobile_online,mobile_last_seen FROM employees WHERE employee_id=?",(str(employee_id or ''),)).fetchone()
        return bool(row and int(row['active'] or 0) and int(row['mobile_online'] or 0) and float(row['mobile_last_seen'] or 0)>=time.time()-max_age)
    finally:
        if own: con.close()


def get_employee(employee_id: str) -> dict[str, Any] | None:
    with db() as con:
        row=con.execute("SELECT * FROM employees WHERE employee_id=?",(employee_id,)).fetchone()
    return dict(row) if row else None


def list_employees() -> list[dict[str, Any]]:
    with db() as con:
        rows=con.execute("SELECT employee_id,username,name,mobile,whatsapp,role,shift_name,status,active,mobile_online,mobile_last_seen,mobile_device_id,created_at,updated_at FROM employees ORDER BY role DESC,name").fetchall()
    return [dict(r) for r in rows]

def _seed_shifts() -> None:
    with db() as con:
        rows=con.execute("SELECT DISTINCT TRIM(shift_name) FROM employees WHERE TRIM(shift_name)<>''").fetchall()
        now=time.time()
        for row in rows:
            name=str(row[0] or '').strip()
            if not name: continue
            con.execute("INSERT OR IGNORE INTO shifts(shift_id,name,active,created_at,updated_at) VALUES(?,?,?,?,?)",
                        ("SH-"+hashlib.sha1(name.encode('utf-8')).hexdigest()[:10].upper(),name,1,now,now))


def list_shifts(active_only: bool=False) -> list[dict[str,Any]]:
    _seed_shifts()
    q="SELECT * FROM shifts"
    if active_only: q += " WHERE active=1"
    q += " ORDER BY active DESC,name"
    with db() as con: rows=con.execute(q).fetchall()
    return [dict(r) for r in rows]


def add_shift(name: str) -> dict[str,Any]:
    name=re.sub(r"\s+"," ",str(name or '').strip())
    if len(name)<2: raise ValueError("اسم الشفت غير صالح")
    now=time.time(); sid="SH-"+secrets.token_hex(5).upper()
    with db() as con:
        con.execute("INSERT INTO shifts(shift_id,name,active,created_at,updated_at) VALUES(?,?,?,?,?)",(sid,name,1,now,now))
        return dict(con.execute("SELECT * FROM shifts WHERE shift_id=?",(sid,)).fetchone())


def update_shift(shift_id: str, name: str='', active: int|None=None) -> dict[str,Any]|None:
    name=re.sub(r"\s+"," ",str(name or '').strip()) if name else ''
    fields=[]; vals=[]
    if name: fields.append('name=?'); vals.append(name)
    if active is not None: fields.append('active=?'); vals.append(int(bool(active)))
    if not fields: return None
    fields.append('updated_at=?'); vals.append(time.time()); vals.append(shift_id)
    with db() as con:
        con.execute('UPDATE shifts SET '+','.join(fields)+' WHERE shift_id=?',vals)
        row=con.execute('SELECT * FROM shifts WHERE shift_id=?',(shift_id,)).fetchone()
    return dict(row) if row else None


def delete_shift(shift_id: str) -> dict[str,Any]:
    with db() as con:
        row=con.execute('SELECT name FROM shifts WHERE shift_id=?',(shift_id,)).fetchone()
        if not row: raise ValueError('الشفت غير موجود')
        name=str(row[0])
        assigned=con.execute('SELECT COUNT(*) FROM employees WHERE shift_name=? AND active=1',(name,)).fetchone()[0]
        if assigned: raise ValueError('لا يمكن حذف الشفت لأنه مستخدم بواسطة موظفين نشطين. غيّر الشفت أولًا.')
        con.execute('DELETE FROM shifts WHERE shift_id=?',(shift_id,))
    return {'ok':True,'name':name}


def replace_shift_name(old_name: str, new_name: str) -> None:
    new_name=re.sub(r"\s+"," ",str(new_name or '').strip())
    if len(new_name)<2: raise ValueError('اسم الشفت غير صالح')
    with db() as con:
        con.execute('UPDATE employees SET shift_name=?,updated_at=? WHERE shift_name=?',(new_name,time.time(),str(old_name or '').strip()))


def create_employee(name: str, username: str, password: str, mobile: str = "", whatsapp: str = "", role: str = "customer_service", shift_name: str = "") -> tuple[dict[str,Any], str]:
    name=re.sub(r"\s+"," ",str(name or "").strip()); username=str(username or "").strip().lower(); password=str(password or ""); shift_name=re.sub(r"\s+"," ",str(shift_name or "").strip())
    if not name or not username or len(password)<8: raise ValueError("بيانات الموظف أو كلمة المرور غير صالحة")
    if shift_name:
        if not any(str(x.get("name") or "") == shift_name and int(x.get("active",0)) for x in list_shifts(active_only=True)):
            raise ValueError("الشفت المختار غير موجود أو غير فعال")
    eid="EMP-"+secrets.token_hex(5).upper(); now=time.time()
    with db() as con:
        con.execute("INSERT INTO employees(employee_id,username,password_hash,name,mobile,whatsapp,role,shift_name,status,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (eid,username,_hash_password(password),name,_norm_phone(mobile),_norm_phone(whatsapp),role if role in {"customer_service","manager"} else "customer_service",shift_name,"OFF_SHIFT",1,now,now))
    return get_employee(eid) or {}, eid


def update_employee(employee_id: str, **fields: Any) -> None:
    allowed={"name","mobile","whatsapp","role","shift_name","status","active","username"}
    fields={k:v for k,v in fields.items() if k in allowed}
    if "shift_name" in fields and str(fields.get("shift_name") or "").strip():
        shift_name=re.sub(r"\s+"," ",str(fields.get("shift_name") or "").strip())
        if not any(str(x.get("name") or "") == shift_name and int(x.get("active",0)) for x in list_shifts(active_only=True)):
            raise ValueError("الشفت المختار غير موجود أو غير فعال")
        fields["shift_name"]=shift_name
    if "status" in fields and str(fields.get("status") or "") not in {"ON_SHIFT","OFF_SHIFT","BREAK"}:
        raise ValueError("حالة الموظف غير صالحة")
    if "shift_name" in fields and str(fields.get("shift_name") or "").strip():
        shift_name=re.sub(r"\s+"," ",str(fields.get("shift_name") or "").strip())
        if not any(str(x.get("name") or "") == shift_name and int(x.get("active",0)) for x in list_shifts(active_only=True)):
            raise ValueError("الشفت المختار غير موجود أو غير فعال")
        fields["shift_name"]=shift_name
    if "status" in fields and str(fields.get("status") or "") not in {"ON_SHIFT","OFF_SHIFT","BREAK"}:
        raise ValueError("حالة الموظف غير صالحة")
    if "mobile" in fields: fields["mobile"]=_norm_phone(fields["mobile"])
    if "whatsapp" in fields: fields["whatsapp"]=_norm_phone(fields["whatsapp"])
    fields["updated_at"]=time.time()
    if not fields: return
    sets=",".join(f"{k}=?" for k in fields); vals=list(fields.values())+[employee_id]
    with db() as con: con.execute(f"UPDATE employees SET {sets} WHERE employee_id=?",vals)


def set_password(employee_id: str, password: str) -> None:
    if len(str(password or ""))<8: raise ValueError("كلمة المرور لا تقل عن 8 أحرف")
    with db() as con: con.execute("UPDATE employees SET password_hash=?,updated_at=? WHERE employee_id=?",(_hash_password(password),time.time(),employee_id))


def add_payment_account(employee_id: str, method: str, label: str, pay_to: str, account_type: str = "", priority: int = 0, shared_for_staff: bool = False) -> dict[str,Any]:
    employee_id = str(employee_id or "").strip()
    method = str(method or "").strip()
    pay_to = str(pay_to or "").strip()
    if method not in {"إنستا باي","محفظة إلكترونية"}:
        raise ValueError("طريقة دفع غير مدعومة")
    if not employee_id:
        raise ValueError("يجب اختيار الموظف صاحب الحساب")
    if not pay_to:
        raise ValueError("بيانات المحفظة أو InstaPay مطلوبة")
    aid="ACC-"+secrets.token_hex(5).upper(); now=time.time()
    with db() as con:
        employee = con.execute("SELECT employee_id,active FROM employees WHERE employee_id=?", (employee_id,)).fetchone()
        if not employee:
            raise ValueError(f"كود الموظف غير موجود: {employee_id}")
        if not int(employee["active"]):
            raise ValueError("لا يمكن إضافة حساب لموظف غير نشط")
        shared=bool(shared_for_staff or method=="إنستا باي")
        if method=="إنستا باي" and shared:
            con.execute("UPDATE payment_accounts SET active=0,updated_at=? WHERE method='إنستا باي' AND active=1",(now,))
        con.execute("INSERT INTO payment_accounts(account_id,employee_id,method,label,pay_to,account_type,active,priority,shared_for_staff,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (aid,employee_id,method,label or method,pay_to,account_type,1,int(priority),1 if shared else 0,now,now))
        row=con.execute("SELECT * FROM payment_accounts WHERE account_id=?",(aid,)).fetchone()
    return dict(row)


def list_payment_accounts(active_only: bool = False) -> list[dict[str,Any]]:
    q="SELECT a.*,e.name employee_name,e.status employee_status,e.whatsapp employee_whatsapp FROM payment_accounts a JOIN employees e ON e.employee_id=a.employee_id"
    if active_only: q += " WHERE a.active=1 AND e.active=1"
    q += " ORDER BY a.method,a.priority DESC,e.name"
    with db() as con: rows=con.execute(q).fetchall()
    return [dict(r) for r in rows]


def _online_employee_candidates(con: sqlite3.Connection, method: str) -> list[dict[str,Any]]:
    now=time.time(); method=str(method or '').strip()
    if method=="إنستا باي":
        rows=con.execute("""SELECT e.employee_id,e.name,e.mobile,e.whatsapp,e.role,e.status,e.mobile_last_seen,
                           COALESCE((SELECT COUNT(*) FROM operations o WHERE o.employee_id=e.employee_id
                                     AND o.status IN ('PAYMENT_PENDING','PROOF_RECEIVED','PAYMENT_REVIEW','PAYMENT_APPROVED')),0) open_ops
                           FROM employees e WHERE e.active=1 AND e.mobile_online=1 AND e.mobile_last_seen>=?
                           ORDER BY open_ops ASC,e.mobile_last_seen DESC,e.name ASC""",(now-90,)).fetchall()
    else:
        rows=con.execute("""SELECT DISTINCT e.employee_id,e.name,e.mobile,e.whatsapp,e.role,e.status,e.mobile_last_seen,
                           COALESCE((SELECT COUNT(*) FROM operations o WHERE o.employee_id=e.employee_id
                                     AND o.status IN ('PAYMENT_PENDING','PROOF_RECEIVED','PAYMENT_REVIEW','PAYMENT_APPROVED')),0) open_ops
                           FROM payment_accounts a JOIN employees e ON e.employee_id=a.employee_id
                           WHERE a.active=1 AND a.method=? AND a.shared_for_staff=0 AND e.active=1
                             AND e.mobile_online=1 AND e.mobile_last_seen>=?
                           ORDER BY open_ops ASC,a.priority DESC,e.mobile_last_seen DESC,e.name ASC""",(method,now-90)).fetchall()
    return [dict(r) for r in rows]


def payment_method_available(method: str) -> bool:
    method=str(method or '').strip()
    with db() as con:
        if method=="إنستا باي":
            acc=con.execute("SELECT account_id FROM payment_accounts WHERE method='إنستا باي' AND active=1 AND shared_for_staff=1 ORDER BY priority DESC,updated_at DESC LIMIT 1").fetchone()
            return bool(acc and _online_employee_candidates(con,method))
        return bool(_online_employee_candidates(con,method))


def select_payment_account(method: str, booking_id: str) -> dict[str,Any] | None:
    method=str(method or '').strip()
    if method not in {"إنستا باي","محفظة إلكترونية"}: return None
    with db() as con:
        staff=_online_employee_candidates(con,method)
        if not staff:return None
        staff=staff[0]
        if method=="إنستا باي":
            row=con.execute("SELECT * FROM payment_accounts WHERE method='إنستا باي' AND active=1 AND shared_for_staff=1 ORDER BY priority DESC,updated_at DESC LIMIT 1").fetchone()
        else:
            row=con.execute("SELECT * FROM payment_accounts WHERE method='محفظة إلكترونية' AND active=1 AND shared_for_staff=0 AND employee_id=? ORDER BY priority DESC,updated_at DESC LIMIT 1",(staff['employee_id'],)).fetchone()
        if not row:return None
        r=dict(row)
    return {"id":r['account_id'],"label":r['label'],"method":r['method'],"pay_to":r['pay_to'],"note":"حساب الدفع المخصص لهذا الطلب.",
            "employee_id":staff['employee_id'],"employee_name":staff['name'],"whatsapp":_norm_phone(staff.get('whatsapp') or staff.get('mobile')),
            "staff_online":True,"shared_for_staff":bool(r.get('shared_for_staff'))}


def support_contact(account_id: str, employee_id: str = "") -> dict[str,Any]:
    with db() as con:
        row=con.execute("""SELECT a.account_id,a.shared_for_staff,e.employee_id,e.name,e.whatsapp,e.mobile,e.status,e.mobile_online,e.mobile_last_seen
                           FROM payment_accounts a JOIN employees e ON e.employee_id=a.employee_id WHERE a.account_id=?""",(account_id,)).fetchone()
        if not row:return {"available":False,"staff_id":"","staff_name":"","whatsapp":"","whatsapp_digits":"","online":False}
        target_id=str(employee_id or '').strip() if int(row['shared_for_staff'] or 0) else str(row['employee_id'] or '')
        target=con.execute("SELECT employee_id,name,whatsapp,mobile,status,mobile_online,mobile_last_seen FROM employees WHERE employee_id=? AND active=1",(target_id,)).fetchone() if target_id else None
        r=target or row
        w=_norm_phone(r['whatsapp'] or r['mobile']); online=bool(int(r['mobile_online'] or 0) and float(r['mobile_last_seen'] or 0)>=time.time()-90)
    return {"available":bool(w),"staff_id":str(r['employee_id'] or ''),"staff_name":str(r['name'] or ''),"whatsapp":w,"whatsapp_digits":w,"status":str(r['status'] or ''),"online":online,"app_connected":online,"shared_for_staff":bool(row['shared_for_staff'])}


def upsert_operation_from_booking(booking: dict[str,Any]) -> dict[str,Any]:
    bid=str(booking.get("booking_id") or "").upper(); now=time.time(); opid="OP-"+bid.replace("-","")
    trip=booking.get("trip") or {}; account_id=str(booking.get("payment_account_id") or ""); assigned=str(booking.get("assigned_employee_id") or "").strip(); created_new=False
    with db() as con:
        existing=con.execute("SELECT * FROM operations WHERE booking_id=?",(bid,)).fetchone()
        if existing:
            opid=str(existing['operation_id']); assigned=assigned or str(existing['employee_id'] or '')
        else:
            created_new=True
            if not assigned and account_id:
                rr=con.execute("SELECT employee_id FROM payment_accounts WHERE account_id=?",(account_id,)).fetchone(); assigned=str(rr[0]) if rr else ''
        con.execute("""INSERT INTO operations(operation_id,booking_id,employee_id,account_id,status,customer_name,customer_phone,from_name,to_name,travel_date,travel_time,seats_json,amount,payment_method,created_at,updated_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(booking_id) DO UPDATE SET employee_id=CASE WHEN excluded.employee_id<>'' THEN excluded.employee_id ELSE operations.employee_id END,account_id=excluded.account_id,status=excluded.status,
                      customer_name=excluded.customer_name,customer_phone=excluded.customer_phone,from_name=excluded.from_name,to_name=excluded.to_name,travel_date=excluded.travel_date,travel_time=excluded.travel_time,
                      seats_json=excluded.seats_json,amount=excluded.amount,payment_method=excluded.payment_method,updated_at=excluded.updated_at""",
                    (opid,bid,assigned,account_id,str(booking.get("status") or "PAYMENT_PENDING"),str(booking.get("passenger_name") or ""),_norm_phone(booking.get("passenger_phone")),str(trip.get("from") or ""),str(trip.get("to") or ""),str(trip.get("date") or ""),str(trip.get("time") or ""),json.dumps(booking.get("seats") or []),float(booking.get("amount") or 0),str(booking.get("payment_method") or ""),float(booking.get("created_at") or now),now))
        row=con.execute("SELECT * FROM operations WHERE booking_id=?",(bid,)).fetchone()
    out=dict(row)
    if created_new and out.get('employee_id'):
        log_event(out['operation_id'],'BOOKING_ASSIGNED','system',{'employee_id':out['employee_id'],'payment_method':out.get('payment_method',''),'account_id':out.get('account_id','')})
    return out


def log_event(operation_id: str, event_type: str, actor: str = "", details: dict[str,Any]|None=None) -> None:
    with db() as con: con.execute("INSERT INTO operation_events(operation_id,event_type,actor,details_json,created_at) VALUES(?,?,?,?,?)",(operation_id,event_type,actor,json.dumps(details or {},ensure_ascii=False),time.time()))


def record_proof(booking: dict[str,Any], path: str, reconciliation: dict[str,Any] | None) -> dict[str,Any]:
    op=upsert_operation_from_booking(booking); oid=op["operation_id"]; pid="PROOF-"+secrets.token_hex(7).upper(); now=time.time()
    with db() as con:
        con.execute("INSERT INTO payment_proofs(proof_id,operation_id,path,vision_json,uploaded_at) VALUES(?,?,?,?,?)",(pid,oid,str(path),json.dumps((reconciliation or {}).get("vision") or {},ensure_ascii=False),now))
        con.execute("UPDATE operations SET status='PROOF_RECEIVED',updated_at=? WHERE operation_id=? AND status NOT IN ('PAYMENT_APPROVED','TICKET_READY','PAYMENT_REJECTED')",(now,oid))
    log_event(oid,"PROOF_RECEIVED","customer",{"path":str(path)})
    return operation_details(oid, str(op.get("employee_id") or ""), True) or op


def _same_amount(a:Any,b:Any)->bool:
    try:return a is not None and b is not None and abs(float(a)-float(b))<0.01
    except:return False


def _same_phone(a:Any,b:Any)->bool:
    aa,bb=_norm_phone(a),_norm_phone(b); return bool(aa and bb and aa==bb)


def _latest_proof(con: sqlite3.Connection, opid: str) -> dict[str,Any]:
    row=con.execute("SELECT vision_json,path FROM payment_proofs WHERE operation_id=? ORDER BY uploaded_at DESC LIMIT 1",(opid,)).fetchone()
    if not row:return {}
    try:v=json.loads(row[0] or "{}")
    except:v={}
    v["_path"]=row[1]; return v


def _operation_account(con, op) -> str:
    row=con.execute("SELECT pay_to FROM payment_accounts WHERE account_id=?",(op["account_id"],)).fetchone(); return str(row[0]) if row else ""


def match_operation(operation_id: str) -> dict[str,Any]:
    with db() as con:
        op=con.execute("SELECT * FROM operations WHERE operation_id=?",(operation_id,)).fetchone()
        if not op:return {"status":"UNMATCHED","score":0,"reason":"العملية غير موجودة"}
        proof=_latest_proof(con,operation_id)
        account=_operation_account(con,op)
        account_row=con.execute("SELECT account_type,method,shared_for_staff,employee_id,pay_to,label FROM payment_accounts WHERE account_id=?",(op["account_id"],)).fetchone() if op["account_id"] else None
        op_created=float(op["created_at"] or 0); now=time.time()
        rows=con.execute("SELECT * FROM android_transactions WHERE received_at>=? AND upper(COALESCE(provider,'')) IN ('VODAFONE_CASH','ORANGE_CASH','ETISALAT_CASH','WE_PAY','INSTAPAY') ORDER BY received_at DESC",(max(0,now-240*60),)).fetchall()
    best=None; best_score=-1.0; best_checks={}
    proof_amount=proof.get("amount") or proof.get("transaction_amount")
    proof_ref=str(proof.get("reference") or proof.get("transaction_id") or proof.get("transaction_reference") or "").strip()
    proof_sender=str(proof.get("sender_phone") or proof.get("sender") or proof.get("from_phone") or proof.get("from") or "").strip()
    proof_rec=str(proof.get("recipient_phone") or proof.get("receiver") or proof.get("recipient") or "").strip()
    acc_type=str(account_row[0] if account_row else "").upper(); acc_method=str(account_row[1] if account_row else ""); account_shared=bool(account_row and int(account_row[2] or 0))
    expected_provider={"VODAFONE_CASH":"VODAFONE_CASH","ORANGE_CASH":"ORANGE_CASH","ETISALAT_CASH":"ETISALAT_CASH","WE_PAY":"WE_PAY","INSTAPAY":"INSTAPAY"}
    for tx in rows:
        if (not account_shared) and op["employee_id"] and tx["employee_id"] and str(op["employee_id"]) != str(tx["employee_id"]):
            continue
        checks={}; score=0.0
        checks["booking_amount"]=_same_amount(op["amount"],tx["amount"]); score += 35 if checks["booking_amount"] else 0
        checks["proof_amount"]=_same_amount(proof_amount,tx["amount"]); score += 15 if checks["proof_amount"] else 0
        tx_rec=str(tx["recipient_account"] or "").strip()
        target_exact=bool(account and ((_same_phone(account,tx_rec)) or str(account).strip().lower()==tx_rec.lower()))
        proof_target=bool(account and proof_rec and ((_same_phone(account,proof_rec)) or str(account).strip().lower()==proof_rec.lower()))
        provider=str(tx["provider"] or "").upper()
        provider_method_ok=(provider==expected_provider.get(acc_type) or (acc_method=="محفظة إلكترونية" and provider in {"VODAFONE_CASH","ORANGE_CASH","ETISALAT_CASH","WE_PAY"}) or (acc_method=="إنستا باي" and provider=="INSTAPAY"))
        # If the notification omitted the recipient number, a verified employee-device + provider/account-type match is a safe inference.
        inferred=(not target_exact and not proof_target and provider_method_ok and (account_shared or (bool(op["employee_id"]) and str(tx["employee_id"] or "")==str(op["employee_id"]))))
        checks["target_account_exact"]=target_exact or proof_target
        checks["target_account_inferred"]=inferred
        checks["shared_account"]=account_shared
        checks["target_account"]=checks["target_account_exact"] or checks["target_account_inferred"]
        score += 20 if checks["target_account_exact"] else (14 if inferred else 0)
        checks["reference"]=bool(proof_ref and tx["reference"] and proof_ref==str(tx["reference"]).strip()); score += 12 if checks["reference"] else 0
        checks["sender_phone"]=_same_phone(proof_sender,tx["sender_phone"]); score += 8 if checks["sender_phone"] else 0
        checks["provider_present"]=bool(provider); score += 2 if checks["provider_present"] else 0
        try:
            tx_ts=float(tx["received_at"] or 0); age=abs(tx_ts-op_created) if op_created else 0
            checks["time_window"]=age <= 240*60 and tx_ts >= op_created-15*60
            score += 8 if age <= 10*60 else 6 if age <= 30*60 else 4 if age <= 120*60 else 2 if age <= 240*60 else 0
        except Exception: checks["time_window"]=True
        if str(tx["transaction_type"]).upper() in {"TRANSFER_OUT","PAYMENT_OUT"}: score-=20; checks["transfer_direction_ok"]=False
        else: checks["transfer_direction_ok"]=True
        if score>best_score: best,best_score,best_checks=tx,score,checks
    if not best:return {"status":"UNMATCHED","score":0,"reason":"لا يوجد إشعار تحويل حديث مرتبط بحساب الموظف.","checks":{},"transaction":{}}
    mandatory=bool(best_checks.get("booking_amount")) and bool(best_checks.get("target_account")) and bool(best_checks.get("time_window")) and bool(best_checks.get("transfer_direction_ok"))
    if best_score>=85 and mandatory: status="MATCHED"; reason="تطابق قوي متعدد الأدلة: المبلغ والحساب والوقت مع بيانات التحويل."
    elif best_score>=65 and mandatory: status="PARTIAL_MATCH"; reason="مطابقة جيدة؛ بعض الأدلة الاختيارية غير متاحة."
    elif best_checks.get("booking_amount") and not best_checks.get("target_account"): status="SUSPICIOUS"; reason="المبلغ مطابق لكن حساب الاستلام لم يُثبت بعد."
    elif best_checks.get("target_account") and not best_checks.get("booking_amount"): status="CONFLICT"; reason="الحساب مطابق لكن قيمة التحويل تختلف عن مبلغ الحجز."
    else: status="UNMATCHED"; reason="لم يثبت ارتباط التحويل بالحجز بشكل كافٍ."
    txd=dict(best); txd.pop("raw_json",None)
    with db() as con:
        con.execute("INSERT INTO payment_matches(match_id,operation_id,event_id,status,score,checks_json,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",("MATCH-"+secrets.token_hex(6).upper(),operation_id,txd.get("event_id") or "",status,float(best_score),json.dumps(best_checks,ensure_ascii=False),reason,time.time()))
    return {"status":status,"score":round(best_score,1),"reason":reason,"checks":best_checks,"transaction":txd}


def search_operations(query: str, employee_id: str | None = None, manager: bool = False, limit: int = 30) -> list[dict[str,Any]]:
    q=str(query or "").strip(); lim=max(1,min(int(limit or 30),100))
    if not q:return []
    qphone=_norm_phone(q); like=f"%{q}%"
    with db() as con:
        sql="SELECT * FROM operations WHERE 1=1"; params=[]
        if not manager:
            sql += " AND employee_id=?"; params.append(employee_id or "")
        sql += " AND (operation_id LIKE ? OR booking_id LIKE ? OR customer_name LIKE ? OR customer_phone LIKE ? OR payment_method LIKE ? OR account_id LIKE ? OR CAST(amount AS TEXT) LIKE ? OR EXISTS (SELECT 1 FROM payment_accounts pa WHERE pa.account_id=operations.account_id AND (pa.pay_to LIKE ? OR pa.label LIKE ?)) OR EXISTS (SELECT 1 FROM android_transactions at WHERE at.operation_id=operations.operation_id AND (at.reference LIKE ? OR at.sender_phone LIKE ? OR at.recipient_account LIKE ?)) OR EXISTS (SELECT 1 FROM payment_proofs pp WHERE pp.operation_id=operations.operation_id AND pp.vision_json LIKE ?))"
        params += [like,like,like,("%"+qphone+"%" if qphone else like),like,like,like,like,like,like,like,like,like]
        sql += " ORDER BY updated_at DESC LIMIT ?"; params.append(lim)
        rows=con.execute(sql,params).fetchall()
        out=[]
        for row in rows:
            op=dict(row); proof=_latest_proof(con,row["operation_id"]); txs=con.execute("SELECT provider,amount,reference,sender_phone,recipient_account,received_at FROM android_transactions WHERE operation_id=? ORDER BY received_at DESC LIMIT 5",(row["operation_id"],)).fetchall(); match=con.execute("SELECT status,score,reason,checks_json,created_at FROM payment_matches WHERE operation_id=? ORDER BY created_at DESC LIMIT 1",(row["operation_id"],)).fetchone()
            op["proof"]=proof; op["android"]=[dict(t) for t in txs]; op["match"]=dict(match) if match else None; out.append(op)
    return out

def record_android(payload: dict[str,Any], employee: dict[str,Any], device_id: str) -> dict[str,Any]:
    eid=str(payload.get("event_id") or "").strip()
    if not eid: raise ValueError("event_id مطلوب")
    now=time.time()
    try: amount=float(payload.get("amount")) if payload.get("amount") not in (None,"") else None
    except: amount=None
    received_at=payload.get("received_at")
    # Android sends ISO timestamp; normalize to epoch, fall back to now.
    ts=now
    if isinstance(received_at,(int,float)): ts=float(received_at)
    else:
        try: from datetime import datetime; ts=datetime.fromisoformat(str(received_at).replace("Z","+00:00")).timestamp()
        except: pass
    with db() as con:
        con.execute("""INSERT OR IGNORE INTO android_transactions(event_id,device_id,employee_id,provider,transaction_type,amount,reference,sender_phone,recipient_account,transaction_date,transaction_time,received_at,raw_json)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(eid,device_id,employee["employee_id"],str(payload.get("provider") or ""),str(payload.get("transaction_type") or ""),amount,str(payload.get("reference") or ""),_norm_phone(payload.get("sender_phone")),str(payload.get("recipient_phone") or payload.get("recipient_account") or "").strip(),str(payload.get("transaction_date") or ""),str(payload.get("transaction_time") or ""),ts,json.dumps(payload,ensure_ascii=False)))
    # Find candidate open operation(s) based on amount + account + time.
    candidates=[]
    with db() as con:
        rows=con.execute("SELECT * FROM operations WHERE status IN ('PAYMENT_PENDING','PROOF_RECEIVED','PAYMENT_REVIEW','PAYMENT_REJECTED') ORDER BY updated_at DESC LIMIT 200").fetchall()
        for op in rows:
            if _same_amount(op["amount"], amount): candidates.append(dict(op))
    with db() as con:
        filtered=[]
        for c in candidates:
            ar=con.execute("SELECT shared_for_staff FROM payment_accounts WHERE account_id=?",(c.get("account_id") or "",)).fetchone()
            shared=bool(ar and int(ar[0] or 0))
            if not shared and c.get("employee_id") and c.get("employee_id")!=employee["employee_id"]: continue
            c["_shared_account"]=shared; filtered.append(c)
        candidates=filtered
    best_op=None; best_score=-1
    with db() as con:
        for op in candidates:
            account=_operation_account(con,op); score=0
            if _same_amount(op["amount"],amount): score+=50
            rec=str(payload.get("recipient_phone") or payload.get("recipient_account") or "").strip()
            if account and (_same_phone(account,rec) or str(account).strip().lower()==rec.lower()): score+=30
            if str(payload.get("reference") or "").strip(): score+=10
            if str(op.get("employee_id") or "")==employee["employee_id"]: score+=10
            if op.get("_shared_account"): score+=5
            if score>best_score: best_score,best_op=score,op
        if best_op:
            con.execute("UPDATE android_transactions SET operation_id=? WHERE event_id=?",(best_op["operation_id"],eid))
    match=None
    if best_op:
        match=match_operation(best_op["operation_id"])
        if match:
            with db() as con:
                con.execute("UPDATE operations SET status=?,updated_at=? WHERE operation_id=?",("PAYMENT_REVIEW",time.time(),best_op["operation_id"]))
            log_event(best_op["operation_id"],"ANDROID_PAYMENT_RECEIVED",employee["employee_id"],{"event_id":eid,"match_status":match.get("status",""),"score":match.get("score",0)})
    return {"event_id":eid,"matched_operation":best_op["operation_id"] if best_op else "","match":match or {"status":"UNMATCHED","score":0}}


def dashboard(employee_id: str, manager: bool = False) -> dict[str,Any]:
    from datetime import datetime
    now=time.time()
    local_now=datetime.now()
    day_start=local_now.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    month_start=local_now.replace(day=1,hour=0,minute=0,second=0,microsecond=0).timestamp()
    with db() as con:
        base="SELECT * FROM operations"; args=[]
        if not manager: base += " WHERE employee_id=?"; args=[employee_id]
        base += " ORDER BY updated_at DESC LIMIT 200"
        ops=[dict(r) for r in con.execute(base,args).fetchall()]
        for op in ops:
            mm=con.execute("SELECT status,score,checks_json,reason FROM payment_matches WHERE operation_id=? ORDER BY created_at DESC LIMIT 1",(op["operation_id"],)).fetchone()
            if mm:
                op["match_status"]=mm[0]; op["match_score"]=float(mm[1] or 0); op["match_reason"]=mm[3] or ""
                try: op["match_checks"]=json.loads(mm[2] or "{}")
                except Exception: op["match_checks"]={}
            else:
                op["match_status"]="UNMATCHED"; op["match_score"]=0; op["match_reason"]="في انتظار إشعار دفع مطابق"; op["match_checks"]={}

        def totals_since(since: float | None):
            # Financial totals contain successful/ticket-ready operations only.
            q="SELECT COUNT(*) c, COALESCE(SUM(amount),0) total FROM operations"; p=[]; clauses=["status='TICKET_READY'"]
            if since is not None: clauses.append("updated_at>=?"); p.append(since)
            if not manager: clauses.append("employee_id=?"); p.append(employee_id)
            q += " WHERE " + " AND ".join(clauses)
            r=con.execute(q,p).fetchone(); return {"operations":int(r[0]),"total":float(r[1])}

        def by_method(since: float | None):
            q="SELECT payment_method,COUNT(*) c,COALESCE(SUM(amount),0) total FROM operations"; p=[]; clauses=["status='TICKET_READY'"]
            if since is not None: clauses.append("updated_at>=?"); p.append(since)
            if not manager: clauses.append("employee_id=?"); p.append(employee_id)
            q += " WHERE " + " AND ".join(clauses) + " GROUP BY payment_method"
            return [dict(r) for r in con.execute(q,p).fetchall()]

        # Financial balance is cash actually accepted by SuperJet only.
        # Pending/rejected Android notifications must never increase the balance.
        balance_q="""SELECT COALESCE(SUM(CASE
            WHEN upper(COALESCE(t.transaction_type,'')) IN ('TRANSFER_OUT','PAYMENT_OUT') THEN 0
            ELSE COALESCE(t.amount,0) END),0)
            FROM android_transactions t
            JOIN operations o ON o.operation_id=t.operation_id
            WHERE o.status='TICKET_READY' AND t.amount IS NOT NULL"""
        balance_args=[]
        if not manager:
            balance_q += " AND o.employee_id=?"; balance_args=[employee_id]
        actual_received_balance=float(con.execute(balance_q,balance_args).fetchone()[0] or 0)

        statuses={}
        q="SELECT status,COUNT(*) c FROM operations"; p=[]; clauses=[]
        if not manager: clauses.append("employee_id=?"); p.append(employee_id)
        if clauses:q += " WHERE " + " AND ".join(clauses)
        q += " GROUP BY status"
        for r in con.execute(q,p).fetchall(): statuses[r[0]]=int(r[1])

    return {
        "today": totals_since(day_start),
        "month": totals_since(month_start),
        "all_time": totals_since(None),
        "by_method": by_method(day_start),
        "by_method_month": by_method(month_start),
        "by_method_all": by_method(None),
        "statuses": statuses,
        "operations": ops,
        "actual_received_balance": actual_received_balance,
        "financial_rule": "TICKET_READY_ONLY",
    }


def operation_details(operation_id: str, employee_id: str, manager: bool=False, allow_shared: bool=False) -> dict[str,Any] | None:
    with db() as con:
        row=con.execute("SELECT * FROM operations WHERE operation_id=?",(operation_id,)).fetchone()
        if not row:return None
        if not manager and str(row["employee_id"] or "")!=employee_id:
            if not allow_shared:
                return None
            shared=con.execute("SELECT shared_for_staff FROM payment_accounts WHERE account_id=?",(row["account_id"],)).fetchone()
            if not (shared and int(shared[0] or 0)==1 and str(row["payment_method"] or "")=="إنستا باي"):
                return None
        op=dict(row)
        proof=con.execute("SELECT * FROM payment_proofs WHERE operation_id=? ORDER BY uploaded_at DESC LIMIT 1",(operation_id,)).fetchone(); op["proof"]=dict(proof) if proof else None
        try: op["proof"]["vision"]=json.loads(op["proof"].get("vision_json") or "{}") if op["proof"] else {}
        except: pass
        tx=con.execute("SELECT * FROM android_transactions WHERE operation_id=? ORDER BY received_at DESC LIMIT 3",(operation_id,)).fetchall(); op["android"]=[dict(r) for r in tx]
        mm=con.execute("SELECT * FROM payment_matches WHERE operation_id=? ORDER BY created_at DESC LIMIT 5",(operation_id,)).fetchall(); op["matches"]=[dict(r) for r in mm]
        brow=con.execute("SELECT payment_reconciliation_json FROM booking_requests WHERE booking_id=?",(op.get("booking_id"),)).fetchone()
        try: op["reconciliation"]=json.loads(brow[0] or "{}") if brow else {}
        except Exception: op["reconciliation"]={}
        op["payment_vision"]=(op.get("reconciliation") or {}).get("vision") or (op.get("proof") or {}).get("vision") or {}
        op["payment_vision_status"]=(op.get("reconciliation") or {}).get("status") or ("ANALYZED" if op["payment_vision"] else "NOT_ANALYZED")
        ee=con.execute("SELECT * FROM operation_events WHERE operation_id=? ORDER BY created_at DESC LIMIT 30",(operation_id,)).fetchall(); op["events"]= [dict(r) for r in ee]
    return op


def log_staff_action(operation_id: str, employee_id: str, action: str, details: dict[str,Any]|None=None) -> None:
    with db() as con: con.execute("INSERT INTO staff_actions(action_id,operation_id,employee_id,action,details_json,created_at) VALUES(?,?,?,?,?,?)",("ACT-"+secrets.token_hex(7).upper(),operation_id,employee_id,action,json.dumps(details or {},ensure_ascii=False),time.time()))
    log_event(operation_id,action,employee_id,details)


def media_token(operation_id: str, path: str, ttl: int=48*3600)->str:
    t=secrets.token_urlsafe(32); now=time.time()
    with db() as con: con.execute("INSERT INTO media_tokens(token,operation_id,path,expires_at,created_at) VALUES(?,?,?,?,?)",(t,operation_id,path,now+ttl,now))
    return t


def resolve_media_token(token: str)->str|None:
    with db() as con: row=con.execute("SELECT path,expires_at FROM media_tokens WHERE token=?",(token,)).fetchone()
    if not row or float(row[1])<time.time(): return None
    return str(row[0])