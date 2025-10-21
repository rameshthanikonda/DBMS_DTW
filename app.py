# NEW: Import jsonify
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, jsonify, Response
import cx_Oracle
import os
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import date
from dateutil.relativedelta import relativedelta # For accurate date math
from functools import wraps
import io
import csv

# --- App Configuration ---
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev_secret_key")
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'png', 'jpg', 'jpeg'}

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

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            flash("Please log in as admin.", "warning")
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

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
                SELECT product_id, brand, model_name, category, image_url
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
                SELECT product_id, brand, model_name, category, image_url
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
                "image_url": row[4]
            })
    except Exception as e:
        flash(f"❌ Error loading products: {e}", "danger")
    finally:
        if 'cur' in locals() and cur: cur.close()
    return render_template('products.html', products=products, page=page, size=size)

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
        sql = f"""
            SELECT warranty_id, product_name, brand, purchase_date, expiry_date, invoice_path
            FROM warranties WHERE user_id = {int(session['user_id'])}
            ORDER BY expiry_date ASC
        """
        try:
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
        notify_on_list = True

        for row in paged:
            warranty_id = row[0]
            expiry_date_from_db = row[4].date()
            
            # NEW: Create notifications for both expired and expiring-soon warranties.
            if notify_on_list:
                try:
                    product_name = row[1]
                    message = None
                    if expiry_date_from_db < today:
                        message = f"Your warranty for '{product_name}' has expired on {expiry_date_from_db.strftime('%B %d, %Y')}."
                    elif today <= expiry_date_from_db <= thirty_days_from_now:
                        message = f"Your warranty for '{product_name}' expires on {expiry_date_from_db.strftime('%B %d, %Y')}."
                    if message:
                        # De-dup by matching the exact message for this user+warranty
                        sql_chk = f"SELECT COUNT(*) FROM notifications WHERE user_id = {int(session['user_id'])} AND warranty_id = {int(warranty_id)} AND message = :msg"
                        cur.execute(sql_chk, {"msg": message})
                        if cur.fetchone()[0] == 0:
                            cur.execute(
                                f"INSERT INTO notifications (user_id, warranty_id, message) VALUES ({int(session['user_id'])}, {int(warranty_id)}, :msg)",
                                {"msg": message}
                            )
                            conn.commit()
                except Exception as e:
                    try:
                        print("Notification creation failed")
                        print("Check SQL:", sql_chk if 'sql_chk' in locals() else '')
                        print("Params:", {"msg": message} if 'message' in locals() else {})
                    except Exception:
                        pass
                    print(f"Notification creation failed: {e}")
            
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
    if request.method == 'POST':
        product_name = request.form['product_name']
        brand = request.form['brand']
        purchase_date_str = request.form['purchase_date']
        period_value = int(request.form['period_value'])
        period_unit = request.form['period_unit']
        invoice_file = request.files.get('invoice_file')
        
        purchase_date = date.fromisoformat(purchase_date_str)
        warranty_months = period_value * 12 if period_unit == 'years' else period_value
        expiry_date = purchase_date + relativedelta(months=warranty_months)

        invoice_filename = None
        if invoice_file and allowed_file(invoice_file.filename):
            invoice_filename = secure_filename(f"{session['user_id']}_{date.today()}_{invoice_file.filename}")
            invoice_file.save(os.path.join(app.config['UPLOAD_FOLDER'], invoice_filename))

        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(*) FROM warranties
                WHERE user_id = :uid
                  AND LOWER(product_name) = :pn
                  AND LOWER(NVL(brand,'')) = :br
                """,
                {"uid": session['user_id'], "pn": product_name.lower(), "br": (brand or '').lower()}
            )
            if cur.fetchone()[0] > 0:
                flash("❌ A warranty for this product and brand already exists.", "danger")
                return redirect(url_for('add_warranty'))
            cur.execute(
                """
                INSERT INTO warranties (user_id, product_name, brand, purchase_date, warranty_period_months, expiry_date, invoice_path)
                VALUES (:1, :2, :3, :4, :5, :6, :7)
                """,
                (int(session['user_id']), product_name, brand, purchase_date, warranty_months, expiry_date, invoice_filename)
            )
            conn.commit()
            flash("✅ Warranty added successfully!", "success")
            return redirect(url_for('my_warranties'))
        except Exception as e:
            msg = str(e)
            if 'ORA-00001' in msg or 'unique' in msg.lower():
                flash("❌ A warranty for this product and brand already exists.", "danger")
            else:
                flash(f"❌ Error adding warranty: {e}", "danger")
        finally:
             if 'cur' in locals() and cur: cur.close()

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

            purchase_date = date.fromisoformat(purchase_date_str)
            warranty_months = period_value * 12 if period_unit == 'years' else period_value
            expiry_date = purchase_date + relativedelta(months=warranty_months)

            invoice_filename = None
            if invoice_file and allowed_file(invoice_file.filename):
                invoice_filename = secure_filename(f"{session['user_id']}_{date.today()}_{invoice_file.filename}")
                invoice_file.save(os.path.join(app.config['UPLOAD_FOLDER'], invoice_filename))

            sql_dup = f"""
                SELECT COUNT(*) FROM warranties
                WHERE user_id = {int(session['user_id'])} AND warranty_id <> {int(warranty_id)}
                  AND LOWER(product_name) = :pn
                  AND LOWER(NVL(brand,'')) = :br
                """
            cur.execute(sql_dup, {"pn": product_name.lower(), "br": (brand or '').lower()})
            if cur.fetchone()[0] > 0:
                flash("❌ A warranty for this product and brand already exists.", "danger")
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
            flash("❌ A warranty for this product and brand already exists.", "danger")
        else:
            flash(f"❌ Error: {e}", "danger")
        return redirect(url_for('my_warranties'))
    finally:
        if 'cur' in locals() and cur: cur.close()

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
            cur.execute("SELECT user_id, password FROM users WHERE email = :1", (email,))
            user_data = cur.fetchone()
            if user_data and check_password_hash(user_data[1], password):
                session['user_id'] = user_data[0]
                flash("✅ Login successful!", "success")
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
    app.run(debug=True)

