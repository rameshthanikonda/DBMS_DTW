# NEW: Import jsonify
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, jsonify, Response
import cx_Oracle
import os
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import date, datetime
from dateutil.relativedelta import relativedelta # For accurate date math
from functools import wraps
import io
import csv
import smtplib
import ssl
from email.message import EmailMessage
import threading
import time

# --- App Configuration ---
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev_secret_key")
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'png', 'jpg', 'jpeg'}
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER or "")

# --- Database Connection ---
try:
    dsn = cx_Oracle.makedsn(os.getenv("DB_HOST"), os.getenv("DB_PORT"), service_name=os.getenv("DB_SERVICE"))
    conn = cx_Oracle.connect(user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"), dsn=dsn)
    print("✅ Oracle DB Connected:", conn.version)
    # Ensure unique constraint for duplicates: (user_id, lower(product_name), lower(nvl(brand,'')))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE UNIQUE INDEX ux_warranties_user_prod_brand
            ON warranties (user_id, LOWER(product_name), LOWER(NVL(brand,'')))
            """
        )
        conn.commit()
        print("✅ Created unique index ux_warranties_user_prod_brand")
    except Exception as ie:
        # ORA-00955 name is already used by an existing object, or table may not exist yet in some envs
        print(f"Index ensure note: {ie}")
    finally:
        if 'cur' in locals() and cur: cur.close()
    # Ensure uniqueness to prevent duplicate notification messages for the same warranty
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE UNIQUE INDEX ux_notifications_user_warranty_message
            ON notifications (user_id, warranty_id, message)
            """
        )
        conn.commit()
        print("✅ Created unique index ux_notifications_user_warranty_message")
    except Exception as ie:
        print(f"Notification index ensure note: {ie}")
    finally:
        if 'cur' in locals() and cur: cur.close()
except Exception as e:
    print(f"❌ Database connection failed: {e}")
    conn = None

# NEW: This function runs on every page load to get the unread notification count for the bell icon.
@app.context_processor
def inject_notification_count():
    if 'user_id' not in session:
        return dict(unread_count=0)
    try:
        cur = conn.cursor()
        sql_count = f"SELECT COUNT(*) FROM notifications WHERE user_id = {int(session['user_id'])} AND status = 'Unread'"
        cur.execute(sql_count)
        count = cur.fetchone()[0]
        return dict(unread_count=count)
    except Exception as e:
        print(f"Error fetching notification count: {e}")
        return dict(unread_count=0)
    finally:
        if 'cur' in locals() and cur: cur.close()

# --- Helper Functions ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("🔒 Please log in to access this page.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Email/notification helpers (inlined)
def send_email(to_email, subject, body):
    try:
        if not (SMTP_USER and SMTP_PASS and SMTP_FROM and to_email):
            return False
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to_email
        # Plain text fallback
        msg.set_content(body)

        # Simple HTML version with color accents
        lower = (body or "").lower()
        tag_label = "Notice"
        tag_bg = "#eef2ff"  # indigo-50
        tag_fg = "#4338ca"  # indigo-700
        if "has expired" in lower:
            tag_label = "Expired"
            tag_bg = "#fee2e2"  # red-100
            tag_fg = "#b91c1c"  # red-700
        elif "expires on" in lower or "expiring" in lower:
            tag_label = "Expiring"
            tag_bg = "#fef3c7"  # amber-100
            tag_fg = "#b45309"  # amber-700

        html = f"""
        <html>
          <body style="font-family:Inter,Segoe UI,Arial,sans-serif;background:#0b1220;padding:24px;">
            <div style="max-width:600px;margin:0 auto;background:#101827;border:1px solid #1f2a44;border-radius:12px;padding:24px;color:#e5e7eb;">
              <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;">
                <span style="display:inline-block;padding:6px 12px;border-radius:999px;background:{tag_bg};color:{tag_fg};font-weight:700;font-size:12px;">{tag_label}</span>
              </div>
              <h2 style="margin:0 0 8px 0;font-size:18px;color:#ffffff;">{subject}</h2>
              <p style="margin:0 0 14px 0;line-height:1.5;color:#cbd5e1;">{body}</p>
              <hr style="border:none;border-top:1px solid #1f2a44;margin:18px 0;" />
              <p style="margin:0;color:#9aa7bd;font-size:12px;">This is an automated message from Warracker.</p>
            </div>
          </body>
        </html>
        """
        msg.add_alternative(html, subtype="html")

        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Email send failed: {e}")
        return False

def create_notification(user_id, warranty_id, message, email_subject=None, send_email_now=True):
    cur = None
    try:
        cur = conn.cursor()
        sql_chk = f"SELECT COUNT(*) FROM notifications WHERE user_id = {int(user_id)} AND warranty_id = {int(warranty_id)} AND message = :msg"
        cur.execute(sql_chk, {"msg": message})
        exists = cur.fetchone()[0]
        if exists == 0:
            cur.execute(
                f"INSERT INTO notifications (user_id, warranty_id, message) VALUES ({int(user_id)}, {int(warranty_id)}, :msg)",
                {"msg": message}
            )
            conn.commit()
        if send_email_now:
            cur.execute("SELECT email FROM users WHERE user_id = :1", (user_id,))
            row = cur.fetchone()
            to_email = row[0] if row else None
            subject = email_subject or "Warracker Notification"
            send_email(to_email, subject, message)
    except Exception as e:
        try:
            print("create_notification failed")
            print("Params:", {"user_id": user_id, "warranty_id": warranty_id, "message": message})
        except Exception:
            pass
        print(f"create_notification error: {e}")
    finally:
        if cur:
            cur.close()

def run_cadence_warranty_notifications():
    """Send warranty notifications with cadence rules:
    - Expiring in 7 days or less: send daily
    - Expiring in 8–30 days: send weekly (on Monday)
    - Expired: send daily for 7 days after expiry
    """
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.user_id, w.warranty_id, w.product_name, TRUNC(w.expiry_date)
            FROM warranties w
            JOIN users u ON w.user_id = u.user_id
            WHERE w.expiry_date <= TRUNC(SYSDATE) + 30
            """
        )
        rows = cur.fetchall()
        from datetime import date as _d
        today = _d.today()
        weekday = today.weekday()  # Monday=0

        for uid, wid, pname, exp_dt in rows:
            exp_date = exp_dt.date()
            days_until = (exp_date - today).days
            should_send = False
            cadence_tag = "Notice"
            if days_until < 0:
                # expired: send daily for 7 days
                if abs(days_until) <= 7:
                    should_send = True
                    cadence_tag = "Expired"
            elif days_until <= 7:
                # within a week: daily
                should_send = True
                cadence_tag = "Expiring"
            elif days_until <= 30:
                # 8–30 days: weekly (Monday)
                if weekday == 0:
                    should_send = True
                    cadence_tag = "Expiring"

            if not should_send:
                continue

            if days_until < 0:
                msg = f"Your warranty for '{pname}' has expired on {exp_date.strftime('%B %d, %Y')}."
                subject = "🔴 Warracker • Warranty Expired"
            else:
                msg = f"Your warranty for '{pname}' expires on {exp_date.strftime('%B %d, %Y')}."
                # Distinguish weekly vs daily subtly in subject
                subject = "🟡 Warracker • Warranty Reminder"

            # Send email immediately; notification record is inserted once due to unique index
            create_notification(int(uid), int(wid), msg, email_subject=subject, send_email_now=True)
    except Exception as e:
        print(f"run_cadence_warranty_notifications error: {e}")
    finally:
        if cur:
            cur.close()

def run_batch_warranty_notifications(days=7):
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.user_id, w.warranty_id, w.product_name, w.expiry_date
            FROM warranties w
            JOIN users u ON w.user_id = u.user_id
            WHERE (
                w.expiry_date < SYSDATE OR
                w.expiry_date BETWEEN TRUNC(SYSDATE) AND TRUNC(SYSDATE) + :1
            )
            """,
            (int(days),)
        )
        rows = cur.fetchall()
        from datetime import date as _d
        today = _d.today()
        for uid, wid, pname, exp_dt in rows:
            exp_date = exp_dt.date()
            if exp_date < today:
                msg = f"Your warranty for '{pname}' has expired on {exp_date.strftime('%B %d, %Y')}."
            else:
                msg = f"Your warranty for '{pname}' expires on {exp_date.strftime('%B %d, %Y')}."
            create_notification(int(uid), int(wid), msg, email_subject="Warranty Reminder", send_email_now=True)
    except Exception as e:
        print(f"run_batch_warranty_notifications error: {e}")
    finally:
        if cur:
            cur.close()

def generate_warranty_notifications(user_id, days=7, send_email_now=False):
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT warranty_id, product_name, expiry_date
            FROM warranties
            WHERE user_id = :1
              AND (
                    expiry_date < SYSDATE
                 OR expiry_date BETWEEN TRUNC(SYSDATE) AND TRUNC(SYSDATE) + :2
              )
            """,
            (int(user_id), int(days))
        )
        rows = cur.fetchall()
        from datetime import date as _d
        today = _d.today()
        for w_id, product_name, exp_dt in rows:
            exp_date = exp_dt.date()
            if exp_date < today:
                msg = f"Your warranty for '{product_name}' has expired on {exp_date.strftime('%B %d, %Y')}."
            else:
                msg = f"Your warranty for '{product_name}' expires on {exp_date.strftime('%B %d, %Y')}."
            create_notification(user_id, int(w_id), msg, email_subject="Warranty Reminder", send_email_now=send_email_now)
    except Exception as e:
        print(f"generate_warranty_notifications error: {e}")
    finally:
        if cur:
            cur.close()

def _scheduler_loop():
    while True:
        try:
            # Cadence-based notifications
            run_cadence_warranty_notifications()
        except Exception as e:
            print(f"Scheduler loop error: {e}")
        time.sleep(86400)

def start_email_scheduler_if_enabled():
    # Avoid double-start under the Flask reloader
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    t = threading.Thread(target=_scheduler_loop, name="warranty-email-scheduler", daemon=True)
    t.start()

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            flash("Please log in as admin.", "warning")
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# Pagination helper for list pages
def _get_page_and_size():
    try:
        page = int(request.args.get('page', '1'))
        size = int(request.args.get('size', '10'))
    except ValueError:
        page, size = 1, 10
    page = max(1, page)
    size = min(max(5, size), 100)
    offset = (page - 1) * size
    return page, size, offset

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        if not new_password or new_password != confirm_password:
            flash("❌ Passwords do not match.", "danger")
            return redirect(url_for('change_password'))
        try:
            cur = conn.cursor()
            cur.execute("SELECT password FROM users WHERE user_id = :1", (session['user_id'],))
            row = cur.fetchone()
            if not row or not check_password_hash(row[0], old_password):
                flash("❌ Current password is incorrect.", "danger")
                return redirect(url_for('change_password'))
            cur.execute("UPDATE users SET password = :1 WHERE user_id = :2", (generate_password_hash(new_password), session['user_id']))
            conn.commit()
            flash("✅ Password changed successfully.", "success")
            return redirect(url_for('profile'))
        except Exception as e:
            flash(f"❌ Error changing password: {e}", "danger")
        finally:
            if 'cur' in locals() and cur: cur.close()
    return render_template('change_password.html')

@app.route('/warranty/<int:warranty_id>')
@login_required
def warranty_detail(warranty_id):
    warranty = None
    claims = []
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT warranty_id, product_name, brand, purchase_date, expiry_date, invoice_path
            FROM warranties
            WHERE warranty_id = :1 AND user_id = :2
            """,
            (warranty_id, session['user_id'])
        )
        row = cur.fetchone()
        if not row:
            flash("❌ Warranty not found.", "danger")
            return redirect(url_for('my_warranties'))
        warranty = {
            'id': row[0],
            'product_name': row[1],
            'brand': row[2],
            'purchase_date': row[3].strftime('%Y-%m-%d'),
            'expiry_date': row[4].strftime('%Y-%m-%d'),
            'status': "Active" if row[4].date() >= date.today() else "Expired",
            'invoice_path': row[5],
        }
        cur.execute(
            """
            SELECT claim_id, claim_date, description, status
            FROM service_claims
            WHERE warranty_id = :1
            ORDER BY claim_date DESC
            """,
            (warranty_id,)
        )
        for r in cur.fetchall():
            claims.append({
                'claim_id': r[0],
                'claim_date': r[1].strftime('%Y-%m-%d') if r[1] else '',
                'description': r[2],
                'status': r[3]
            })
    except Exception as e:
        flash(f"❌ Error loading warranty: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return render_template('warranty_detail.html', warranty=warranty, claims=claims)

@app.route('/expiring')
@login_required
def expiring():
    try:
        days = int(request.args.get('days', '30'))
    except ValueError:
        days = 30
    items = []
    try:
        cur = conn.cursor()
        # Oracle can add numeric days to SYSDATE
        cur.execute(
            """
            SELECT warranty_id, product_name, brand, purchase_date, expiry_date
            FROM warranties
            WHERE user_id = :1 AND expiry_date BETWEEN SYSDATE AND SYSDATE + :2
            ORDER BY expiry_date ASC
            """,
            (session['user_id'], days)
        )
        for row in cur.fetchall():
            items.append({
                'id': row[0],
                'product_name': row[1],
                'brand': row[2],
                'purchase_date': row[3].strftime('%Y-%m-%d'),
                'expiry_date': row[4].strftime('%Y-%m-%d'),
                'status': 'Active' if row[4].date() >= date.today() else 'Expired'
            })
    except Exception as e:
        flash(f"❌ Error loading expiring warranties: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return render_template('expiring.html', items=items, days=days)

@app.route('/admin/warranties')
@admin_required
def admin_warranties():
    q = request.args.get('q')
    status = request.args.get('status')  # Active / Expired
    rows = []
    page, size, offset = _get_page_and_size()
    try:
        cur = conn.cursor()
        base_sql = (
            "SELECT w.warranty_id, u.full_name, w.product_name, w.brand, w.purchase_date, w.expiry_date, "
            "CASE WHEN w.expiry_date >= SYSDATE THEN 'Active' ELSE 'Expired' END AS status "
            "FROM warranties w JOIN users u ON w.user_id = u.user_id WHERE 1=1"
        )
        params = []
        if q:
            base_sql += " AND (LOWER(w.product_name) LIKE :q OR LOWER(NVL(w.brand,'')) LIKE :q OR LOWER(u.full_name) LIKE :q)"
            params.append(f"%{q.lower()}%")
        if status in ("Active", "Expired"):
            base_sql += " AND (CASE WHEN w.expiry_date >= SYSDATE THEN 'Active' ELSE 'Expired' END) = :st"
            params.append(status)
        base_sql += " ORDER BY w.expiry_date ASC OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY"
        bind = {}
        # Map params by name in order appended above
        # jinja building used :q then :st, so fill accordingly
        if q:
            bind['q'] = f"%{q.lower()}%"
        if status in ("Active", "Expired"):
            bind['st'] = status
        bind['off'] = offset
        bind['lim'] = size
        cur.execute(base_sql, bind)
        for r in cur.fetchall():
            rows.append({
                'warranty_id': r[0],
                'user_name': r[1],
                'product_name': r[2],
                'brand': r[3],
                'purchase_date': r[4].strftime('%Y-%m-%d'),
                'expiry_date': r[5].strftime('%Y-%m-%d'),
                'status': r[6]
            })
    except Exception as e:
        flash(f"❌ Error loading warranties: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return render_template('admin_warranties.html', warranties=rows, current_status=status, q=q, page=page, size=size)

def _csv_response(headers, rows):
    sio = io.StringIO()
    writer = csv.writer(sio)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    data = sio.getvalue()
    sio.close()
    return Response(data, mimetype='text/csv; charset=utf-8')

@app.route('/export/my_warranties')
@login_required
def export_my_warranties():
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT product_name, NVL(brand,''), purchase_date, expiry_date FROM warranties WHERE user_id = :1 ORDER BY expiry_date",
            (session['user_id'],)
        )
        rows = [(r[0], r[1], r[2].strftime('%Y-%m-%d'), r[3].strftime('%Y-%m-%d')) for r in cur.fetchall()]
        return _csv_response(["Product Name", "Brand", "Purchase Date", "Expiry Date"], rows)
    except Exception as e:
        flash(f"❌ Export failed: {e}", "danger")
        return redirect(url_for('my_warranties'))
    finally:
        if 'cur' in locals() and cur: cur.close()

@app.route('/admin/export/warranties')
@admin_required
def admin_export_warranties():
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.full_name, w.product_name, NVL(w.brand,''), w.purchase_date, w.expiry_date,
                   CASE WHEN w.expiry_date >= SYSDATE THEN 'Active' ELSE 'Expired' END
            FROM warranties w JOIN users u ON w.user_id = u.user_id
            ORDER BY w.expiry_date
            """
        )
        rows = [(r[0], r[1], r[2], r[3].strftime('%Y-%m-%d'), r[4].strftime('%Y-%m-%d'), r[5]) for r in cur.fetchall()]
        return _csv_response(["User", "Product", "Brand", "Purchase Date", "Expiry Date", "Status"], rows)
    except Exception as e:
        flash(f"❌ Export failed: {e}", "danger")
        return redirect(url_for('admin_warranties'))
    finally:
        if 'cur' in locals() and cur: cur.close()

@app.route('/admin/export/claims')
@admin_required
def admin_export_claims():
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.full_name, w.product_name, c.claim_date, c.status, c.description
            FROM service_claims c
            JOIN warranties w ON c.warranty_id = w.warranty_id
            JOIN users u ON w.user_id = u.user_id
            ORDER BY c.claim_date DESC
            """
        )
        rows = [(r[0], r[1], r[2].strftime('%Y-%m-%d') if r[2] else '', r[3], r[4]) for r in cur.fetchall()]
        return _csv_response(["User", "Product", "Claim Date", "Status", "Description"], rows)
    except Exception as e:
        flash(f"❌ Export failed: {e}", "danger")
        return redirect(url_for('admin_claims'))
    finally:
        if 'cur' in locals() and cur: cur.close()

@app.route('/admin/export/products')
@admin_required
def admin_export_products():
    try:
        cur = conn.cursor()
        cur.execute("SELECT brand, model_name, NVL(category,''), NVL(image_url,'') FROM products ORDER BY brand, model_name")
        rows = [tuple(r) for r in cur.fetchall()]
        return _csv_response(["Brand", "Model", "Category", "Image URL"], rows)
    except Exception as e:
        flash(f"❌ Export failed: {e}", "danger")
        return redirect(url_for('admin_products'))
    finally:
        if 'cur' in locals() and cur: cur.close()

@app.route('/claims', methods=['GET', 'POST'])
@login_required
def claims():
    if request.method == 'POST':
        warranty_id = request.form.get('warranty_id')
        description = request.form.get('description')
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM warranties WHERE warranty_id = :1 AND user_id = :2", (warranty_id, session['user_id']))
            if cur.fetchone()[0] == 0:
                flash("Invalid warranty selection.", "danger")
                return redirect(url_for('claims'))
            cur.execute("INSERT INTO service_claims (warranty_id, description) VALUES (:1, :2)", (warranty_id, description))
            conn.commit()
            flash("✅ Claim submitted.", "success")
            try:
                cur.execute("SELECT product_name FROM warranties WHERE warranty_id = :1", (warranty_id,))
                pr = cur.fetchone()
                pn = pr[0] if pr else "your product"
                msg = f"Your service claim for '{pn}' has been submitted and is pending review."
                create_notification(session['user_id'], int(warranty_id), msg, email_subject="Claim Submitted")
            except Exception as _:
                pass
            return redirect(url_for('claims'))
        except Exception as e:
            flash(f"❌ Error submitting claim: {e}", "danger")
        finally:
            if 'cur' in locals() and cur: cur.close()

    warranties = []
    claims_list = []
    try:
        cur = conn.cursor()
        cur.execute("SELECT warranty_id, product_name FROM warranties WHERE user_id = :1 ORDER BY product_name", (session['user_id'],))
        for row in cur.fetchall():
            warranties.append({"id": row[0], "product_name": row[1]})
        cur.execute(
            """
            SELECT c.claim_id, w.product_name, c.claim_date, c.description, c.status
            FROM service_claims c
            JOIN warranties w ON c.warranty_id = w.warranty_id
            WHERE w.user_id = :1
            ORDER BY c.claim_date DESC
            """,
            (session['user_id'],)
        )
        for row in cur.fetchall():
            claims_list.append({
                "claim_id": row[0],
                "product_name": row[1],
                "claim_date": row[2].strftime('%Y-%m-%d') if row[2] else '',
                "description": row[3],
                "status": row[4]
            })
    except Exception as e:
        flash(f"❌ Error loading claims: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return render_template('service_claims.html', warranties=warranties, claims=claims_list)

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if 'admin_id' in session:
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        try:
            cur = conn.cursor()
            cur.execute("SELECT admin_id, password FROM admin WHERE email = :1", (email,))
            row = cur.fetchone()
            if row and check_password_hash(row[1], password):
                session['admin_id'] = row[0]
                flash("✅ Admin login successful!", "success")
                return redirect(url_for('admin_dashboard'))
            else:
                flash("❌ Invalid admin email or password.", "danger")
        except Exception as e:
            flash(f"❌ Error: {e}", "danger")
        finally:
            if 'cur' in locals() and cur: cur.close()
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_id', None)
    flash("👋 Logged out of admin.", "info")
    return redirect(url_for('admin_login'))

@app.route('/admin/seed')
def admin_seed():
    token = request.args.get('token')
    if token != app.secret_key:
        return "Forbidden", 403
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM admin")
        if cur.fetchone()[0] == 0:
            cur.execute(
                "INSERT INTO admin (full_name, email, password) VALUES (:1, :2, :3)",
                ("Administrator", "admin@example.com", generate_password_hash("admin123"))
            )
            conn.commit()
            return "Seeded admin@example.com / admin123", 200
        return "Already seeded", 200
    except Exception as e:
        return str(e), 500
    finally:
        if 'cur' in locals() and cur: cur.close()

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    stats = {"users": 0, "warranties": 0, "expiring_soon": 0, "pending_claims": 0}
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        stats["users"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM warranties")
        stats["warranties"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM warranties WHERE expiry_date BETWEEN SYSDATE AND SYSDATE + 30")
        stats["expiring_soon"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM service_claims WHERE status = 'Pending'")
        stats["pending_claims"] = cur.fetchone()[0]
    except Exception as e:
        flash(f"❌ Error loading dashboard: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return render_template('admin_dashboard.html', stats=stats)

@app.route('/admin/claims')
@admin_required
def admin_claims():
    status = request.args.get('status')
    page, size, offset = _get_page_and_size()
    claims = []
    try:
        cur = conn.cursor()
        if status:
            cur.execute(
                """
                SELECT c.claim_id, u.full_name, w.product_name, c.claim_date, c.description, c.status
                FROM service_claims c
                JOIN warranties w ON c.warranty_id = w.warranty_id
                JOIN users u ON w.user_id = u.user_id
                WHERE c.status = :st
                ORDER BY c.claim_date DESC
                OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
                """,
                {"st": status, "off": offset, "lim": size}
            )
        else:
            cur.execute(
                """
                SELECT c.claim_id, u.full_name, w.product_name, c.claim_date, c.description, c.status
                FROM service_claims c
                JOIN warranties w ON c.warranty_id = w.warranty_id
                JOIN users u ON w.user_id = u.user_id
                ORDER BY c.claim_date DESC
                OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
                """,
                {"off": offset, "lim": size}
            )
        for row in cur.fetchall():
            claims.append({
                "claim_id": row[0],
                "user_name": row[1],
                "product_name": row[2],
                "claim_date": row[3].strftime('%Y-%m-%d') if row[3] else '',
                "description": row[4],
                "status": row[5]
            })
    except Exception as e:
        flash(f"❌ Error loading claims: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return render_template('admin_claims.html', claims=claims, current_status=status, page=page, size=size)

@app.route('/admin/claims/<int:claim_id>/status', methods=['POST'])
@admin_required
def admin_update_claim_status(claim_id):
    new_status = request.form.get('status')
    try:
        cur = conn.cursor()
        cur.execute("UPDATE service_claims SET status = :1 WHERE claim_id = :2", (new_status, claim_id))
        conn.commit()
        flash("✅ Claim status updated.", "success")
        try:
            cur.execute(
                """
                SELECT u.user_id, w.warranty_id, w.product_name
                FROM service_claims c
                JOIN warranties w ON c.warranty_id = w.warranty_id
                JOIN users u ON w.user_id = u.user_id
                WHERE c.claim_id = :cid
                """,
                {"cid": claim_id}
            )
            r = cur.fetchone()
            if r:
                uid, wid, pname = int(r[0]), int(r[1]), r[2]
                msg = f"Your service claim for '{pname}' status has been updated to: {new_status}."
                create_notification(uid, wid, msg, email_subject="Claim Status Updated")
        except Exception as _:
            pass
    except Exception as e:
        flash(f"❌ Error updating claim: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return redirect(url_for('admin_claims'))

@app.route('/admin/products', methods=['GET', 'POST'])
@admin_required
def admin_products():
    page, size, offset = _get_page_and_size()
    if request.method == 'POST':
        brand = request.form.get('brand')
        model_name = request.form.get('model_name')
        category = request.form.get('category')
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO products (brand, model_name, category) VALUES (:1, :2, :3)",
                (brand, model_name, category)
            )
            conn.commit()
            flash("✅ Product added.", "success")
            return redirect(url_for('admin_products'))
        except Exception as e:
            flash(f"❌ Error adding product: {e}", "danger")
        finally:
            if 'cur' in locals() and cur: cur.close()

    products = []
    try:
        q = request.args.get('q')
        cur = conn.cursor()
        if q:
            like = f"%{q.lower()}%"
            cur.execute(
                """
                SELECT product_id, brand, model_name, category, image_url, created_at
                FROM products
                WHERE LOWER(brand) LIKE :q OR LOWER(model_name) LIKE :q OR LOWER(NVL(category,'')) LIKE :q
                ORDER BY brand, model_name
                OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
                """,
                {"q": like, "off": offset, "lim": size}
            )
        else:
            cur.execute(
                """
                SELECT product_id, brand, model_name, category, image_url, created_at
                FROM products
                ORDER BY brand, model_name
                OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
                """,
                {"off": offset, "lim": size}
            )
        for row in cur.fetchall():
            products.append({
                "product_id": row[0],
                "brand": row[1],
                "model_name": row[2],
                "category": row[3],
                "image_url": row[4],
                "added_day": (row[5].strftime('%A') if row[5] else ''),
                "added_datetime": (row[5].strftime('%Y-%m-%d %H:%M') if row[5] else '')
            })
    except Exception as e:
        flash(f"❌ Error loading products: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return render_template('products.html', products=products, page=page, size=size)

@app.route('/admin/products/pending')
@admin_required
def admin_pending_products():
    items = []
    page, size, offset = _get_page_and_size()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT product_id, brand, model_name, NVL(category,''), NVL(added_by, 0), created_at
            FROM products
            WHERE verified = 'N'
            ORDER BY created_at DESC
            OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
            """,
            {"off": offset, "lim": size}
        )
        for r in cur.fetchall():
            items.append({
                "product_id": r[0],
                "brand": r[1],
                "model_name": r[2],
                "category": r[3],
                "added_by": int(r[4]) if r[4] is not None else None,
                "created_at": r[5].strftime('%Y-%m-%d') if r[5] else ''
            })
    except Exception as e:
        flash(f"❌ Error loading pending products: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return render_template('pending_products.html', products=items, page=page, size=size)

@app.route('/admin/products/<int:product_id>/verify', methods=['POST'])
@admin_required
def admin_verify_product(product_id: int):
    try:
        cur = conn.cursor()
        cur.execute("UPDATE products SET verified = 'Y' WHERE product_id = :1", (product_id,))
        conn.commit()
        if cur.rowcount and cur.rowcount > 0:
            flash("✅ Product verified.", "success")
        else:
            flash("❌ Product not found.", "danger")
    except Exception as e:
        flash(f"❌ Error verifying product: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return redirect(url_for('admin_pending_products'))

@app.route('/admin/products/<int:product_id>/edit')
@admin_required
def admin_edit_product(product_id: int):
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT product_id, brand, model_name, NVL(category,''), NVL(image_url,'') FROM products WHERE product_id = :1",
            (product_id,)
        )
        row = cur.fetchone()
        if not row:
            flash("❌ Product not found.", "danger")
            return redirect(url_for('admin_products'))
        product = {
            "product_id": row[0],
            "brand": row[1],
            "model_name": row[2],
            "category": row[3],
            "image_url": row[4]
        }
        return render_template('edit_product.html', product=product)
    except Exception as e:
        flash(f"❌ Error loading product: {e}", "danger")
        return redirect(url_for('admin_products'))
    finally:
        if 'cur' in locals() and cur:
            cur.close()

@app.route('/admin/products/<int:product_id>/delete', methods=['POST'])
@admin_required
def admin_delete_product(product_id: int):
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM products WHERE product_id = :1", (product_id,))
        conn.commit()
        flash("✅ Product deleted.", "success")
    except Exception as e:
        flash(f"❌ Error deleting product: {e}", "danger")
    finally:
        if 'cur' in locals() and cur:
            cur.close()
    return redirect(url_for('admin_products'))

@app.route('/admin/users')
@admin_required
def admin_users():
    page, size, offset = _get_page_and_size()
    users = []
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id, full_name, email FROM users ORDER BY full_name OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY", {"off": offset, "lim": size})
        for row in cur.fetchall():
            users.append({"user_id": row[0], "full_name": row[1], "email": row[2]})
    except Exception as e:
        flash(f"❌ Error loading users: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return render_template('admin_users.html', users=users, page=page, size=size)

@app.route('/admin/reports')
@admin_required
def admin_reports():
    expired = []
    upcoming = []
    claims_summary = []
    try:
        cur = conn.cursor()
        # Expired warranties
        cur.execute(
            """
            SELECT w.product_name, u.full_name, w.expiry_date
            FROM warranties w
            JOIN users u ON w.user_id = u.user_id
            WHERE w.expiry_date < SYSDATE
            ORDER BY w.expiry_date DESC
            """
        )
        expired = [{"product_name": r[0], "user_name": r[1], "expiry_date": r[2].strftime('%Y-%m-%d')} for r in cur.fetchall()]
        # Upcoming 30 days
        cur.execute(
            """
            SELECT w.product_name, u.full_name, w.expiry_date
            FROM warranties w
            JOIN users u ON w.user_id = u.user_id
            WHERE w.expiry_date BETWEEN SYSDATE AND SYSDATE + 30
            ORDER BY w.expiry_date ASC
            """
        )
        upcoming = [{"product_name": r[0], "user_name": r[1], "expiry_date": r[2].strftime('%Y-%m-%d')} for r in cur.fetchall()]
        # Claims summary
        cur.execute(
            """
            SELECT u.full_name AS user_name, w.product_name AS product_name,
                   COUNT(c.claim_id) AS total_claims,
                   SUM(CASE WHEN c.status = 'Pending' THEN 1 ELSE 0 END) AS pending_claims,
                   SUM(CASE WHEN c.status = 'In Progress' THEN 1 ELSE 0 END) AS in_progress_claims,
                   SUM(CASE WHEN c.status = 'Completed' THEN 1 ELSE 0 END) AS completed_claims,
                   SUM(CASE WHEN c.status = 'Denied' THEN 1 ELSE 0 END) AS denied_claims
            FROM service_claims c
            JOIN warranties w ON c.warranty_id = w.warranty_id
            JOIN users u ON w.user_id = u.user_id
            GROUP BY u.full_name, w.product_name
            ORDER BY u.full_name, w.product_name
            """
        )
        for r in cur.fetchall():
            claims_summary.append({
                "user_name": r[0],
                "product_name": r[1],
                "total_claims": int(r[2] or 0),
                "pending_claims": int(r[3] or 0),
                "in_progress_claims": int(r[4] or 0),
                "completed_claims": int(r[5] or 0),
                "denied_claims": int(r[6] or 0)
            })
    except Exception as e:
        flash(f"❌ Error loading reports: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return render_template('admin_reports.html', expired=expired, upcoming=upcoming, claims_summary=claims_summary)

# --- Core Routes ---
@app.route('/')
@login_required
def home():
    return render_template('home.html')

@app.route('/my-warranties')
@login_required
def my_warranties():
    page, size, offset = _get_page_and_size()
    warranties = []
    try:
        cur = conn.cursor()
        # Generate notifications (no email) for expired and next-7-days before listing
        try:
            generate_warranty_notifications(session['user_id'], days=7, send_email_now=False)
        except Exception:
            pass
        q = request.args.get('q')
        if q:
            sql = (
                "SELECT warranty_id, product_name, brand, purchase_date, expiry_date, invoice_path "
                "FROM warranties WHERE user_id = :1 "
                "AND (LOWER(product_name) LIKE :2 OR LOWER(NVL(brand,'')) LIKE :2) "
                "ORDER BY expiry_date ASC"
            )
            params = (int(session['user_id']), f"%{q.lower()}%")
        else:
            sql = (
                f"SELECT warranty_id, product_name, brand, purchase_date, expiry_date, invoice_path "
                f"FROM warranties WHERE user_id = {int(session['user_id'])} "
                f"ORDER BY expiry_date ASC"
            )
            params = None
        try:
            if params is not None:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
        except Exception as e:
            print("my_warranties main SELECT failed")
            print("SQL:", sql)
            raise

        rows = cur.fetchall()
        # Python-side pagination
        start_ix = int(offset)
        end_ix = int(offset) + int(size)
        paged = rows[start_ix:end_ix]

        today = date.today()
        thirty_days_from_now = today + relativedelta(days=30)
        notify_on_list = False  # notifications now handled by generate_warranty_notifications

        for row in paged:
            warranty_id = row[0]
            expiry_date_from_db = row[4].date()
            
            # Notification generation moved to generate_warranty_notifications
            
            warranties.append({
                'id': warranty_id,
                'product_name': row[1],
                'brand': row[2],
                'purchase_date': row[3].strftime('%Y-%m-%d'),
                'expiry_date': row[4].strftime('%Y-%m-%d'),
                'invoice_path': row[5],
                'status': "Active" if expiry_date_from_db >= today else "Expired"
            })
    except Exception as e:
        flash(f"❌ Error fetching warranties: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return render_template('my_warranties.html', warranties=warranties, page=page, size=size)

@app.route('/add-warranty', methods=['GET', 'POST'])
@login_required
def add_warranty():
    def _db_select_one(sql, params=None):
        cur = conn.cursor()
        try:
            if params is not None:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            return cur.fetchone()
        finally:
            cur.close()

    def _db_execute(sql, params=None, commit=True):
        cur = conn.cursor()
        try:
            if params is not None:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            if commit:
                conn.commit()
            return cur.rowcount
        finally:
            cur.close()

    def _parse_date_flexible(s):
        try:
            return date.fromisoformat(s)
        except Exception:
            return datetime.strptime(s, "%d-%m-%Y").date()

    def _normalize_pair(brand, name):
        return (str(brand or '').strip().lower(), str(name or '').strip().lower())

    def _user_warranty_exists(user_id, product_name, brand, exclude_id=None):
        if exclude_id is None:
            sql = (
                "SELECT COUNT(*) FROM warranties WHERE user_id = :1 "
                "AND LOWER(product_name) = :2 AND LOWER(NVL(brand,'')) = :3"
            )
            row = _db_select_one(sql, (int(user_id), product_name.lower(), (brand or '').lower()))
        else:
            sql = (
                "SELECT COUNT(*) FROM warranties WHERE user_id = :1 AND warranty_id <> :2 "
                "AND LOWER(product_name) = :3 AND LOWER(NVL(brand,'')) = :4"
            )
            row = _db_select_one(sql, (int(user_id), int(exclude_id), product_name.lower(), (brand or '').lower()))
        return (row[0] if row else 0) > 0
    if request.method == 'POST':
        product_name = request.form['product_name']
        brand = request.form['brand']
        purchase_date_str = request.form['purchase_date']
        period_value = int(request.form['period_value'])
        period_unit = request.form['period_unit']
        invoice_file = request.files.get('invoice_file')
        
        try:
            purchase_date = _parse_date_flexible(purchase_date_str)
        except Exception:
            flash("❌ Invalid purchase date. Use YYYY-MM-DD or DD-MM-YYYY.", "danger")
            return redirect(url_for('add_warranty'))
        warranty_months = period_value * 12 if period_unit == 'years' else period_value
        expiry_date = purchase_date + relativedelta(months=warranty_months)

        invoice_filename = None
        if invoice_file and allowed_file(invoice_file.filename):
            invoice_filename = secure_filename(f"{session['user_id']}_{date.today()}_{invoice_file.filename}")
            invoice_file.save(os.path.join(app.config['UPLOAD_FOLDER'], invoice_filename))

        try:
            if _user_warranty_exists(session['user_id'], product_name, brand):
                flash("❌ Already exists.", "danger")
                return redirect(url_for('add_warranty'))

            b_norm, m_norm = _normalize_pair(brand, product_name)
            row = _db_select_one(
                "SELECT product_id FROM products WHERE LOWER(TRIM(brand)) = :1 AND LOWER(TRIM(model_name)) = :2",
                (b_norm, m_norm)
            )
            if row:
                product_id = int(row[0])
            else:
                cur = conn.cursor()
                try:
                    ret_id = cur.var(cx_Oracle.NUMBER)
                    cur.execute(
                        """
                        INSERT INTO products (brand, model_name, category, image_url)
                        VALUES (:1, :2, NULL, NULL)
                        RETURNING product_id INTO :3
                        """,
                        (brand, product_name, ret_id)
                    )
                    conn.commit()
                    product_id = int(ret_id.getvalue()[0])
                finally:
                    cur.close()

            _db_execute(
                """
                INSERT INTO warranties (user_id, product_name, brand, product_id, purchase_date, warranty_period_months, expiry_date, invoice_path)
                VALUES (:1, :2, :3, :4, :5, :6, :7, :8)
                """,
                (int(session['user_id']), product_name, brand, product_id, purchase_date, warranty_months, expiry_date, invoice_filename)
            )
            flash("✅ Warranty added successfully!", "success")
            return redirect(url_for('my_warranties'))
        except Exception as e:
            msg = str(e)
            try:
                print("Add warranty failed")
                print("Params:", {"uid": int(session['user_id']), "pn": product_name, "br": brand, "pd": purchase_date, "wm": warranty_months, "ed": expiry_date, "ip": invoice_filename})
            except Exception:
                pass
            if 'ORA-00001' in msg or 'unique' in msg.lower():
                flash("❌ Already exists.", "danger")
            else:
                flash(f"❌ Error adding warranty: {e}", "danger")
        finally:
            pass

    return render_template('add_warranty.html')

@app.route('/warranty/<int:warranty_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_warranty(warranty_id):
    try:
        cur = conn.cursor()
        if request.method == 'POST':
            product_name = request.form.get('product_name')
            brand = request.form.get('brand')
            purchase_date_str = request.form.get('purchase_date')
            period_value = int(request.form.get('period_value'))
            period_unit = request.form.get('period_unit')
            invoice_file = request.files.get('invoice_file')

            try:
                purchase_date = date.fromisoformat(purchase_date_str)
            except Exception:
                purchase_date = datetime.strptime(purchase_date_str, "%d-%m-%Y").date()
            warranty_months = period_value * 12 if period_unit == 'years' else period_value
            expiry_date = purchase_date + relativedelta(months=warranty_months)

            invoice_filename = None
            if invoice_file and allowed_file(invoice_file.filename):
                invoice_filename = secure_filename(f"{session['user_id']}_{date.today()}_{invoice_file.filename}")
                invoice_file.save(os.path.join(app.config['UPLOAD_FOLDER'], invoice_filename))

            from_user_has_dup = False
            try:
                from_user_has_dup = _user_warranty_exists(session['user_id'], product_name, brand, exclude_id=warranty_id)
            except Exception:
                pass
            if from_user_has_dup:
                flash("❌ Already exists.", "danger")
                return redirect(url_for('edit_warranty', warranty_id=warranty_id))

            params_tuple = (product_name, brand, purchase_date, warranty_months, expiry_date)
            if invoice_filename:
                sql_upd = f"""
                    UPDATE warranties
                    SET product_name = :1, brand = :2, purchase_date = :3,
                        warranty_period_months = :4, expiry_date = :5, invoice_path = :6
                    WHERE warranty_id = {int(warranty_id)} AND user_id = {int(session['user_id'])}
                """
                try:
                    cur.execute(sql_upd, (product_name, brand, purchase_date, warranty_months, expiry_date, invoice_filename))
                except Exception as e:
                    print("Edit UPDATE failed (with invoice)")
                    print("SQL:", sql_upd)
                    print("Params:", (product_name, brand, purchase_date, warranty_months, expiry_date, invoice_filename))
                    raise
            else:
                sql_upd = f"""
                    UPDATE warranties
                    SET product_name = :1, brand = :2, purchase_date = :3,
                        warranty_period_months = :4, expiry_date = :5
                    WHERE warranty_id = {int(warranty_id)} AND user_id = {int(session['user_id'])}
                """
                try:
                    cur.execute(sql_upd, params_tuple)
                except Exception as e:
                    print("Edit UPDATE failed")
                    print("SQL:", sql_upd)
                    print("Params:", params_tuple)
                    raise
            conn.commit()
            flash("✅ Warranty updated successfully!", "success")
            return redirect(url_for('my_warranties'))

        sql_get = f"""
            SELECT warranty_id, product_name, NVL(brand,''), purchase_date, warranty_period_months, expiry_date, NVL(invoice_path,'')
            FROM warranties WHERE warranty_id = {int(warranty_id)} AND user_id = {int(session['user_id'])}
            """
        cur.execute(sql_get)
        row = cur.fetchone()
        if not row:
            flash("❌ Warranty not found.", "danger")
            return redirect(url_for('my_warranties'))
        data = {
            "warranty_id": row[0],
            "product_name": row[1],
            "brand": row[2],
            "purchase_date": row[3].strftime('%Y-%m-%d'),
            "warranty_period_months": int(row[4]),
            "expiry_date": row[5].strftime('%Y-%m-%d'),
            "invoice_path": row[6] or ''
        }
        return render_template('edit_warranty.html', item=data)
    except Exception as e:
        msg = str(e)
        if 'ORA-00001' in msg or 'unique' in msg.lower():
            flash("❌ Already exists.", "danger")
        else:
            flash(f"❌ Error: {e}", "danger")
        return redirect(url_for('my_warranties'))
    finally:
        if 'cur' in locals() and cur: cur.close()

@app.route('/warranty/<int:warranty_id>/delete', methods=['POST'])
@login_required
def delete_warranty(warranty_id):
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM warranties WHERE warranty_id = :1 AND user_id = :2",
            (int(warranty_id), int(session['user_id']))
        )
        conn.commit()
        if cur.rowcount and cur.rowcount > 0:
            flash("✅ Warranty deleted.", "success")
        else:
            flash("❌ Warranty not found or not owned by you.", "danger")
    except Exception as e:
        flash(f"❌ Error deleting warranty: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return redirect(url_for('my_warranties'))

@app.route('/dedupe-my-warranties', methods=['POST'])
@login_required
def dedupe_my_warranties():
    try:
        cur = conn.cursor()
        sql = f"""
            DELETE FROM warranties
            WHERE ROWID IN (
                SELECT rid FROM (
                    SELECT ROWID rid,
                           ROW_NUMBER() OVER (
                               PARTITION BY LOWER(product_name), LOWER(NVL(brand,''))
                               ORDER BY warranty_id
                           ) rn
                    FROM warranties
                    WHERE user_id = {int(session['user_id'])}
                )
                WHERE rn > 1
            )
        """
        cur.execute(sql)
        deleted = cur.rowcount or 0
        conn.commit()
        # Try to (re)create the unique index after cleanup
        try:
            cur.execute(
                """
                CREATE UNIQUE INDEX ux_warranties_user_prod_brand
                ON warranties (user_id, LOWER(product_name), LOWER(NVL(brand,'')))
                """
            )
            conn.commit()
        except Exception as ie:
            print(f"Index ensure after dedupe note: {ie}")
        flash(f"✅ Removed {deleted} duplicate warranty record(s).", "success")
    except Exception as e:
        flash(f"❌ Error deduping warranties: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return redirect(url_for('my_warranties'))

@app.route('/profile')
@login_required
def profile():
    user_info = {}
    try:
        cur = conn.cursor()
        cur.execute("SELECT full_name, email FROM users WHERE user_id = :1", (session['user_id'],))
        user_data = cur.fetchone()
        if user_data:
            user_info['name'] = user_data[0]
            user_info['email'] = user_data[1]
    except Exception as e:
        flash(f"❌ Error fetching profile: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return render_template('profile.html', user=user_info)

@app.route('/profile/name', methods=['POST'])
@login_required
def update_profile_name():
    try:
        data = request.get_json(silent=True) or {}
        full_name = (data.get('full_name') or '').strip()
        if not full_name:
            return jsonify({"success": False, "error": "Name cannot be empty."}), 400
        cur = conn.cursor()
        cur.execute("UPDATE users SET full_name = :1 WHERE user_id = :2", (full_name, session['user_id']))
        conn.commit()
        return jsonify({"success": True, "full_name": full_name})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if 'cur' in locals() and cur: cur.close()
    
@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- Authentication Routes ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('home'))
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        try:
            cur = conn.cursor()
            cur.execute("SELECT user_id, password, full_name, email FROM users WHERE email = :1", (email,))
            user_data = cur.fetchone()
            if user_data and check_password_hash(user_data[1], password):
                session['user_id'] = user_data[0]
                flash("✅ Login successful!", "success")
                # Send a welcome email with site info
                try:
                    user_name = user_data[2] or "User"
                    to_email = user_data[3]
                    subject = "Welcome back to Warracker"
                    body = (
                        f"Hi {user_name},\n\n"
                        "You have successfully logged in to Warracker.\n\n"
                        "With Warracker you can:\n"
                        "- Track all your product warranties in one place.\n"
                        "- Get reminders before warranties expire.\n"
                        "- Submit and track service claims easily.\n\n"
                        "Visit your dashboard to view expiring warranties and more.\n\n"
                        "Best regards,\n"
                        "Warracker Team"
                    )
                    send_email(to_email, subject, body)
                except Exception as _:
                    pass
                # Also generate 7-day warranty reminders and send via email post-login
                try:
                    generate_warranty_notifications(session['user_id'], days=7, send_email_now=True)
                except Exception:
                    pass
                return redirect(url_for('home'))
            else:
                flash("❌ Invalid email or password.", "danger")
        except Exception as e:
             flash(f"❌ Error: {e}", "danger")
        finally:
            if 'cur' in locals() and cur: cur.close()
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('home'))
    if request.method == 'POST':
        full_name = request.form['full_name']
        email = request.form['email']
        password = request.form['password']
        hashed_password = generate_password_hash(password)
        try:
            cur = conn.cursor()
            cur.execute("SELECT email FROM users WHERE email = :1", (email,))
            if cur.fetchone():
                flash("📧 An account with this email already exists.", "warning")
                return redirect(url_for('register'))
            cur.execute("INSERT INTO users (full_name, email, password) VALUES (:1, :2, :3)", (full_name, email, hashed_password))
            conn.commit()
            # Send a professional welcome email
            try:
                subject = "🎉 Welcome to Warracker"
                body = (
                    f"Hi {full_name},\n\n"
                    "You have successfully registered to Warracker.\n\n"
                    "With Warracker you can:\n"
                    "- Track all your product warranties in one place.\n"
                    "- Receive smart reminders before warranties expire.\n"
                    "- Submit and manage service claims with ease.\n\n"
                    "Tip: Add your first product now to start receiving reminders.\n\n"
                    "Enjoy our services,\n"
                    "Warracker Team"
                )
                send_email(email, subject, body)
            except Exception:
                pass
            flash("✅ Registration successful! Please log in.", "success")
            return redirect(url_for('login'))
        except Exception as e:
            flash(f"❌ Error: {e}", "danger")
        finally:
            if 'cur' in locals() and cur: cur.close()
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("👋 You have been successfully logged out.", "info")
    return redirect(url_for('login'))

# NEW: These are the API routes the JavaScript uses to fetch and update notifications.
@app.route('/get_notifications')
@login_required
def get_notifications():
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT notification_id, message, created_at, status FROM notifications WHERE user_id = :1 ORDER BY created_at DESC",
            (session['user_id'],)
        )
        notifications = []
        for row in cur.fetchall():
            notifications.append({
                "NOTIFICATION_ID": row[0],
                "MESSAGE": row[1],
                "CREATED_AT": row[2].strftime("%Y-%m-%d"), # Convert date to string
                "STATUS": row[3]
            })
        return jsonify(notifications)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cur' in locals() and cur: cur.close()

@app.route('/mark_notifications_read', methods=['POST'])
@login_required
def mark_notifications_read():
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE notifications SET status = 'Read' WHERE user_id = :1 AND status = 'Unread'",
            (session['user_id'],)
        )
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cur' in locals() and cur: cur.close()

if __name__ == '__main__':
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    # Start background scheduler for email reminders (runs even without user activity)
    start_email_scheduler_if_enabled()
    app.run(debug=True)

