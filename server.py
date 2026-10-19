"""ChefOS full-stack local application server (stdlib only)."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import json
import os
import secrets
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path.home() / ".chefos"
DB_PATH = Path(os.environ.get("CHEFOS_DB_PATH", DATA_DIR / "chefos.sqlite3"))
MAX_BODY = 1_000_000


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS organizations(id TEXT PRIMARY KEY,name TEXT NOT NULL,timezone TEXT NOT NULL DEFAULT 'America/New_York',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS locations(id TEXT PRIMARY KEY,org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,name TEXT NOT NULL,address TEXT NOT NULL DEFAULT '',timezone TEXT NOT NULL DEFAULT 'America/New_York',is_default INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,email TEXT NOT NULL,password_hash TEXT NOT NULL,display_name TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'owner',active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(org_id,email));
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,csrf_token TEXT NOT NULL,expires_at INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_global ON users(lower(email));
CREATE TABLE IF NOT EXISTS invitations(id TEXT PRIMARY KEY,org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,email TEXT NOT NULL,role TEXT NOT NULL,employee_id TEXT REFERENCES employees(id),token_hash TEXT NOT NULL UNIQUE,invited_by TEXT NOT NULL REFERENCES users(id),expires_at INTEGER NOT NULL,accepted_at TEXT,revoked_at TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS audit_log(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,actor_id TEXT,location_id TEXT,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,action TEXT NOT NULL,details TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY,org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,name TEXT NOT NULL,category TEXT NOT NULL DEFAULT 'Other',unit TEXT NOT NULL DEFAULT 'each',par REAL NOT NULL DEFAULT 0,cost REAL NOT NULL DEFAULT 0,vendor_id TEXT,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS inventory_levels(id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,on_hand REAL NOT NULL DEFAULT 0,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(item_id,location_id));
CREATE TABLE IF NOT EXISTS stock_movements(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,location_id TEXT NOT NULL,item_id TEXT NOT NULL REFERENCES items(id),movement_type TEXT NOT NULL,quantity REAL NOT NULL,unit_cost REAL NOT NULL DEFAULT 0,reference TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',actor_id TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS vendors(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,name TEXT NOT NULL,email TEXT NOT NULL DEFAULT '',phone TEXT NOT NULL DEFAULT '',account_number TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS purchase_orders(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,location_id TEXT NOT NULL REFERENCES locations(id),vendor_id TEXT NOT NULL REFERENCES vendors(id),po_number TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'draft',expected_at TEXT,notes TEXT NOT NULL DEFAULT '',created_by TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,received_at TEXT);
CREATE TABLE IF NOT EXISTS purchase_order_lines(id TEXT PRIMARY KEY,po_id TEXT NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,item_id TEXT NOT NULL REFERENCES items(id),quantity REAL NOT NULL,received_quantity REAL NOT NULL DEFAULT 0,unit_cost REAL NOT NULL);
CREATE TABLE IF NOT EXISTS recipes(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,name TEXT NOT NULL,menu_price REAL NOT NULL DEFAULT 0,servings REAL NOT NULL DEFAULT 1,active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS recipe_ingredients(id TEXT PRIMARY KEY,recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,item_id TEXT NOT NULL REFERENCES items(id),quantity REAL NOT NULL,unit TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS waste_logs(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,location_id TEXT NOT NULL,item_id TEXT NOT NULL REFERENCES items(id),quantity REAL NOT NULL,reason TEXT NOT NULL,station TEXT NOT NULL DEFAULT '',shift_id TEXT,notes TEXT NOT NULL DEFAULT '',actor_id TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS employees(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,location_id TEXT NOT NULL REFERENCES locations(id),name TEXT NOT NULL,email TEXT NOT NULL DEFAULT '',phone TEXT NOT NULL DEFAULT '',role TEXT NOT NULL DEFAULT 'Team member',department TEXT NOT NULL DEFAULT 'Front of house',hourly_rate REAL NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS shifts(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,location_id TEXT NOT NULL REFERENCES locations(id),employee_id TEXT REFERENCES employees(id) ON DELETE SET NULL,position TEXT NOT NULL,starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'scheduled',notes TEXT NOT NULL DEFAULT '',created_by TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS shift_requests(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,shift_id TEXT NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,requester_id TEXT NOT NULL REFERENCES employees(id),target_employee_id TEXT REFERENCES employees(id),request_type TEXT NOT NULL DEFAULT 'trade',status TEXT NOT NULL DEFAULT 'pending',note TEXT NOT NULL DEFAULT '',reviewed_by TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,reviewed_at TEXT);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,location_id TEXT NOT NULL REFERENCES locations(id),title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',assigned_to TEXT REFERENCES employees(id),due_at TEXT,status TEXT NOT NULL DEFAULT 'open',priority TEXT NOT NULL DEFAULT 'normal',created_by TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,completed_at TEXT);
CREATE TABLE IF NOT EXISTS sales_entries(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,location_id TEXT NOT NULL REFERENCES locations(id),business_date TEXT NOT NULL,channel TEXT NOT NULL DEFAULT 'in_store',net_sales REAL NOT NULL,covers INTEGER NOT NULL DEFAULT 0,food_cost REAL NOT NULL DEFAULT 0,labor_cost REAL NOT NULL DEFAULT 0,source TEXT NOT NULL DEFAULT 'manual',external_id TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(org_id,location_id,business_date,channel,external_id));
CREATE TABLE IF NOT EXISTS channels(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,location_id TEXT NOT NULL,name TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(location_id,name));
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id),body TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS recipes(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,name TEXT NOT NULL,menu_price REAL NOT NULL DEFAULT 0,servings REAL NOT NULL DEFAULT 1,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS recipe_ingredients(id TEXT PRIMARY KEY,recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,item_id TEXT NOT NULL REFERENCES items(id),quantity REAL NOT NULL,unit TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS time_entries(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,location_id TEXT NOT NULL REFERENCES locations(id),employee_id TEXT NOT NULL REFERENCES employees(id),shift_id TEXT REFERENCES shifts(id),clock_in TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,clock_out TEXT,status TEXT NOT NULL DEFAULT 'clocked_in',notes TEXT NOT NULL DEFAULT '',reviewed_by TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS api_keys(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,user_id TEXT NOT NULL REFERENCES users(id),name TEXT NOT NULL,key_hash TEXT NOT NULL UNIQUE,permissions TEXT NOT NULL DEFAULT 'sales:write',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,last_used_at TEXT,revoked_at TEXT);
CREATE INDEX IF NOT EXISTS idx_items_org ON items(org_id,active,name);
CREATE INDEX IF NOT EXISTS idx_movements_location ON stock_movements(org_id,location_id,created_at);
CREATE INDEX IF NOT EXISTS idx_shifts_location_date ON shifts(org_id,location_id,starts_at);
CREATE INDEX IF NOT EXISTS idx_sales_date ON sales_entries(org_id,location_id,business_date);
"""


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def ident():
    import uuid
    return str(uuid.uuid4())


def password_hash(password, salt=None):
    import hashlib
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
    return f"{salt.hex()}:{digest.hex()}"


def password_ok(password, stored):
    import hashlib
    try:
        salt_hex, expected = stored.split(":", 1)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 260_000).hex()
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def initialize():
    with db() as con:
        con.executescript(SCHEMA)
        existing=con.execute("SELECT id FROM organizations WHERE name='Juniper House' LIMIT 1").fetchone()
        if existing:
            org=existing[0]
            if not con.execute("SELECT 1 FROM recipes WHERE org_id=? LIMIT 1",(org,)).fetchone():
                items={r["name"]:r["id"] for r in con.execute("SELECT id,name FROM items WHERE org_id=?",(org,))}
                for name,price,servings,ingredients in [("Chanterelle toast",16,1,[("Chanterelle",0.15),("Sourdough loaf",0.5)]),("Burrata & greens",18,1,[("Burrata",1),("Arugula",0.08)])]:
                    if all(n in items for n,_ in ingredients):
                        recipe=ident();con.execute("INSERT INTO recipes(id,org_id,name,menu_price,servings) VALUES(?,?,?,?,?)",(recipe,org,name,price,servings))
                        for item_name,quantity in ingredients:
                            row=con.execute("SELECT unit FROM items WHERE id=?",(items[item_name],)).fetchone();con.execute("INSERT INTO recipe_ingredients(id,recipe_id,item_id,quantity,unit) VALUES(?,?,?,?,?)",(ident(),recipe,items[item_name],quantity,row[0]))
            return
        if con.execute("SELECT 1 FROM organizations LIMIT 1").fetchone():
            return
        org, loc, user = ident(), ident(), ident()
        con.execute("INSERT INTO organizations(id,name) VALUES(?,?)", (org, "Juniper House"))
        con.execute("INSERT INTO locations(id,org_id,name,address,is_default) VALUES(?,?,?,?,1)", (loc, org, "Juniper House · Brooklyn", "Brooklyn, NY"))
        con.execute("INSERT INTO users(id,org_id,email,password_hash,display_name,role) VALUES(?,?,?,?,?,?)", (user, org, "owner@chefos.local", password_hash("ChefOS-demo-2026!"), "Morgan Lee", "owner"))
        vendors = {}
        for name in ("North Shore Seafood", "Hudson Valley Produce", "Union Dairy Co."):
            vid = ident(); vendors[name] = vid
            con.execute("INSERT INTO vendors(id,org_id,name,email) VALUES(?,?,?,?)", (vid, org, name, "orders@example.test"))
        seed_items = [
            ("Halibut", "Seafood", "lb", 6, 19.5, "North Shore Seafood", 2),
            ("Duck leg", "Meat", "each", 12, 4.25, "North Shore Seafood", 11),
            ("Burrata", "Dairy", "each", 8, 5.1, "Union Dairy Co.", 8),
            ("Chanterelle", "Produce", "lb", 4, 13.0, "Hudson Valley Produce", 1),
            ("Arugula", "Produce", "lb", 5, 3.4, "Hudson Valley Produce", 7),
            ("Olive oil", "Pantry", "L", 3, 12.0, "Hudson Valley Produce", 4),
            ("Heavy cream", "Dairy", "qt", 8, 4.15, "Union Dairy Co.", 6),
            ("Sourdough loaf", "Bakery", "each", 10, 4.8, "Hudson Valley Produce", 8),
        ]
        item_ids = {}
        for name, category, unit, par, cost, vendor, stock in seed_items:
            iid = ident(); item_ids[name] = iid
            con.execute("INSERT INTO items(id,org_id,name,category,unit,par,cost,vendor_id) VALUES(?,?,?,?,?,?,?,?)", (iid, org, name, category, unit, par, cost, vendors[vendor]))
            con.execute("INSERT INTO inventory_levels(id,item_id,location_id,on_hand) VALUES(?,?,?,?)", (ident(), iid, loc, stock))
            con.execute("INSERT INTO stock_movements(id,org_id,location_id,item_id,movement_type,quantity,unit_cost,reference,notes,actor_id) VALUES(?,?,?,?,?,?,?,?,?,?)", (ident(), org, loc, iid, "opening", stock, cost, "OPENING", "Opening inventory", user))
        for name,price,servings,ingredients in [("Chanterelle toast",16,1,[("Chanterelle",0.15),("Sourdough loaf",0.5)]),("Burrata & greens",18,1,[("Burrata",1),("Arugula",0.08)])]:
            recipe=ident();con.execute("INSERT INTO recipes(id,org_id,name,menu_price,servings) VALUES(?,?,?,?,?)",(recipe,org,name,price,servings))
            for item_name,quantity in ingredients:
                item_id=item_ids[item_name];unit=con.execute("SELECT unit FROM items WHERE id=?",(item_id,)).fetchone()[0];con.execute("INSERT INTO recipe_ingredients(id,recipe_id,item_id,quantity,unit) VALUES(?,?,?,?,?)",(ident(),recipe,item_id,quantity,unit))
        employee_names = [("Alex Rivera","Kitchen","Line cook",24),("Mina Chen","Front of house","Server",19),("Jordan Wilson","Bar","Bartender",21),("Sam Patel","Kitchen","Sous chef",31),("Taylor Brooks","Front of house","Host",18)]
        employees = []
        for name,dept,role,rate in employee_names:
            eid=ident(); employees.append(eid)
            con.execute("INSERT INTO employees(id,org_id,location_id,name,role,department,hourly_rate) VALUES(?,?,?,?,?,?,?)",(eid,org,loc,name,role,dept,rate))
        from datetime import date, timedelta
        today = date.today()
        for offset in range(7):
            day=(today+timedelta(days=offset)).isoformat()
            for j,eid in enumerate(employees[:4]):
                start="10:00" if j%2==0 else "15:00"; end="18:00" if j%2==0 else "23:00"
                con.execute("INSERT INTO shifts(id,org_id,location_id,employee_id,position,starts_at,ends_at,created_by) VALUES(?,?,?,?,?,?,?,?)",(ident(),org,loc,eid,employee_names[j][2],f"{day}T{start}:00",f"{day}T{end}:00",user))
        for title, desc, priority in [("Receive the seafood delivery","Verify invoice quantities and update stock.","high"),("Check walk-in temperature","Log the opening temperature before prep.","normal"),("Review Friday coverage","Confirm the late dinner shift.","normal")]:
            con.execute("INSERT INTO tasks(id,org_id,location_id,title,description,priority,created_by) VALUES(?,?,?,?,?,?,?)",(ident(),org,loc,title,desc,priority,user))
        for offset in range(14):
            day=(today-timedelta(days=13-offset)).isoformat()
            for channel,factor in (("in_store",1.0),("delivery",0.22)):
                sales=14200+((offset*977)%4800)
                con.execute("INSERT INTO sales_entries(id,org_id,location_id,business_date,channel,net_sales,covers,food_cost,labor_cost,source) VALUES(?,?,?,?,?,?,?,?,?,?)",(ident(),org,loc,day,channel,round(sales*factor,2),int(160*factor),round(sales*factor*.29,2),round(sales*factor*.27,2),"sample"))
        for name in ("#kitchen", "#floor", "#management"):
            con.execute("INSERT INTO channels(id,org_id,location_id,name) VALUES(?,?,?,?)",(ident(),org,loc,name))
    print("Demo sign-in: owner@chefos.local / ChefOS-demo-2026!", file=sys.stderr)


class APIError(Exception):
    def __init__(self, status, message, code="request_error", fields=None):
        self.status, self.message, self.code, self.fields = status, message, code, fields or {}


class ChefOSHandler(SimpleHTTPRequestHandler):
    server_version = "ChefOS/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def translate_path(self, path):
        request_path = urlsplit(path).path
        if any(part in (".git", ".chefos", "data", "__pycache__") for part in Path(request_path).parts) or request_path.endswith((".sqlite", ".sqlite3", ".db")):
            return str(ROOT / "__blocked_static_file__")
        resolved = Path(super().translate_path(path))
        if not resolved.exists() and Path(request_path).suffix == "":
            return str(ROOT / "index.html")
        return str(resolved)

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            return
        super().log_message(fmt, *args)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: https:; font-src 'self'; connect-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "public, max-age=300")
        super().end_headers()

    def json(self, status, data, extra_headers=()):
        body = json.dumps(data, separators=(",", ":"), default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key,value in extra_headers: self.send_header(key,value)
        self.end_headers(); self.wfile.write(body)

    def body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > MAX_BODY: raise APIError(413,"Request body is too large","payload_too_large")
        try: data=json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError: raise APIError(400,"Request body must be valid JSON","invalid_json")
        if not isinstance(data,dict): raise APIError(400,"Request body must be a JSON object","invalid_json")
        return data

    def do_OPTIONS(self):
        self.send_response(204); self.send_header("Allow","GET, POST, PATCH, DELETE, OPTIONS"); self.end_headers()

    def do_GET(self): self.handle_api() if urlsplit(self.path).path.startswith("/api/") else super().do_GET()
    def do_POST(self): self.handle_api()
    def do_PATCH(self): self.handle_api()
    def do_DELETE(self): self.handle_api()

    def handle_api(self):
        try:
            result, status, headers = self.dispatch()
            self.json(status,result,headers)
        except APIError as exc:
            self.json(exc.status,{"error":{"code":exc.code,"message":exc.message,"fields":exc.fields}})
        except sqlite3.IntegrityError as exc:
            self.json(409,{"error":{"code":"constraint_violation","message":"That change conflicts with existing records."}})
        except Exception:
            import traceback; traceback.print_exc()
            self.json(500,{"error":{"code":"internal_error","message":"The request could not be completed."}})

    def dispatch(self):
        method=self.command
        parsed=urlsplit(self.path); path=parsed.path.rstrip("/") or "/"; query=__import__('urllib.parse',fromlist=['parse_qs']).parse_qs(parsed.query)
        parts=[p for p in path.split("/") if p]
        if path=="/api/v1/health": return {"status":"ok","service":"chefos","time":int(time.time())},200,[]
        con=db()
        try:
            if parts[:3]==["api","v1","auth"]:
                result,status,headers=self.auth(con,parts[3:] if len(parts)>3 else [],method)
                con.commit(); return result,status,headers
            user,csrf=self.authenticate(con)
            if method not in ("GET","HEAD") and not getattr(self,"is_api_key",False) and not secrets.compare_digest(self.headers.get("X-CSRF-Token",""),csrf):
                raise APIError(403,"Missing or invalid CSRF token.","csrf_failed")
            org=user["org_id"]
            if getattr(self,"is_api_key",False):
                if parts[2:3]!=["sales"] or method!="POST" or "sales:write" not in self.api_permissions: raise APIError(403,"This API key can only write sales records.","forbidden")
            result,status=self.resource(con,user,parts[2:],method,query)
            con.commit(); return result,status,[]
        finally: con.close()

    def authenticate(self,con):
        bearer=self.headers.get("Authorization","").removeprefix("Bearer ").strip()
        if bearer.startswith("ck_live_"):
            import hashlib
            keyhash=hashlib.sha256(bearer.encode()).hexdigest()
            row=con.execute("SELECT u.*,k.id AS api_key_id,k.permissions FROM api_keys k JOIN users u ON u.id=k.user_id WHERE k.key_hash=? AND k.revoked_at IS NULL AND u.active=1",(keyhash,)).fetchone()
            if not row:raise APIError(401,"API key is invalid or revoked.","invalid_api_key")
            self.is_api_key=True;self.api_permissions=set(row["permissions"].split(","));con.execute("UPDATE api_keys SET last_used_at=CURRENT_TIMESTAMP WHERE id=?",(row["api_key_id"],))
            return row,""
        self.is_api_key=False
        cookie=self.headers.get("Cookie","")
        token=next((v.strip() for v in cookie.split(";") if v.strip().startswith("chefos_session=")),"").partition("=")[2]
        if not token: raise APIError(401,"Sign in to continue.","unauthenticated")
        import hashlib
        row=con.execute("SELECT s.csrf_token,s.expires_at,u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row or row["expires_at"]<int(time.time()) or not row["active"]:
            raise APIError(401,"Your session expired. Sign in again.","session_expired")
        self.current_session_id=hashlib.sha256(token.encode()).hexdigest()
        return row,row["csrf_token"]

    def auth(self,con,parts,method):
        action=parts[0] if parts else ""
        if method=="POST" and action=="register":
            data=self.body(); email=str(data.get("email","")).strip().lower(); password=str(data.get("password","")); name=str(data.get("name","")).strip(); restaurant=str(data.get("restaurant","")).strip(); location=str(data.get("location","")).strip() or "Main location"
            fields={}
            if "@" not in email: fields["email"]="Enter a valid email address."
            if len(password)<12: fields["password"]="Use at least 12 characters."
            if not name: fields["name"]="Enter your name."
            if not restaurant: fields["restaurant"]="Enter your restaurant name."
            if fields: raise APIError(422,"Check the highlighted fields.","validation_error",fields)
            if con.execute("SELECT 1 FROM users WHERE lower(email)=?",(email,)).fetchone(): raise APIError(409,"An account already uses this email address.","email_exists")
            org,loc,uid=ident(),ident(),ident()
            con.execute("INSERT INTO organizations(id,name) VALUES(?,?)",(org,restaurant))
            con.execute("INSERT INTO locations(id,org_id,name,is_default) VALUES(?,?,?,1)",(loc,org,location))
            con.execute("INSERT INTO users(id,org_id,email,password_hash,display_name,role) VALUES(?,?,?,?,?,'owner')",(uid,org,email,password_hash(password),name))
            token,csrf=self.new_session(con,uid)
            return {"csrf_token":csrf,"user":{"id":uid,"email":email,"name":name,"role":"owner","organization":restaurant},"location":{"id":loc,"name":location}},201,self.session_cookie(token)
        if method=="POST" and action=="accept-invitation":
            d=self.body();token=self.required(d,"token");name=self.required(d,"name");password=str(d.get("password",""))
            if len(password)<12:raise APIError(422,"Use at least 12 characters for your password.","validation_error",{"password":"Use at least 12 characters."})
            import hashlib
            invitation=con.execute("SELECT * FROM invitations WHERE token_hash=? AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>=?",(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
            if not invitation:raise APIError(400,"This invitation is invalid, expired, or already used.","invalid_invitation")
            email=invitation["email"]
            if con.execute("SELECT 1 FROM users WHERE lower(email)=?",(email.lower(),)).fetchone():raise APIError(409,"An account already uses this email address.","email_exists")
            uid=ident();con.execute("INSERT INTO users(id,org_id,email,password_hash,display_name,role) VALUES(?,?,?,?,?,?)",(uid,invitation["org_id"],email,password_hash(password),name,invitation["role"]));con.execute("UPDATE invitations SET accepted_at=CURRENT_TIMESTAMP WHERE id=?",(invitation["id"],))
            token,csrf=self.new_session(con,uid);org=con.execute("SELECT name FROM organizations WHERE id=?",(invitation["org_id"],)).fetchone()[0];location=con.execute("SELECT id,name FROM locations WHERE org_id=? ORDER BY is_default DESC,name LIMIT 1",(invitation["org_id"],)).fetchone()
            return {"csrf_token":csrf,"user":{"id":uid,"email":email,"name":name,"role":invitation["role"],"organization":org},"location":dict(location) if location else None},201,self.session_cookie(token)
        if method=="POST" and action=="login":
            data=self.body(); email=str(data.get("email","")).strip().lower(); password=str(data.get("password",""))
            user=con.execute("SELECT * FROM users WHERE lower(email)=? AND active=1 ORDER BY created_at DESC LIMIT 1",(email,)).fetchone()
            if not user or not password_ok(password,user["password_hash"]): raise APIError(401,"Email or password is incorrect.","invalid_credentials")
            token,csrf=self.new_session(con,user["id"])
            loc=con.execute("SELECT id,name FROM locations WHERE org_id=? ORDER BY is_default DESC,name LIMIT 1",(user["org_id"],)).fetchone()
            return {"csrf_token":csrf,"user":{"id":user["id"],"email":user["email"],"name":user["display_name"],"role":user["role"],"organization":con.execute("SELECT name FROM organizations WHERE id=?",(user["org_id"],)).fetchone()[0]},"location":dict(loc) if loc else None},200,self.session_cookie(token)
        if action=="logout" and method=="POST":
            cookie=self.headers.get("Cookie",""); token=next((v.strip() for v in cookie.split(";") if v.strip().startswith("chefos_session=")),"").partition("=")[2]
            if token:
                import hashlib
                row=con.execute("SELECT csrf_token FROM sessions WHERE token_hash=?",(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
                if row and not secrets.compare_digest(self.headers.get("X-CSRF-Token",""),row[0]): raise APIError(403,"Missing or invalid CSRF token.","csrf_failed")
                con.execute("DELETE FROM sessions WHERE token_hash=?",(hashlib.sha256(token.encode()).hexdigest(),))
            return {"ok":True},200,[("Set-Cookie","chefos_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")]
        if action=="me" and method=="GET":
            user,csrf=self.authenticate(con); location=con.execute("SELECT id,name FROM locations WHERE org_id=? ORDER BY is_default DESC,name LIMIT 1",(user["org_id"],)).fetchone()
            org=con.execute("SELECT name,timezone FROM organizations WHERE id=?",(user["org_id"],)).fetchone()
            return {"csrf_token":csrf,"user":{"id":user["id"],"email":user["email"],"name":user["display_name"],"role":user["role"]},"organization":dict(org),"location":dict(location) if location else None},200,[]
        raise APIError(404,"Auth endpoint not found.","not_found")

    def new_session(self,con,user_id):
        token=secrets.token_urlsafe(36); csrf=secrets.token_urlsafe(24)
        import hashlib
        con.execute("INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at) VALUES(?,?,?,?)",(hashlib.sha256(token.encode()).hexdigest(),user_id,csrf,int(time.time())+60*60*24*14))
        return token,csrf

    def session_cookie(self,token):
        secure="; Secure" if self.headers.get("X-Forwarded-Proto")=="https" else ""
        return [("Set-Cookie",f"chefos_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=1209600{secure}")]

    def audit(self,con,user,entity_type,entity_id,action,details=None,location_id=None):
        con.execute("INSERT INTO audit_log(id,org_id,actor_id,location_id,entity_type,entity_id,action,details) VALUES(?,?,?,?,?,?,?,?)",(ident(),user["org_id"],user["id"],location_id,entity_type,entity_id,action,json.dumps(details or {})))

    def resource(self,con,user,parts,method,query):
        if not parts: return {"api":"ChefOS","version":"v1","resources":["locations","inventory","vendors","purchase-orders","waste","employees","shifts","tasks","sales","reports","channels","audit-log"]},200
        resource=parts[0]; rid=parts[1] if len(parts)>1 else None; action=parts[2] if len(parts)>2 else None; org=user["org_id"]
        location_id=str((query.get("location_id") or [""])[0]) or None
        if location_id and not con.execute("SELECT 1 FROM locations WHERE id=? AND org_id=?",(location_id,org)).fetchone(): raise APIError(404,"Location not found.","not_found")
        if resource=="locations":
            if method=="GET": return {"data":[dict(r) for r in con.execute("SELECT id,name,address,timezone,is_default,created_at FROM locations WHERE org_id=? ORDER BY is_default DESC,name",(org,))]},200
            if method=="POST":
                self.require_role(user,"owner","admin"); d=self.body(); name=self.required(d,"name"); lid=ident(); con.execute("INSERT INTO locations(id,org_id,name,address,timezone) VALUES(?,?,?,?,?)",(lid,org,name,str(d.get("address", "")),str(d.get("timezone","America/New_York")))); self.audit(con,user,"location",lid,"created",{"name":name}); return {"id":lid,"name":name},201
        if resource=="inventory": return self.inventory(con,user,rid,action,method,query)
        if resource=="recipes": return self.recipes(con,user,rid,method)
        if resource in ("time-entries","timeclock"): return self.time_entries(con,user,rid,method,query)
        if resource=="api-keys": return self.integration_keys(con,user,rid,method)
        if resource=="vendors":
            if method=="GET": return {"data":[dict(r) for r in con.execute("SELECT * FROM vendors WHERE org_id=? AND active=1 ORDER BY name",(org,))]},200
            if method=="POST":
                self.require_role(user,"owner","admin","manager"); d=self.body(); name=self.required(d,"name"); vid=ident(); con.execute("INSERT INTO vendors(id,org_id,name,email,phone,account_number) VALUES(?,?,?,?,?,?)",(vid,org,name,str(d.get("email","")),str(d.get("phone","")),str(d.get("account_number","")))); self.audit(con,user,"vendor",vid,"created",{"name":name}); return {"id":vid,"name":name},201
        if resource in ("purchase-orders","purchase_orders"): return self.purchase_orders(con,user,rid,action,method,query)
        if resource=="waste": return self.waste(con,user,rid,action,method,query)
        if resource in ("employees","team"): return self.employees(con,user,rid,method,query)
        if resource=="users":
            self.require_role(user,"owner","admin","manager")
            if method=="GET":return {"data":[dict(r) for r in con.execute("SELECT id,email,display_name,role,active,created_at FROM users WHERE org_id=? ORDER BY display_name",(org,))]},200
            raise APIError(405,"Use invitations to add workspace accounts.","method_not_allowed")
        if resource=="invitations":return self.invitations(con,user,rid,method)
        if resource in ("shifts","schedule"): return self.shifts(con,user,rid,action,method,query)
        if resource=="shift-requests": return self.shift_requests(con,user,rid,action,method,query)
        if resource=="tasks": return self.tasks(con,user,rid,method,query)
        if resource=="sales": return self.sales(con,user,rid,action,method,query)
        if resource=="reports": return self.reports(con,user,action,method,query)
        if resource=="channels": return self.channels(con,user,rid,action,method,query)
        if resource=="audit-log" and method=="GET":
            self.require_role(user,"owner","admin","manager"); rows=con.execute("SELECT a.*,u.display_name AS actor_name FROM audit_log a LEFT JOIN users u ON u.id=a.actor_id WHERE a.org_id=? ORDER BY a.created_at DESC LIMIT 200",(org,)); return {"data":[dict(r) for r in rows]},200
        raise APIError(404,"Resource not found.","not_found")

    def require_role(self,user,*roles):
        if user["role"] not in roles: raise APIError(403,"Your role cannot perform this action.","forbidden")
    def required(self,data,key):
        value=data.get(key)
        if value is None or not str(value).strip(): raise APIError(422,f"{key.replace('_',' ').capitalize()} is required.","validation_error",{key:"This field is required."})
        return str(value).strip()
    def number(self,data,key,minimum=None):
        try: value=float(data[key])
        except (KeyError,TypeError,ValueError): raise APIError(422,f"{key.replace('_',' ').capitalize()} must be a number.","validation_error",{key:"Enter a valid number."})
        if minimum is not None and value<minimum: raise APIError(422,f"{key.replace('_',' ').capitalize()} is below the allowed minimum.","validation_error",{key:f"Use {minimum} or higher."})
        return value
    def scoped_item(self,con,org,item_id):
        row=con.execute("SELECT * FROM items WHERE id=? AND org_id=? AND active=1",(item_id,org)).fetchone()
        if not row: raise APIError(404,"Inventory item not found.","not_found")
        return row
    def inventory(self,con,user,rid,action,method,query):
        org=user["org_id"]; location=str((query.get("location_id") or [""])[0]) or None
        if location and not con.execute("SELECT 1 FROM locations WHERE id=? AND org_id=?",(location,org)).fetchone(): raise APIError(404,"Location not found.","not_found")
        if not rid:
            if method=="GET":
                where="i.org_id=? AND i.active=1"; args=[org]
                if location: where+=" AND l.location_id=?";args.append(location)
                category=(query.get("category") or [""])[0]
                if category: where+=" AND i.category=?";args.append(category)
                rows=con.execute(f"SELECT i.*,v.name AS vendor_name,l.location_id,l.on_hand,l.updated_at AS stock_updated_at,(l.on_hand-i.par) AS variance FROM items i LEFT JOIN vendors v ON v.id=i.vendor_id LEFT JOIN inventory_levels l ON l.item_id=i.id WHERE {where} ORDER BY i.category,i.name",args)
                return {"data":[dict(r) for r in rows]},200
            if method=="POST":
                self.require_role(user,"owner","admin","manager"); d=self.body(); name=self.required(d,"name"); unit=self.required(d,"unit"); category=str(d.get("category","Other")); iid=ident(); par=self.number(d,"par",0) if "par" in d else 0; cost=self.number(d,"cost",0) if "cost" in d else 0
                vendor_id=d.get("vendor_id")
                if vendor_id and not con.execute("SELECT 1 FROM vendors WHERE id=? AND org_id=?",(vendor_id,org)).fetchone(): raise APIError(422,"Choose a vendor in this workspace.","validation_error")
                con.execute("INSERT INTO items(id,org_id,name,category,unit,par,cost,vendor_id) VALUES(?,?,?,?,?,?,?,?)",(iid,org,name,category,unit,par,cost,vendor_id))
                loc=location or self.default_location(con,org)
                on_hand=self.number(d,"on_hand",0) if "on_hand" in d else 0
                con.execute("INSERT INTO inventory_levels(id,item_id,location_id,on_hand) VALUES(?,?,?,?)",(ident(),iid,loc,on_hand))
                self.movement(con,user,loc,iid,"opening",on_hand,cost,"","New item opening balance")
                self.audit(con,user,"inventory_item",iid,"created",{"name":name},loc)
                return {"id":iid,"name":name,"on_hand":on_hand},201
        if rid and action in ("adjust","movement") and method=="POST":
            self.require_role(user,"owner","admin","manager"); item=self.scoped_item(con,org,rid); d=self.body(); qty=self.number(d,"quantity",0.000001); kind=str(d.get("type","count_adjustment")); loc=location or self.default_location(con,org); row=con.execute("SELECT on_hand FROM inventory_levels WHERE item_id=? AND location_id=?",(rid,loc)).fetchone(); current=float(row[0]) if row else 0; delta=qty-current if kind=="count" else (qty if kind in ("receive","transfer_in") else -qty if kind in ("waste","transfer_out") else float(d.get("delta",0)))
            if kind not in ("count","receive","waste","transfer_in","transfer_out","count_adjustment"): raise APIError(422,"Unknown stock movement type.","validation_error")
            if kind=="count_adjustment": delta=self.number(d,"delta")
            updated=current+delta
            if updated<0: raise APIError(409,"This movement would make on-hand stock negative.","insufficient_stock")
            con.execute("INSERT INTO inventory_levels(id,item_id,location_id,on_hand) VALUES(?,?,?,?) ON CONFLICT(item_id,location_id) DO UPDATE SET on_hand=excluded.on_hand,updated_at=CURRENT_TIMESTAMP",(ident(),rid,loc,updated))
            mid=self.movement(con,user,loc,rid,kind,delta,float(item["cost"]),str(d.get("reference","")),str(d.get("notes","")))
            self.audit(con,user,"inventory_item",rid,"stock_moved",{"type":kind,"delta":delta,"on_hand":updated},loc)
            return {"movement_id":mid,"item_id":rid,"on_hand":updated,"delta":delta},201
        if rid and action=="movements" and method=="GET":
            self.scoped_item(con,org,rid); rows=con.execute("SELECT * FROM stock_movements WHERE org_id=? AND item_id=? ORDER BY created_at DESC LIMIT 100",(org,rid)); return {"data":[dict(r) for r in rows]},200
        if rid and not action and method=="PATCH":
            self.require_role(user,"owner","admin","manager"); item=self.scoped_item(con,org,rid); d=self.body(); allowed={k:d[k] for k in ("name","category","unit","par","cost","vendor_id") if k in d};
            for key in ("par","cost"):
                if key in allowed: allowed[key]=self.number(allowed,key,0)
            if not allowed: raise APIError(400,"No editable fields were provided.","empty_update")
            con.execute("UPDATE items SET "+",".join(f"{k}=?" for k in allowed)+" WHERE id=? AND org_id=?",[*allowed.values(),rid,org]); self.audit(con,user,"inventory_item",rid,"updated",allowed,location); return {"id":rid,"updated":True},200
        if rid and not action and method=="DELETE":
            self.require_role(user,"owner","admin"); self.scoped_item(con,org,rid); con.execute("UPDATE items SET active=0 WHERE id=? AND org_id=?",(rid,org)); self.audit(con,user,"inventory_item",rid,"archived",{},location); return {"id":rid,"archived":True},200
        if rid=="count" and method=="POST":
            d=self.body(); loc=location or self.default_location(con,org); results=[]
            for entry in d.get("items",[]):
                iid=entry.get("item_id"); item=self.scoped_item(con,org,iid); qty=self.number(entry,"quantity",0); row=con.execute("SELECT on_hand FROM inventory_levels WHERE item_id=? AND location_id=?",(iid,loc)).fetchone(); previous=float(row[0]) if row else 0; delta=qty-previous
                con.execute("INSERT INTO inventory_levels(id,item_id,location_id,on_hand) VALUES(?,?,?,?) ON CONFLICT(item_id,location_id) DO UPDATE SET on_hand=excluded.on_hand,updated_at=CURRENT_TIMESTAMP",(ident(),iid,loc,qty)); self.movement(con,user,loc,iid,"count",delta,float(item["cost"]),str(d.get("reference","COUNT")),str(entry.get("notes",""))); results.append({"item_id":iid,"previous":previous,"counted":qty,"variance":delta})
            self.audit(con,user,"inventory_count",ident(),"posted",{"items":len(results)},loc); return {"data":results},201
        raise APIError(405 if rid else 404,"Inventory operation not found.","not_found")

    def movement(self,con,user,location,item,kind,delta,cost,reference,notes):
        mid=ident(); con.execute("INSERT INTO stock_movements(id,org_id,location_id,item_id,movement_type,quantity,unit_cost,reference,notes,actor_id) VALUES(?,?,?,?,?,?,?,?,?,?)",(mid,user["org_id"],location,item,kind,delta,cost,reference,notes,user["id"])); return mid
    def default_location(self,con,org):
        row=con.execute("SELECT id FROM locations WHERE org_id=? ORDER BY is_default DESC,name LIMIT 1",(org,)).fetchone()
        if not row: raise APIError(409,"Create a location before using operations.","missing_location")
        return row[0]

    def purchase_orders(self,con,user,rid,action,method,query):
        org=user["org_id"]
        if not rid and method=="GET":
            loc=(query.get("location_id") or [""])[0];sql="SELECT p.*,v.name AS vendor_name,(SELECT SUM(quantity*unit_cost) FROM purchase_order_lines WHERE po_id=p.id) AS total FROM purchase_orders p JOIN vendors v ON v.id=p.vendor_id WHERE p.org_id=?";args=[org]
            if loc:sql+=" AND p.location_id=?";args.append(loc)
            sql+=" ORDER BY p.created_at DESC";rows=con.execute(sql,args); return {"data":[dict(r) for r in rows]},200
        if not rid and method=="POST":
            self.require_role(user,"owner","admin","manager"); d=self.body(); location=d.get("location_id") or self.default_location(con,org); vendor=d.get("vendor_id"); vendor_row=con.execute("SELECT id FROM vendors WHERE id=? AND org_id=? AND active=1",(vendor,org)).fetchone()
            if not vendor_row: raise APIError(422,"Choose a vendor in this workspace.","validation_error")
            if not con.execute("SELECT 1 FROM locations WHERE id=? AND org_id=?",(location,org)).fetchone(): raise APIError(422,"Choose a location in this workspace.","validation_error")
            lines=d.get("lines",[])
            if not lines: raise APIError(422,"Add at least one line item.","validation_error")
            po=ident(); number=f"PO-{time.strftime('%y%m%d')}-{secrets.randbelow(9000)+1000}"
            con.execute("INSERT INTO purchase_orders(id,org_id,location_id,vendor_id,po_number,status,expected_at,notes,created_by) VALUES(?,?,?,?,?,'ordered',?,?,?)",(po,org,location,vendor,number,d.get("expected_at"),str(d.get("notes","")),user["id"]))
            for line in lines:
                item=self.scoped_item(con,org,line.get("item_id")); qty=self.number(line,"quantity",0.000001); cost=self.number(line,"unit_cost",0); con.execute("INSERT INTO purchase_order_lines(id,po_id,item_id,quantity,unit_cost) VALUES(?,?,?,?,?)",(ident(),po,item["id"],qty,cost))
            self.audit(con,user,"purchase_order",po,"created",{"po_number":number},location); return {"id":po,"po_number":number,"status":"ordered"},201
        if rid and action=="receive" and method=="POST":
            self.require_role(user,"owner","admin","manager"); po=con.execute("SELECT * FROM purchase_orders WHERE id=? AND org_id=?",(rid,org)).fetchone()
            if not po: raise APIError(404,"Purchase order not found.","not_found")
            if po["status"] in ("received","cancelled"): raise APIError(409,"This purchase order can no longer be received.","invalid_state")
            d=self.body(); received=d.get("lines") or []
            lines=con.execute("SELECT * FROM purchase_order_lines WHERE po_id=?",(rid,)).fetchall(); receive_map={r.get("line_id"):r for r in received}
            for line in lines:
                spec=receive_map.get(line["id"],{}); qty=self.number(spec,"quantity",0) if "quantity" in spec else float(line["quantity"])-float(line["received_quantity"])
                remaining=float(line["quantity"])-float(line["received_quantity"])
                if qty>remaining: raise APIError(422,"Received quantity cannot exceed the open quantity.","validation_error")
                if qty:
                    level=con.execute("SELECT on_hand FROM inventory_levels WHERE item_id=? AND location_id=?",(line["item_id"],po["location_id"])).fetchone(); stock=(float(level[0]) if level else 0)+qty
                    con.execute("INSERT INTO inventory_levels(id,item_id,location_id,on_hand) VALUES(?,?,?,?) ON CONFLICT(item_id,location_id) DO UPDATE SET on_hand=excluded.on_hand,updated_at=CURRENT_TIMESTAMP",(ident(),line["item_id"],po["location_id"],stock)); self.movement(con,user,po["location_id"],line["item_id"],"receive",qty,line["unit_cost"],po["po_number"],"Purchase order receipt"); con.execute("UPDATE purchase_order_lines SET received_quantity=received_quantity+? WHERE id=?",(qty,line["id"]))
                    con.execute("UPDATE items SET cost=? WHERE id=? AND org_id=?",(line["unit_cost"],line["item_id"],org))
            open_count=con.execute("SELECT COUNT(*) FROM purchase_order_lines WHERE po_id=? AND received_quantity<quantity",(rid,)).fetchone()[0]; status="partially_received" if open_count else "received"
            con.execute("UPDATE purchase_orders SET status=?,received_at=CASE WHEN ?='received' THEN CURRENT_TIMESTAMP ELSE received_at END WHERE id=?",(status,status,rid)); self.audit(con,user,"purchase_order",rid,"received",{"status":status},po["location_id"]); return {"id":rid,"status":status},200
        if rid and action=="lines" and method=="GET":
            po=con.execute("SELECT id FROM purchase_orders WHERE id=? AND org_id=?",(rid,org)).fetchone()
            if not po: raise APIError(404,"Purchase order not found.","not_found")
            rows=con.execute("SELECT l.*,i.name AS item_name,i.unit FROM purchase_order_lines l JOIN items i ON i.id=l.item_id WHERE l.po_id=?",(rid,));return {"data":[dict(r) for r in rows]},200
        raise APIError(404,"Purchase order operation not found.","not_found")

    def waste(self,con,user,rid,action,method,query):
        org=user["org_id"]
        if method=="GET":
            loc=(query.get("location_id") or [""])[0];sql="SELECT w.*,i.name AS item_name,i.unit,e.display_name AS actor_name FROM waste_logs w JOIN items i ON i.id=w.item_id LEFT JOIN users e ON e.id=w.actor_id WHERE w.org_id=?";args=[org]
            if loc:sql+=" AND w.location_id=?";args.append(loc)
            sql+=" ORDER BY w.created_at DESC LIMIT 200";rows=con.execute(sql,args);return {"data":[dict(r) for r in rows]},200
        if method=="POST":
            self.require_role(user,"owner","admin","manager");d=self.body();item=self.scoped_item(con,org,d.get("item_id"));loc=d.get("location_id") or self.default_location(con,org);qty=self.number(d,"quantity",0.000001); reason=self.required(d,"reason");level=con.execute("SELECT on_hand FROM inventory_levels WHERE item_id=? AND location_id=?",(item["id"],loc)).fetchone();stock=float(level[0]) if level else 0
            if qty>stock:raise APIError(409,"Logged waste cannot exceed on-hand stock.","insufficient_stock")
            con.execute("UPDATE inventory_levels SET on_hand=on_hand-?,updated_at=CURRENT_TIMESTAMP WHERE item_id=? AND location_id=?",(qty,item["id"],loc));wid=ident();con.execute("INSERT INTO waste_logs(id,org_id,location_id,item_id,quantity,reason,station,notes,actor_id) VALUES(?,?,?,?,?,?,?,?,?)",(wid,org,loc,item["id"],qty,reason,str(d.get("station","")),str(d.get("notes","")),user["id"]));self.movement(con,user,loc,item["id"],"waste",-qty,item["cost"],wid,reason);self.audit(con,user,"waste",wid,"logged",{"item":item["name"],"quantity":qty,"reason":reason},loc);return {"id":wid,"quantity":qty,"item":item["name"]},201
        raise APIError(405,"Method not allowed.","method_not_allowed")

    def employees(self,con,user,rid,method,query):
        org=user["org_id"]
        if not rid and method=="GET":
            location=str((query.get("location_id") or [""])[0]) or None;sql="SELECT e.*,l.name AS location_name FROM employees e JOIN locations l ON l.id=e.location_id WHERE e.org_id=? AND e.active=1";args=[org]
            if location: sql+=" AND e.location_id=?";args.append(location)
            sql+=" ORDER BY e.department,e.name";return {"data":[dict(r) for r in con.execute(sql,args)]},200
        if not rid and method=="POST":
            self.require_role(user,"owner","admin","manager");d=self.body();name=self.required(d,"name");loc=d.get("location_id") or self.default_location(con,org)
            if not con.execute("SELECT 1 FROM locations WHERE id=? AND org_id=?",(loc,org)).fetchone():raise APIError(422,"Choose a location in this workspace.","validation_error")
            eid=ident();rate=self.number(d,"hourly_rate",0) if "hourly_rate" in d else 0
            con.execute("INSERT INTO employees(id,org_id,location_id,name,email,phone,role,department,hourly_rate) VALUES(?,?,?,?,?,?,?,?,?)",(eid,org,loc,name,str(d.get("email","")),str(d.get("phone","")),str(d.get("role","Team member")),str(d.get("department","Front of house")),rate));self.audit(con,user,"employee",eid,"created",{"name":name},loc);return {"id":eid,"name":name},201
        if rid and method=="PATCH":
            self.require_role(user,"owner","admin","manager");row=con.execute("SELECT * FROM employees WHERE id=? AND org_id=? AND active=1",(rid,org)).fetchone()
            if not row:raise APIError(404,"Team member not found.","not_found")
            d=self.body();allowed={k:d[k] for k in ("name","email","phone","role","department","hourly_rate") if k in d}
            if "hourly_rate" in allowed:allowed["hourly_rate"]=self.number(allowed,"hourly_rate",0)
            if not allowed:raise APIError(400,"No editable fields were provided.","empty_update")
            con.execute("UPDATE employees SET "+",".join(f"{k}=?" for k in allowed)+" WHERE id=?",[*allowed.values(),rid]);self.audit(con,user,"employee",rid,"updated",allowed,row["location_id"]);return {"id":rid,"updated":True},200
        if rid and method=="DELETE":
            self.require_role(user,"owner","admin");row=con.execute("SELECT location_id FROM employees WHERE id=? AND org_id=?",(rid,org)).fetchone()
            if not row:raise APIError(404,"Team member not found.","not_found")
            con.execute("UPDATE employees SET active=0 WHERE id=?",(rid,));self.audit(con,user,"employee",rid,"deactivated",{},row[0]);return {"id":rid,"active":False},200
        raise APIError(404,"Team operation not found.","not_found")

    def invitations(self,con,user,rid,method):
        org=user["org_id"]
        if not rid and method=="GET":
            self.require_role(user,"owner","admin","manager");rows=con.execute("SELECT id,email,role,employee_id,expires_at,accepted_at,revoked_at,created_at FROM invitations WHERE org_id=? ORDER BY created_at DESC",(org,));return {"data":[dict(r) for r in rows]},200
        if not rid and method=="POST":
            self.require_role(user,"owner","admin","manager");d=self.body();email=self.required(d,"email").lower();role=str(d.get("role","staff"));employee=d.get("employee_id") or None
            if "@" not in email:raise APIError(422,"Enter a valid email address.","validation_error",{"email":"Enter a valid email address."})
            if role not in ("admin","manager","staff"):raise APIError(422,"Choose admin, manager, or staff access.","validation_error")
            if user["role"]=="manager" and role!="staff":raise APIError(403,"Managers can invite staff accounts only.","forbidden")
            if con.execute("SELECT 1 FROM users WHERE lower(email)=?",(email,)).fetchone():raise APIError(409,"An account already uses this email address.","email_exists")
            if con.execute("SELECT 1 FROM invitations WHERE lower(email)=? AND org_id=? AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>=?",(email,org,int(time.time()))).fetchone():raise APIError(409,"An active invitation already exists for this email.","invitation_exists")
            if employee and not con.execute("SELECT 1 FROM employees WHERE id=? AND org_id=? AND active=1",(employee,org)).fetchone():raise APIError(422,"Choose an active team member in this workspace.","validation_error")
            raw="invite_"+secrets.token_urlsafe(32);import hashlib;iid=ident();con.execute("INSERT INTO invitations(id,org_id,email,role,employee_id,token_hash,invited_by,expires_at) VALUES(?,?,?,?,?,?,?,?)",(iid,org,email,role,employee,hashlib.sha256(raw.encode()).hexdigest(),user["id"],int(time.time())+60*60*24*7));self.audit(con,user,"invitation",iid,"created",{"email":email,"role":role});return {"id":iid,"email":email,"role":role,"token":raw,"accept_url":f"/app?invite={raw}","expires_in_days":7},201
        if rid and method=="DELETE":
            self.require_role(user,"owner","admin","manager");row=con.execute("SELECT id FROM invitations WHERE id=? AND org_id=? AND accepted_at IS NULL AND revoked_at IS NULL",(rid,org)).fetchone()
            if not row:raise APIError(404,"Pending invitation not found.","not_found")
            con.execute("UPDATE invitations SET revoked_at=CURRENT_TIMESTAMP WHERE id=?",(rid,));self.audit(con,user,"invitation",rid,"revoked");return {"id":rid,"revoked":True},200
        raise APIError(404,"Invitation operation not found.","not_found")

    def shifts(self,con,user,rid,action,method,query):
        org=user["org_id"]
        if not rid and method=="GET":
            location=str((query.get("location_id") or [""])[0]) or self.default_location(con,org);start=(query.get("from") or [""])[0];end=(query.get("to") or [""])[0]
            sql="SELECT s.*,e.name AS employee_name,e.department,e.hourly_rate FROM shifts s LEFT JOIN employees e ON e.id=s.employee_id WHERE s.org_id=? AND s.location_id=?";args=[org,location]
            if start:sql+=" AND s.starts_at>=?";args.append(start)
            if end:sql+=" AND s.starts_at<?";args.append(end)
            sql+=" ORDER BY s.starts_at,e.name";return {"data":[dict(r) for r in con.execute(sql,args)]},200
        if not rid and method=="POST":
            self.require_role(user,"owner","admin","manager");d=self.body();loc=d.get("location_id") or self.default_location(con,org);employee=d.get("employee_id");position=self.required(d,"position");start=self.required(d,"starts_at");end=self.required(d,"ends_at")
            if end<=start:raise APIError(422,"Shift end must be after its start.","validation_error",{"ends_at":"Choose a later end time."})
            if not con.execute("SELECT 1 FROM locations WHERE id=? AND org_id=?",(loc,org)).fetchone():raise APIError(422,"Choose a location in this workspace.","validation_error")
            if employee and not con.execute("SELECT 1 FROM employees WHERE id=? AND org_id=? AND location_id=? AND active=1",(employee,org,loc)).fetchone():raise APIError(422,"Choose an active team member at this location.","validation_error")
            if employee and con.execute("SELECT 1 FROM shifts WHERE org_id=? AND employee_id=? AND status!='cancelled' AND starts_at<? AND ends_at>?",(org,employee,end,start)).fetchone():raise APIError(409,"This team member already has an overlapping shift.","shift_conflict")
            sid=ident();con.execute("INSERT INTO shifts(id,org_id,location_id,employee_id,position,starts_at,ends_at,notes,created_by) VALUES(?,?,?,?,?,?,?,?,?)",(sid,org,loc,employee,position,start,end,str(d.get("notes","")),user["id"]));self.audit(con,user,"shift",sid,"created",{"position":position,"starts_at":start},loc);return {"id":sid,"status":"scheduled"},201
        if rid and method=="PATCH":
            self.require_role(user,"owner","admin","manager");row=con.execute("SELECT * FROM shifts WHERE id=? AND org_id=?",(rid,org)).fetchone()
            if not row:raise APIError(404,"Shift not found.","not_found")
            d=self.body();allowed={k:d[k] for k in ("employee_id","position","starts_at","ends_at","status","notes") if k in d}
            start=allowed.get("starts_at",row["starts_at"]);end=allowed.get("ends_at",row["ends_at"]);employee=allowed.get("employee_id",row["employee_id"])
            if end<=start:raise APIError(422,"Shift end must be after its start.","validation_error")
            if employee and not con.execute("SELECT 1 FROM employees WHERE id=? AND org_id=? AND location_id=? AND active=1",(employee,org,row["location_id"])).fetchone():raise APIError(422,"Choose an active team member at this location.","validation_error")
            if employee and con.execute("SELECT 1 FROM shifts WHERE org_id=? AND employee_id=? AND id!=? AND status!='cancelled' AND starts_at<? AND ends_at>?",(org,employee,rid,end,start)).fetchone():raise APIError(409,"This team member already has an overlapping shift.","shift_conflict")
            if not allowed:raise APIError(400,"No editable fields were provided.","empty_update")
            con.execute("UPDATE shifts SET "+",".join(f"{k}=?" for k in allowed)+" WHERE id=?",[*allowed.values(),rid]);self.audit(con,user,"shift",rid,"updated",allowed,row["location_id"]);return {"id":rid,"updated":True},200
        if rid and method=="DELETE":
            self.require_role(user,"owner","admin","manager");row=con.execute("SELECT location_id FROM shifts WHERE id=? AND org_id=?",(rid,org)).fetchone()
            if not row:raise APIError(404,"Shift not found.","not_found")
            con.execute("UPDATE shifts SET status='cancelled' WHERE id=?",(rid,));self.audit(con,user,"shift",rid,"cancelled",{},row[0]);return {"id":rid,"status":"cancelled"},200
        raise APIError(404,"Schedule operation not found.","not_found")

    def shift_requests(self,con,user,rid,action,method,query):
        org=user["org_id"]
        if method=="GET":
            loc=str((query.get("location_id") or [""])[0]) or None
            sql="SELECT r.*,s.position,s.starts_at,s.ends_at,e.name AS requester_name,t.name AS target_name FROM shift_requests r JOIN shifts s ON s.id=r.shift_id JOIN employees e ON e.id=r.requester_id LEFT JOIN employees t ON t.id=r.target_employee_id WHERE r.org_id=?"
            args=[org]
            if loc: sql+=" AND s.location_id=?";args.append(loc)
            sql+=" ORDER BY r.created_at DESC"
            rows=con.execute(sql,args);return {"data":[dict(r) for r in rows]},200
        if method=="POST" and not rid:
            d=self.body();shift=con.execute("SELECT * FROM shifts WHERE id=? AND org_id=?",(d.get("shift_id"),org)).fetchone();requester=d.get("requester_id");target=d.get("target_employee_id")
            if not shift or not con.execute("SELECT 1 FROM employees WHERE id=? AND org_id=?",(requester,org)).fetchone():raise APIError(422,"Choose a valid shift and team member.","validation_error")
            if target and not con.execute("SELECT 1 FROM employees WHERE id=? AND org_id=? AND location_id=? AND active=1",(target,org,shift["location_id"])).fetchone():raise APIError(422,"Choose an active teammate at this location.","validation_error")
            rid=ident();con.execute("INSERT INTO shift_requests(id,org_id,shift_id,requester_id,target_employee_id,request_type,note) VALUES(?,?,?,?,?,?,?)",(rid,org,shift["id"],requester,target,str(d.get("request_type","trade")),str(d.get("note",""))));return {"id":rid,"status":"pending"},201
        if rid and action in ("approve","reject") and method=="POST":
            self.require_role(user,"owner","admin","manager");row=con.execute("SELECT * FROM shift_requests WHERE id=? AND org_id=?",(rid,org)).fetchone()
            if not row:raise APIError(404,"Shift request not found.","not_found")
            if row["status"]!="pending":raise APIError(409,"This request has already been reviewed.","invalid_state")
            status="approved" if action=="approve" else "rejected";con.execute("UPDATE shift_requests SET status=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(status,user["id"],rid))
            if status=="approved" and row["request_type"]=="trade" and row["target_employee_id"]:con.execute("UPDATE shifts SET employee_id=? WHERE id=?",(row["target_employee_id"],row["shift_id"]))
            self.audit(con,user,"shift_request",rid,status,{});return {"id":rid,"status":status},200
        raise APIError(404,"Shift request operation not found.","not_found")

    def tasks(self,con,user,rid,method,query):
        org=user["org_id"]
        if not rid and method=="GET":
            loc=(query.get("location_id") or [""])[0];sql="SELECT t.*,e.name AS assigned_name FROM tasks t LEFT JOIN employees e ON e.id=t.assigned_to WHERE t.org_id=?";args=[org]
            if loc:sql+=" AND t.location_id=?";args.append(loc)
            sql+=" ORDER BY CASE t.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,CASE t.priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,t.due_at";rows=con.execute(sql,args);return {"data":[dict(r) for r in rows]},200
        if not rid and method=="POST":
            self.require_role(user,"owner","admin","manager");d=self.body();title=self.required(d,"title");loc=d.get("location_id") or self.default_location(con,org);assigned=d.get("assigned_to") or None
            if not con.execute("SELECT 1 FROM locations WHERE id=? AND org_id=?",(loc,org)).fetchone():raise APIError(422,"Choose a location in this workspace.","validation_error")
            if assigned and not con.execute("SELECT 1 FROM employees WHERE id=? AND org_id=? AND location_id=? AND active=1",(assigned,org,loc)).fetchone():raise APIError(422,"Choose an active team member at this location.","validation_error")
            tid=ident();con.execute("INSERT INTO tasks(id,org_id,location_id,title,description,assigned_to,due_at,priority,created_by) VALUES(?,?,?,?,?,?,?,?,?)",(tid,org,loc,title,str(d.get("description","")),assigned,d.get("due_at"),str(d.get("priority","normal")),user["id"]));self.audit(con,user,"task",tid,"created",{"title":title},loc);return {"id":tid,"status":"open"},201
        if rid and method=="PATCH":
            row=con.execute("SELECT * FROM tasks WHERE id=? AND org_id=?",(rid,org)).fetchone()
            if not row:raise APIError(404,"Task not found.","not_found")
            d=self.body();allowed={k:d[k] for k in ("title","description","assigned_to","due_at","priority","status") if k in d}
            assigned=allowed.get("assigned_to",row["assigned_to"])
            if assigned and not con.execute("SELECT 1 FROM employees WHERE id=? AND org_id=? AND location_id=? AND active=1",(assigned,org,row["location_id"])).fetchone():raise APIError(422,"Choose an active team member at this location.","validation_error")
            if "status" in allowed and allowed["status"] not in ("open","in_progress","done","cancelled"):raise APIError(422,"Choose a valid task status.","validation_error")
            if not allowed:raise APIError(400,"No editable fields were provided.","empty_update")
            if allowed.get("status")=="done":con.execute("UPDATE tasks SET "+",".join(f"{k}=?" for k in allowed)+",completed_at=CURRENT_TIMESTAMP WHERE id=?",[*allowed.values(),rid])
            else:con.execute("UPDATE tasks SET "+",".join(f"{k}=?" for k in allowed)+" WHERE id=?",[*allowed.values(),rid])
            self.audit(con,user,"task",rid,"updated",allowed,row["location_id"]);return {"id":rid,"updated":True},200
        if rid and method=="DELETE":
            self.require_role(user,"owner","admin","manager");con.execute("DELETE FROM tasks WHERE id=? AND org_id=?",(rid,org));return {"id":rid,"deleted":True},200
        raise APIError(404,"Task operation not found.","not_found")

    def sales(self,con,user,rid,action,method,query):
        org=user["org_id"]
        if method=="GET":
            location=str((query.get("location_id") or [""])[0]) or self.default_location(con,org);start=(query.get("from") or [""])[0];end=(query.get("to") or [""])[0];sql="SELECT * FROM sales_entries WHERE org_id=? AND location_id=?";args=[org,location]
            if start:sql+=" AND business_date>=?";args.append(start)
            if end:sql+=" AND business_date<=?";args.append(end)
            sql+=" ORDER BY business_date DESC,channel";rows=con.execute(sql,args);return {"data":[dict(r) for r in rows]},200
        if method=="POST":
            d=self.body();loc=d.get("location_id") or self.default_location(con,org)
            if not con.execute("SELECT 1 FROM locations WHERE id=? AND org_id=?",(loc,org)).fetchone():raise APIError(422,"Choose a location in this workspace.","validation_error")
            if getattr(self,"is_api_key",False) and ("sales:write" not in self.api_permissions or method!="POST"):raise APIError(403,"This API key cannot perform that operation.","forbidden")
            date=self.required(d,"business_date");sales=self.number(d,"net_sales",0);covers=int(self.number(d,"covers",0)) if "covers" in d else 0;food=self.number(d,"food_cost",0) if "food_cost" in d else 0;labor=self.number(d,"labor_cost",0) if "labor_cost" in d else 0;channel=str(d.get("channel","in_store"));source="integration" if getattr(self,"is_api_key",False) else str(d.get("source","manual"));external=d.get("external_id")
            if source in ("integration","pos") and not external:raise APIError(422,"Provide external_id for idempotent POS ingestion.","validation_error",{"external_id":"Required for integrations."})
            sid=ident()
            if external:
                con.execute("INSERT INTO sales_entries(id,org_id,location_id,business_date,channel,net_sales,covers,food_cost,labor_cost,source,external_id) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(org_id,location_id,business_date,channel,external_id) DO UPDATE SET net_sales=excluded.net_sales,covers=excluded.covers,food_cost=excluded.food_cost,labor_cost=excluded.labor_cost,source=excluded.source",(sid,org,loc,date,channel,sales,covers,food,labor,source,str(external)))
                row=con.execute("SELECT id FROM sales_entries WHERE org_id=? AND location_id=? AND business_date=? AND channel=? AND external_id=?",(org,loc,date,channel,str(external))).fetchone();sid=row[0]
            else:con.execute("INSERT INTO sales_entries(id,org_id,location_id,business_date,channel,net_sales,covers,food_cost,labor_cost,source) VALUES(?,?,?,?,?,?,?,?,?,?)",(sid,org,loc,date,channel,sales,covers,food,labor,source))
            self.audit(con,user,"sales_entry",sid,"ingested",{"business_date":date,"net_sales":sales},loc);return {"id":sid,"upserted":bool(external)},201
        raise APIError(405,"Sales operation not found.","not_found")

    def reports(self,con,user,action,method,query):
        if method!="GET":raise APIError(405,"Reports are read-only.","method_not_allowed")
        org=user["org_id"];location=str((query.get("location_id") or [""])[0]) or self.default_location(con,org)
        if not con.execute("SELECT 1 FROM locations WHERE id=? AND org_id=?",(location,org)).fetchone():raise APIError(404,"Location not found.","not_found")
        end=(query.get("to") or [time.strftime('%Y-%m-%d')])[0];start=(query.get("from") or [""])[0]
        if not start:
            from datetime import date,timedelta
            start=(date.fromisoformat(end)-timedelta(days=6)).isoformat()
        rows=con.execute("SELECT business_date,SUM(net_sales) AS net_sales,SUM(covers) AS covers,SUM(food_cost) AS food_cost,SUM(labor_cost) AS labor_cost FROM sales_entries WHERE org_id=? AND location_id=? AND business_date BETWEEN ? AND ? GROUP BY business_date ORDER BY business_date",(org,location,start,end)).fetchall()
        totals={k:sum(float(r[k] or 0) for r in rows) for k in ("net_sales","covers","food_cost","labor_cost")};sales=totals["net_sales"]
        totals.update({"food_cost_pct":totals["food_cost"]/sales if sales else 0,"labor_cost_pct":totals["labor_cost"]/sales if sales else 0,"prime_cost_pct":(totals["food_cost"]+totals["labor_cost"])/sales if sales else 0,"average_check":sales/totals["covers"] if totals["covers"] else 0})
        low=con.execute("SELECT i.id,i.name,i.unit,i.par,COALESCE(l.on_hand,0) AS on_hand FROM items i LEFT JOIN inventory_levels l ON l.item_id=i.id AND l.location_id=? WHERE i.org_id=? AND i.active=1 AND COALESCE(l.on_hand,0)<i.par ORDER BY (COALESCE(l.on_hand,0)-i.par),i.name",(location,org)).fetchall()
        waste=con.execute("SELECT COALESCE(SUM(w.quantity*i.cost),0) AS cost,COUNT(*) AS entries FROM waste_logs w JOIN items i ON i.id=w.item_id WHERE w.org_id=? AND w.location_id=? AND date(w.created_at) BETWEEN ? AND ?",(org,location,start,end)).fetchone()
        return {"period":{"from":start,"to":end},"totals":totals,"daily":[dict(r) for r in rows],"low_stock":[dict(r) for r in low],"waste":{"cost":float(waste["cost"]),"entries":int(waste["entries"])}},200

    def channels(self,con,user,rid,action,method,query):
        org=user["org_id"]
        if not rid and method=="GET":
            loc=str((query.get("location_id") or [""])[0]) or self.default_location(con,org);rows=con.execute("SELECT c.*,(SELECT COUNT(*) FROM messages m WHERE m.channel_id=c.id) AS message_count FROM channels c WHERE c.org_id=? AND c.location_id=? ORDER BY c.name",(org,loc));return {"data":[dict(r) for r in rows]},200
        if rid and action=="messages" and method=="GET":
            channel=con.execute("SELECT id FROM channels WHERE id=? AND org_id=?",(rid,org)).fetchone()
            if not channel:raise APIError(404,"Channel not found.","not_found")
            rows=con.execute("SELECT m.*,u.display_name AS author FROM messages m JOIN users u ON u.id=m.user_id WHERE m.channel_id=? ORDER BY m.created_at DESC LIMIT 100",(rid,)).fetchall();return {"data":[dict(r) for r in reversed(rows)]},200
        if rid and action=="messages" and method=="POST":
            channel=con.execute("SELECT id FROM channels WHERE id=? AND org_id=?",(rid,org)).fetchone()
            if not channel:raise APIError(404,"Channel not found.","not_found")
            body=self.required(self.body(),"body")
            if len(body)>4000:raise APIError(422,"Message must be 4,000 characters or fewer.","validation_error")
            mid=ident();con.execute("INSERT INTO messages(id,org_id,channel_id,user_id,body) VALUES(?,?,?,?,?)",(mid,org,rid,user["id"],body));return {"id":mid,"body":body},201
        if not rid and method=="POST":
            d=self.body();name=self.required(d,"name");loc=d.get("location_id") or self.default_location(con,org)
            if not con.execute("SELECT 1 FROM locations WHERE id=? AND org_id=?",(loc,org)).fetchone():raise APIError(422,"Choose a location in this workspace.","validation_error")
            cid=ident();con.execute("INSERT INTO channels(id,org_id,location_id,name) VALUES(?,?,?,?)",(cid,org,loc,name));return {"id":cid,"name":name},201
        raise APIError(404,"Channel operation not found.","not_found")

    def recipes(self,con,user,rid,method):
        org=user["org_id"]
        if not rid and method=="GET":
            rows=con.execute("SELECT * FROM recipes WHERE org_id=? AND active=1 ORDER BY name",(org,)).fetchall();result=[]
            for recipe in rows:
                lines=con.execute("SELECT ri.*,i.name AS item_name,i.cost AS unit_cost FROM recipe_ingredients ri JOIN items i ON i.id=ri.item_id WHERE ri.recipe_id=?",(recipe["id"],)).fetchall();cost=sum(float(x["quantity"])*float(x["unit_cost"]) for x in lines)/max(float(recipe["servings"]),0.000001);price=float(recipe["menu_price"]);result.append({**dict(recipe),"ingredients":[dict(x) for x in lines],"plate_cost":cost,"food_cost_pct":cost/price if price else 0,"gross_margin":price-cost})
            return {"data":result},200
        if not rid and method=="POST":
            self.require_role(user,"owner","admin","manager");d=self.body();name=self.required(d,"name");price=self.number(d,"menu_price",0) if "menu_price" in d else 0;servings=self.number(d,"servings",0.000001) if "servings" in d else 1;lines=d.get("ingredients",[])
            if not lines:raise APIError(422,"Add at least one ingredient.","validation_error")
            recipe=ident();con.execute("INSERT INTO recipes(id,org_id,name,menu_price,servings) VALUES(?,?,?,?,?)",(recipe,org,name,price,servings))
            for line in lines:
                item=self.scoped_item(con,org,line.get("item_id"));qty=self.number(line,"quantity",0.000001);con.execute("INSERT INTO recipe_ingredients(id,recipe_id,item_id,quantity,unit) VALUES(?,?,?,?,?)",(ident(),recipe,item["id"],qty,str(line.get("unit",item["unit"]))))
            self.audit(con,user,"recipe",recipe,"created",{"name":name});return {"id":recipe,"name":name},201
        if rid and method=="DELETE":
            self.require_role(user,"owner","admin");row=con.execute("SELECT id FROM recipes WHERE id=? AND org_id=?",(rid,org)).fetchone()
            if not row:raise APIError(404,"Recipe not found.","not_found")
            con.execute("UPDATE recipes SET active=0 WHERE id=?",(rid,));self.audit(con,user,"recipe",rid,"archived");return {"id":rid,"archived":True},200
        raise APIError(404,"Recipe operation not found.","not_found")

    def time_entries(self,con,user,rid,method,query):
        org=user["org_id"]
        if not rid and method=="GET":
            start=(query.get("from") or [""])[0];end=(query.get("to") or [""])[0];sql="SELECT t.*,e.name AS employee_name,e.department,s.position FROM time_entries t JOIN employees e ON e.id=t.employee_id LEFT JOIN shifts s ON s.id=t.shift_id WHERE t.org_id=?";args=[org]
            location=str((query.get("location_id") or [""])[0]) or None
            if location:sql+=" AND t.location_id=?";args.append(location)
            if start:sql+=" AND date(t.clock_in)>=?";args.append(start)
            if end:sql+=" AND date(t.clock_in)<=?";args.append(end)
            sql+=" ORDER BY t.clock_in DESC";return {"data":[dict(x) for x in con.execute(sql,args)]},200
        if rid=="clock-in" and method=="POST":
            self.require_role(user,"owner","admin","manager");d=self.body();employee=d.get("employee_id");e=con.execute("SELECT * FROM employees WHERE id=? AND org_id=? AND active=1",(employee,org)).fetchone()
            if not e:raise APIError(422,"Choose an active team member.","validation_error")
            if con.execute("SELECT 1 FROM time_entries WHERE employee_id=? AND org_id=? AND clock_out IS NULL",(employee,org)).fetchone():raise APIError(409,"This team member is already clocked in.","already_clocked_in")
            shift=con.execute("SELECT id FROM shifts WHERE employee_id=? AND org_id=? AND date(starts_at)=date('now') AND status!='cancelled' ORDER BY starts_at LIMIT 1",(employee,org)).fetchone();entry=ident();con.execute("INSERT INTO time_entries(id,org_id,location_id,employee_id,shift_id,notes) VALUES(?,?,?,?,?,?)",(entry,org,e["location_id"],employee,shift[0] if shift else None,str(d.get("notes",""))));self.audit(con,user,"time_entry",entry,"clocked_in",{"employee":e["name"]},e["location_id"]);return {"id":entry,"status":"clocked_in"},201
        if rid and method=="PATCH":
            self.require_role(user,"owner","admin","manager");d=self.body();entry=con.execute("SELECT * FROM time_entries WHERE id=? AND org_id=?",(rid,org)).fetchone()
            if not entry:raise APIError(404,"Time entry not found.","not_found")
            if entry["clock_out"]:raise APIError(409,"This time entry is already closed.","invalid_state")
            con.execute("UPDATE time_entries SET clock_out=CURRENT_TIMESTAMP,status='clocked_out',reviewed_by=? WHERE id=?",(user["id"],rid));self.audit(con,user,"time_entry",rid,"clocked_out",{},entry["location_id"]);return {"id":rid,"status":"clocked_out"},200
        if rid=="clock-out" and method=="POST":
            self.require_role(user,"owner","admin","manager");d=self.body();entry=con.execute("SELECT * FROM time_entries WHERE id=? AND org_id=? AND clock_out IS NULL",(d.get("entry_id"),org)).fetchone()
            if not entry:raise APIError(404,"Open time entry not found.","not_found")
            con.execute("UPDATE time_entries SET clock_out=CURRENT_TIMESTAMP,status='clocked_out',reviewed_by=? WHERE id=?",(user["id"],entry["id"]));self.audit(con,user,"time_entry",entry["id"],"clocked_out",{},entry["location_id"]);return {"id":entry["id"],"status":"clocked_out"},200
        raise APIError(404,"Time clock operation not found.","not_found")

    def integration_keys(self,con,user,rid,method):
        org=user["org_id"]
        if not rid and method=="GET":
            self.require_role(user,"owner","admin");rows=con.execute("SELECT id,name,permissions,created_at,last_used_at,revoked_at FROM api_keys WHERE org_id=? ORDER BY created_at DESC",(org,));return {"data":[dict(r) for r in rows]},200
        if not rid and method=="POST":
            self.require_role(user,"owner","admin");d=self.body();name=self.required(d,"name");permissions=d.get("permissions",["sales:write"])
            if not isinstance(permissions,list) or not permissions or any(p not in ("sales:write",) for p in permissions):raise APIError(422,"Unsupported API key permission.","validation_error")
            token="ck_live_"+secrets.token_urlsafe(36);import hashlib;kid=ident();con.execute("INSERT INTO api_keys(id,org_id,user_id,name,key_hash,permissions) VALUES(?,?,?,?,?,?)",(kid,org,user["id"],name,hashlib.sha256(token.encode()).hexdigest(),",".join(permissions)));self.audit(con,user,"api_key",kid,"created",{"name":name});return {"id":kid,"name":name,"key":token,"permissions":permissions,"warning":"Copy this key now. It will not be shown again."},201
        if rid and method=="DELETE":
            self.require_role(user,"owner","admin");row=con.execute("SELECT id FROM api_keys WHERE id=? AND org_id=? AND revoked_at IS NULL",(rid,org)).fetchone()
            if not row:raise APIError(404,"Active API key not found.","not_found")
            con.execute("UPDATE api_keys SET revoked_at=CURRENT_TIMESTAMP WHERE id=?",(rid,));self.audit(con,user,"api_key",rid,"revoked");return {"id":rid,"revoked":True},200
        raise APIError(404,"API key operation not found.","not_found")

    def sous(self,con,user):
        d=self.body();question=self.required(d,"question").lower();org=user["org_id"];loc=d.get("location_id") or self.default_location(con,org)
        if not con.execute("SELECT 1 FROM locations WHERE id=? AND org_id=?",(loc,org)).fetchone():raise APIError(404,"Location not found.","not_found")
        from datetime import date,timedelta
        today=date.today();start=(today-timedelta(days=6)).isoformat();end=today.isoformat()
        report, _=self.reports(con,user,"summary","GET",{"location_id":[loc],"from":[start],"to":[end]})
        if "inventory" in question or "86" in question or "stock" in question:
            data=report["low_stock"];answer=(f"{len(data)} items are below par at this location." if data else "No items are below par at this location.");evidence=data[:8]
        elif "labor" in question or "staff" in question or "schedule" in question:
            rows=con.execute("SELECT date(starts_at) AS day,COUNT(*) AS shifts,ROUND(SUM((julianday(ends_at)-julianday(starts_at))*24),1) AS hours FROM shifts WHERE org_id=? AND location_id=? AND status!='cancelled' AND date(starts_at) BETWEEN ? AND ? GROUP BY date(starts_at) ORDER BY day",(org,loc,start,end)).fetchall();answer=f"There are {sum(r['shifts'] for r in rows)} scheduled shifts and {sum(float(r['hours'] or 0) for r in rows):.1f} scheduled hours this week.";evidence=[dict(r) for r in rows]
        elif "waste" in question:
            answer=f"Recorded waste in the last seven days is ${report['waste']['cost']:.2f} across {report['waste']['entries']} entries.";evidence=report["waste"]
        elif "food cost" in question or "prime cost" in question or "sales" in question:
            t=report["totals"];answer=f"For {start} through {end}, net sales are ${t['net_sales']:,.2f}, food cost is {t['food_cost_pct']:.1%}, labor is {t['labor_cost_pct']:.1%}, and prime cost is {t['prime_cost_pct']:.1%}.";evidence=t
        else:answer="I can answer questions about sales, food cost, prime cost, labor, schedule coverage, inventory, 86 risk, and recorded waste from this workspace.";evidence={"supported_topics":["sales","food cost","prime cost","labor","schedule","inventory","86 risk","waste"]}
        return {"answer":answer,"evidence":evidence,"period":{"from":start,"to":end},"mode":"deterministic_workspace_query"},200

    def do_POST(self):
        if urlsplit(self.path).path=="/api/v1/sous":
            try:
                con=db()
                try:
                    user,csrf=self.authenticate(con)
                    if not secrets.compare_digest(self.headers.get("X-CSRF-Token",""),csrf):raise APIError(403,"Missing or invalid CSRF token.","csrf_failed")
                    result,status=self.sous(con,user);con.commit();self.json(status,result)
                finally:con.close()
            except APIError as exc:self.json(exc.status,{"error":{"code":exc.code,"message":exc.message,"fields":exc.fields}})
            return
        self.handle_api()


if __name__ == "__main__":
    initialize()
    host=os.environ.get("CHEFOS_HOST","127.0.0.1");port=int(os.environ.get("PORT","8000"))
    server=ThreadingHTTPServer((host,port),ChefOSHandler)
    print(f"ChefOS running at http://{host}:{port}")
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
