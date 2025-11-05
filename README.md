# DBMS_DTW
Digital Warranty Tracker (Warracker)

## Overview
Warracker is a Flask-based web application that helps users track product warranties, receive reminders before expiry, and manage service claims. It also provides an administrator portal for managing users, products, warranties, and reports, with CSV export capabilities.

## Features
- **User authentication**
- **Manage warranties**
  - Add/edit warranties with purchase date, period (months/years), and brand
  - Upload invoice files (PDF/PNG/JPG/JPEG)
  - View active vs expired status and expiring lists
- **Automated reminders and notifications**
  - Background scheduler sends daily reminders for upcoming and expired warranties
  - In-app notifications (bell icon) with unread count
  - Email notifications via SMTP
  - API endpoints: `/get_notifications`, `/mark_notifications_read`
- **Service claims**
  - Submit service claims linked to warranties
  - Admin updates claim statuses; users receive notifications
- **Admin portal**
  - Dashboard with key stats
  - Manage products catalog
  - View/filter warranties and claims with pagination
  - CSV exports for warranties, claims, products, and user warranties
- **De-duplication safeguards**
  - Unique index on `(user_id, lower(product_name), lower(nvl(brand,'')))` for warranties
  - Unique index on `(user_id, warranty_id, message)` for notifications

## Tech Stack
- Python, Flask
- Oracle Database via `cx_Oracle`
- Templates (Jinja2), HTML/CSS/JS
- `python-dotenv` for environment variables
- `dateutil` for date calculations

## Prerequisites
- Python 3.x
- Oracle Client/Instant Client compatible with `cx_Oracle`
- Access to an Oracle Database with the required tables

## Installation
1. Clone/download this repository.
2. Create and activate a virtual environment.
   - Windows (PowerShell):
     - `python -m venv venv`
     - `./venv/Scripts/Activate.ps1`
3. Install dependencies:
   - `pip install flask cx_Oracle python-dotenv python-dateutil`
4. Create an `.env` file in the project root with the variables below.

## Environment Variables (.env)
- `SECRET_KEY` — Flask session secret key
- `DB_HOST` — Oracle DB host
- `DB_PORT` — Oracle DB port (e.g., 1521)
- `DB_SERVICE` — Oracle service name
- `DB_USER` — Oracle username
- `DB_PASSWORD` — Oracle password
- `SMTP_HOST` — SMTP server host (default: smtp.gmail.com)
- `SMTP_PORT` — SMTP port (default: 587)
- `SMTP_USER` — SMTP username
- `SMTP_PASS` — SMTP password
- `SMTP_FROM` — From address (defaults to `SMTP_USER`)

## Running Locally
1. Ensure the Oracle DB is accessible and the required tables exist.
2. Start the app:
   - `python app.py`
3. Visit the app at: `http://127.0.0.1:5000`

On first start, the app will create an `uploads/` folder if missing and start a background scheduler that sends daily warranty reminders.

## Localhost URLs
- **App (Home):** `http://127.0.0.1:5000/`
- **Login:** `http://127.0.0.1:5000/login`
- **Register:** `http://127.0.0.1:5000/register`
- **My Warranties:** `http://127.0.0.1:5000/my-warranties`
- **Claims:** `http://127.0.0.1:5000/claims`
- **Expiring:** `http://127.0.0.1:5000/expiring?days=30`
- **Profile:** `http://127.0.0.1:5000/profile`
- **Notifications API:**
  - `http://127.0.0.1:5000/get_notifications`
  - `http://127.0.0.1:5000/mark_notifications_read`
- **Admin:**
  - `http://127.0.0.1:5000/admin/login`
  - `http://127.0.0.1:5000/admin/dashboard`
  - `http://127.0.0.1:5000/admin/warranties`
  - `http://127.0.0.1:5000/admin/claims`
  - `http://127.0.0.1:5000/admin/products`
  - `http://127.0.0.1:5000/admin/users`
  - `http://127.0.0.1:5000/admin/reports`
  - Seed default admin (temporary): `http://127.0.0.1:5000/admin/seed?token=<SECRET_KEY>`

## Theme (Local Colors)
These are the current theme variables from `static/css/style.css` (`:root`), used across the app.

- **Background (`--bg`):** `#0b1220`
- **Panel (`--panel`):** `#101827`
- **Panel 2 (`--panel-2`):** `#0f172a`
- **Border (`--border`):** `#1f2a44`
- **Text (`--text`):** `#e5e7eb`
- **Muted (`--muted`):** `#9aa7bd`
- **Primary (`--primary`):** `#6366f1`
- **Primary 600 (`--primary-600`):** `#5458e8`
- **Primary 700 (`--primary-700`):** `#4f46e5`
- **Accent (`--accent`):** `#22d3ee`
- **Success (`--success`):** `#10b981`
- **Danger (`--danger`):** `#ef4444`
- **Warning (`--warning`):** `#f59e0b`

Common component colors you’ll also see in the UI:

- **Buttons (primary gradient):** `linear-gradient(135deg, #6366f1, #22d3ee)`
- **Input focus ring:** `rgba(34,211,238,0.18)`
- **Table header:** `#141d30`

To change the theme, edit the variables under `:root` in `static/css/style.css`.

## Screenshots
Add screenshots in this section after running locally. Suggested shots:

- **Login / Register**
- **Dashboard (Home)**
- **My Warranties (table)**
- **Create/Edit Warranty (form)**
- **Claims**
- **Expiring**
- **Profile**
- **Admin Dashboard and Lists**

Place images in a folder like `docs/screenshots/` and reference them here, for example:

```markdown
![Login](docs/screenshots/login.png)
![Dashboard](docs/screenshots/dashboard.png)
```

## Database Notes
The app expects the following tables (names inferred from the code):
- `users`
- `warranties`
- `service_claims`
- `notifications`
- `products`
- `admin`

At runtime, the app attempts to ensure helpful unique indexes:
- `ux_warranties_user_prod_brand` on `(user_id, LOWER(product_name), LOWER(NVL(brand,'')))`
- `ux_notifications_user_warranty_message` on `(user_id, warranty_id, message)`

## Key Routes (Non-exhaustive)
- User
  - `/` — Home (requires login)
  - `/login`, `/register`, `/logout`
  - `/my-warranties` — List (pagination)
  - `/add-warranty`, `/warranty/<id>/edit`, `/warranty/<id>`
  - `/claims` — Create and list service claims
  - `/expiring?days=30` — View expiring warranties
  - `/export/my_warranties` — CSV export
- Notifications API
  - `/get_notifications` — JSON list
  - `/mark_notifications_read` — Mark unread as read
- Admin
  - `/admin/login`, `/admin/logout`, `/admin/dashboard`
  - `/admin/warranties`, `/admin/claims`, `/admin/claims/<id>/status`
  - `/admin/products`, `/admin/users`, `/admin/reports`
  - CSV: `/admin/export/warranties`, `/admin/export/claims`, `/admin/export/products`
  - Seed: `/admin/seed?token=<SECRET_KEY>` — Creates default admin if none exists

## File Uploads
- Invoices are saved under `uploads/`.
- Allowed extensions: `pdf`, `png`, `jpg`, `jpeg`.

## Email and Scheduler
- SMTP settings are read from environment variables.
- A background thread runs daily (`start_email_scheduler_if_enabled`) to generate and email reminders for warranties that are expired or expiring soon.

## Security Notes
- Change `SECRET_KEY` in production.
- Protect `/admin/seed` by keeping the token secret; disable or remove after seeding.
- Do not run with `debug=True` in production.
- Validate and restrict who can access the server and database.

## Troubleshooting
- "Database connection failed": Ensure Oracle Instant Client is installed and on the system PATH. Verify all `DB_*` environment variables.
- SMTP errors or no email: Check credentials, firewall, and that the account allows SMTP/STARTTLS.
- File uploads failing: Ensure `uploads/` exists and the process has write permission. The app auto-creates this folder on start.
- Duplicate warranty errors: The app prevents duplicates by product+brand per user; adjust the existing record or edit instead of adding a new one.

## License
This project is provided as-is for educational and internal use. Add a proper license if distributing.
