"""
ApplyBot Backend — Flask + Stripe + CDP Auto-Apply Engine
v2: Fair pricing, legal compliance, email confirmations, queue system
"""
import os, json, time, uuid, threading, sqlite3, logging, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, render_template, request, jsonify, redirect, send_from_directory
import stripe

# === CONFIG ===
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "applybot.db"
PERSIST_DIR = DATA_DIR / "persist"  # JSON backups survive Render disk wipes (S9 Yesod)
DATA_DIR.mkdir(exist_ok=True)
PERSIST_DIR.mkdir(exist_ok=True)

STRIPE_SECRET = os.environ.get("STRIPE_SECRET_KEY", os.environ.get("STRIPE_SECRET", ""))
STRIPE_PUBLIC = os.environ.get("STRIPE_PUBLISHABLE_KEY", os.environ.get("STRIPE_PUBLIC", ""))
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
DOMAIN = os.environ.get("DOMAIN", "https://applybot-yavz.onrender.com")
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")  # S5 Gevurah: env-only, never hardcoded

stripe.api_key = STRIPE_SECRET

# Price IDs from Stripe dashboard
PRICES = {
    "starter": {"id": os.environ.get("PRICE_STARTER", ""), "tokens": 10, "amount": 500},
    "standard": {"id": os.environ.get("PRICE_STANDARD", ""), "tokens": 50, "amount": 1900},
    "pro": {"id": os.environ.get("PRICE_PRO", ""), "tokens": 150, "amount": 3900},
    "unlimited": {"id": os.environ.get("PRICE_UNLIMITED", ""), "tokens": 500, "amount": 7900},
}

# === DATABASE ===
def get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT,
            name TEXT,
            resume_path TEXT,
            resume_text TEXT,
            skills TEXT,
            target_location TEXT,
            target_titles TEXT,
            tokens INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS applications (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            job_title TEXT,
            company TEXT,
            platform TEXT,
            status TEXT DEFAULT 'pending',
            url TEXT,
            tailored_resume TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS payments (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            stripe_session_id TEXT,
            plan TEXT,
            tokens INTEGER,
            amount INTEGER,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
    """)
    db.commit()
    db.close()

init_db()

# ── PERSISTENCE (S9 Yesod: survive Render disk wipes) ──
def _persist_user(user_row):
    """Save user as JSON backup."""
    path = PERSIST_DIR / f"user_{user_row['id']}.json"
    data = dict(user_row)
    data["_saved_at"] = datetime.utcnow().isoformat()
    with open(path, 'w') as f:
        json.dump(data, f, default=str)

def _restore_users():
    """Restore users from JSON backups after disk wipe.
    S9 Yesod: if JSON backups are also gone (Render ephemeral disk), 
    fall back to Stripe API to reconstruct from payment history."""
    count = 0
    for f in PERSIST_DIR.glob("user_*.json"):
        try:
            data = json.loads(f.read_text())
            uid = data.get("id")
            if uid:
                db = get_db()
                db.execute("""INSERT OR REPLACE INTO users 
                    (id, email, name, resume_path, resume_text, skills,
                     target_location, target_titles, tokens)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (uid, data.get("email"), data.get("name"),
                     data.get("resume_path"), data.get("resume_text"),
                     data.get("skills"), data.get("target_location"),
                     data.get("target_titles"), data.get("tokens", 0)))
                db.commit()
                db.close()
                count += 1
        except: pass
    
    # S9: If nothing restored, try Stripe as ultimate source of truth
    if count == 0 and STRIPE_SECRET:
        try:
            sessions = stripe.checkout.Session.list(limit=100, status="complete")
            for session in sessions.auto_paging_iter():
                meta = session.get("metadata", {})
                user_id = meta.get("user_id")
                tokens = int(meta.get("tokens", 0))
                email = session.get("customer_details", {}).get("email", "")
                name = session.get("customer_details", {}).get("name", "")
                if user_id and tokens:
                    db = get_db()
                    db.execute("""INSERT OR REPLACE INTO users 
                        (id, email, name, tokens) VALUES (?, ?, ?, ?)""",
                        (user_id, email, name, tokens))
                    # Also persist to JSON for future restores
                    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
                    if user:
                        _persist_user(user)
                    db.commit()
                    db.close()
                    count += 1
        except Exception as e:
            logging.warning(f"Stripe fallback restore failed: {e}")
    
    return count

_restored = _restore_users()
if _restored:
    logging.info(f"Restored {_restored} users from persistent backup (JSON + Stripe fallback)")

# === FLASK APP ===
app = Flask(__name__)

# === CORS (allow GitHub Pages to call this API) ===
@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

@app.route("/")
def index():
    return render_template("index.html",
        STRIPE_KEY=STRIPE_PUBLIC,
        DOMAIN=DOMAIN,
        PRICE_STARTER=PRICES["starter"]["id"],
        PRICE_STANDARD=PRICES["standard"]["id"],
        PRICE_PRO=PRICES["pro"]["id"],
        PRICE_UNLIMITED=PRICES["unlimited"]["id"])

# === SiteLaunch Platform Routes ===
@app.route("/crm")
def crm_index():
    """Serve the SiteLaunch CRM."""
    return send_from_directory(str(BASE_DIR / "platform"), "index.html")

@app.route("/crm/config/<path:filename>")
def crm_config(filename):
    return send_from_directory(str(BASE_DIR / "platform" / "config"), filename)

@app.route("/crm/lead-sources/<path:filename>")
def crm_leads(filename):
    return send_from_directory(str(BASE_DIR / "platform" / "lead-sources"), filename)

@app.route("/crm/<path:filename>")
def crm_static(filename):
    return send_from_directory(str(BASE_DIR / "platform"), filename)

@app.route("/api/create-checkout", methods=["POST", "OPTIONS"])
def create_checkout():
    if request.method == "OPTIONS":
        return "", 200
    data = request.json
    plan_name = data.get("plan", "starter")
    plan = PRICES.get(plan_name, PRICES["starter"])
    
    user_id = str(uuid.uuid4())
    
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{"price": plan["id"], "quantity": 1}],
        mode="payment",
        success_url=f"{DOMAIN}/apply/{user_id}",
        cancel_url=f"{DOMAIN}/#pricing",
        metadata={"user_id": user_id, "plan": plan_name, "tokens": str(plan["tokens"])},
        payment_intent_data={"metadata": {"user_id": user_id, "plan": plan_name}}
    )
    
    return jsonify({"sessionId": session.id})


# === FREE TRIAL (S10 Malkuth: trust before payment) ===
FREE_TRIAL_TOKENS = 2
FREE_TRIAL_LIMIT = {}  # in-memory rate limit per email (reset on deploy)

@app.route("/api/free-trial", methods=["POST", "OPTIONS"])
def free_trial():
    """Create a free trial user with 2 application tokens. No payment required."""
    if request.method == "OPTIONS":
        return "", 200
    
    data = request.json or {}
    email = (data.get("email", "") or "").strip().lower()
    name = (data.get("name", "") or "").strip()
    job_title = (data.get("job_title", "") or "IT Support Specialist").strip()
    location = (data.get("location", "") or "Remote").strip()
    
    # Basic validation
    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400
    
    # Rate limit: 1 free trial per email
    if email in FREE_TRIAL_LIMIT:
        return jsonify({"error": "You've already claimed your free trial. Upgrade to continue!"}), 429
    
    FREE_TRIAL_LIMIT[email] = time.time()
    
    # Create user
    user_id = str(uuid.uuid4())
    db = get_db()
    
    # Check if this email already has a paid account
    existing = db.execute("SELECT id, tokens FROM users WHERE email=?", (email,)).fetchone()
    if existing and existing["tokens"] > FREE_TRIAL_TOKENS:
        db.close()
        return jsonify({"error": "You already have an account with tokens!", "user_id": existing["id"]}), 409
    
    if existing:
        # Top up existing free user (they used their 2 tokens and came back)
        db.execute("UPDATE users SET tokens = tokens + ? WHERE id=?", 
                   (FREE_TRIAL_TOKENS, existing["id"]))
        db.commit()
        user_id = existing["id"]
    else:
        db.execute(
            """INSERT INTO users (id, email, name, tokens, target_titles, target_location)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, email, name, FREE_TRIAL_TOKENS, job_title, location)
        )
        db.commit()
    
    # Persist
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    _persist_user(user)
    db.close()
    
    # Send welcome email in background
    if email and GMAIL_ADDRESS:
        threading.Thread(target=_send_free_trial_email, 
                        args=(email, name, user_id, job_title, location), 
                        daemon=True).start()
    
    logging.info(f"Free trial: {email} gets {FREE_TRIAL_TOKENS} tokens (id={user_id})")
    
    return jsonify({
        "success": True,
        "user_id": user_id,
        "tokens": FREE_TRIAL_TOKENS,
        "message": f"🎉 {FREE_TRIAL_TOKENS} free applications queued! We'll search for '{job_title}' jobs in {location}.",
        "next": f"{DOMAIN}/dashboard/{user_id}"
    })


def _send_free_trial_email(email, name, user_id, job_title, location):
    """Send welcome email for free trial users with upgrade prompt."""
    try:
        msg = MIMEMultipart()
        msg["From"] = f"ApplyBot <{GMAIL_ADDRESS}>"
        msg["To"] = email
        msg["Subject"] = f"🎉 Your {FREE_TRIAL_TOKENS} free applications are queued!"
        
        body = f"""Hi {name or 'there'},

Your {FREE_TRIAL_TOKENS} free job applications are being processed!

    🔍 Searching: {job_title}
    📍 Location: {location}
    📧 Confirmations: You'll get an email for each application

Track your applications live:
{DOMAIN}/dashboard/{user_id}

━━━━━━━━━━━━━━━━━━━━
Want to apply to MORE jobs?
━━━━━━━━━━━━━━━━━━━━

    ⚡ 50 applications — $19
    🚀 150 applications — $39
    🔥 500 applications — $79

Upgrade here: {DOMAIN}/#pricing

— ApplyBot
"""
        msg.attach(MIMEText(body, "plain"))
        
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(GMAIL_ADDRESS, os.environ.get("GMAIL_APP_PASSWORD", ""))
        server.sendmail(GMAIL_ADDRESS, email, msg.as_string())
        server.quit()
    except Exception as e:
        logging.warning(f"Free trial email failed: {e}")


# === JOB SEARCH API ===
@app.route("/api/job-search")
def job_search():
    from search import search_jobs
    keywords = request.args.get("keywords", "")
    location = request.args.get("location", "")
    category = request.args.get("category", "")
    
    if not keywords and not category and not location:
        return jsonify({"error": "keywords, category, or location required"}), 400
    
    results = search_jobs(
        keywords=keywords,
        location=location,
        category=category,
        max_preview=15
    )
    return jsonify(results)

@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_data(as_text=True)
    sig = request.headers.get("Stripe-Signature")
    
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        logging.error(f"Webhook signature verification failed: {e}")
        return "Invalid signature", 400
    
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session["metadata"]["user_id"]
        plan = session["metadata"]["plan"]
        tokens = int(session["metadata"]["tokens"])
        email = session.get("customer_details", {}).get("email", "")
        name = session.get("customer_details", {}).get("name", "")
        
        db = get_db()
        db.execute(
            "INSERT OR REPLACE INTO users (id, email, name, tokens) VALUES (?, ?, ?, ?)",
            (user_id, email, name, tokens)
        )
        db.execute(
            "INSERT INTO payments (id, user_id, stripe_session_id, plan, tokens, amount, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, session["id"], plan, tokens, session["amount_total"], "completed")
        )
        db.commit()
        db.close()
        
        # Persist user to JSON backup (S9 Yesod)
        try:
            db2 = get_db()
            user = db2.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if user:
                _persist_user(user)
            db2.close()
        except: pass
        
        logging.info(f"Payment complete: {email} got {tokens} tokens ({plan})")
        
        # Send welcome email in background
        if email:
            threading.Thread(target=_send_welcome, args=(email, user_id, tokens), daemon=True).start()
    
    return "OK", 200

def _send_welcome(email, user_id, tokens):
    try:
        from emailer import send_welcome_email
        send_welcome_email(email, user_id, tokens)
    except Exception as e:
        logging.error(f"Welcome email failed: {e}")

# === QUEUE API (for local worker) ===
@app.route("/api/queue/pending")
def queue_pending():
    """Return users who have tokens and a resume uploaded."""
    db = get_db()
    users = db.execute("""
        SELECT id, email, tokens, target_titles, target_location 
        FROM users 
        WHERE tokens > 0 AND resume_text IS NOT NULL AND resume_text != ''
        ORDER BY created_at DESC
        LIMIT 10
    """).fetchall()
    db.close()
    
    return jsonify({
        "users": [{
            "user_id": u["id"],
            "email": u["email"],
            "tokens": u["tokens"],
            "target_titles": u["target_titles"],
            "target_location": u["target_location"]
        } for u in users]
    })

@app.route("/api/queue/stats")
def queue_stats():
    """Return overall queue statistics."""
    db = get_db()
    
    total_users = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    total_payments = db.execute("SELECT COUNT(*) as c FROM payments WHERE status='completed'").fetchone()["c"]
    total_apps = db.execute("SELECT COUNT(*) as c FROM applications").fetchone()["c"]
    submitted = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='submitted'").fetchone()["c"]
    pending = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='pending'").fetchone()["c"]
    failed = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='failed'").fetchone()["c"]
    total_tokens = db.execute("SELECT SUM(tokens) as c FROM users").fetchone()["c"] or 0
    
    db.close()
    
    return jsonify({
        "users": total_users,
        "payments": total_payments,
        "applications_total": total_apps,
        "applications_submitted": submitted,
        "applications_failed": failed,
        "applications_pending": pending,
        "tokens_available": total_tokens,
        "success_rate": round(submitted / total_apps * 100, 1) if total_apps > 0 else 0
    })


@app.route("/api/analytics")
def api_analytics():
    """Real application analytics (S8 Hod) — success rates, failure reasons, company stats."""
    try:
        from engine import get_analytics
        analytics = get_analytics()
    except:
        return jsonify({"error": "Engine analytics not available"}), 503
    
    db = get_db()
    recent_failures = db.execute(
        "SELECT job_title, company, notes, created_at FROM applications "
        "WHERE status='failed' ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    db.close()
    
    return jsonify({
        "engine": analytics,
        "recent_failures": [dict(r) for r in recent_failures]
    })

# === APPLICATION ENDPOINTS ===
@app.route("/apply/<user_id>", methods=["GET", "POST"])
def apply_page(user_id):
    if request.method == "POST":
        resume_file = request.files.get("resume")
        location = request.form.get("location", "")
        titles = request.form.get("titles", "")
        
        db = get_db()
        if resume_file:
            resume_path = DATA_DIR / f"resume_{user_id}.pdf"
            resume_file.save(str(resume_path))
            resume_text = extract_resume_text(str(resume_path))
            
            db.execute(
                "UPDATE users SET resume_path=?, resume_text=?, target_location=?, target_titles=? WHERE id=?",
                (str(resume_path), resume_text, location, titles, user_id)
            )
        else:
            resume_text = request.form.get("resume_text", "")
            db.execute(
                "UPDATE users SET resume_text=?, target_location=?, target_titles=? WHERE id=?",
                (resume_text, location, titles, user_id)
            )
        
        db.commit()
        db.close()
        
        return redirect(f"/dashboard/{user_id}")
    
    return render_template("index.html")


@app.route("/apply/demo")
def apply_demo():
    """Demo apply page — no login required, starts with free trial."""
    return render_template("apply.html", user_id="demo",
                          user_email="",
                          user_name="Guest")


@app.route("/builder")
def resume_builder():
    """Standalone resume builder & job matcher."""
    return render_template("builder.html")


@app.route("/apply/<user_id>")
def apply_page(user_id):
    """Show the apply page."""
    db = get_db()
    user = db.execute("SELECT email, name FROM users WHERE id=?", (user_id,)).fetchone()
    db.close()
    return render_template("apply.html", user_id=user_id, 
                          user_email=user["email"] if user else "",
                          user_name=user["name"] if user else "")


@app.route("/dashboard/<user_id>")
def dashboard(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        db.close()
        return render_template("index.html",
            STRIPE_KEY=STRIPE_PUBLIC,
            DOMAIN=DOMAIN,
            PRICE_STARTER=PRICES["starter"]["id"],
            PRICE_STANDARD=PRICES["standard"]["id"],
            PRICE_PRO=PRICES["pro"]["id"],
            PRICE_UNLIMITED=PRICES["unlimited"]["id"])
    
    apps = db.execute(
        "SELECT * FROM applications WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
        (user_id,)
    ).fetchall()
    db.close()
    
    return render_template("dashboard.html", user=user, apps=apps)

@app.route("/api/status/<user_id>")
def api_status(user_id):
    db = get_db()
    user = db.execute("SELECT tokens, email, name FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        db.close()
        return jsonify({"error": "not found"}), 404
    
    stats = db.execute(
        "SELECT status, COUNT(*) as count FROM applications WHERE user_id=? GROUP BY status",
        (user_id,)
    ).fetchall()
    
    apps = db.execute(
        "SELECT job_title, company, platform, status, url, created_at FROM applications WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
        (user_id,)
    ).fetchall()
    db.close()
    
    # Generate referral code
    ref_code = user_id[:8]
    
    return jsonify({
        "tokens": user["tokens"],
        "email": user["email"],
        "name": user["name"],
        "referral_code": ref_code,
        "referral_link": f"{DOMAIN}/?ref={ref_code}",
        "stats": {s["status"]: s["count"] for s in stats},
        "total": sum(s["count"] for s in stats),
        "recent": [dict(a) for a in apps]
    })


# ── REFERRAL SYSTEM (S4 Chesed) ──
@app.route("/api/referral/<ref_code>")
def apply_referral(ref_code):
    """Verify a referral code and return the referrer info."""
    db = get_db()
    # Find user whose ID starts with this code
    user = db.execute(
        "SELECT id, name, tokens FROM users WHERE id LIKE ?",
        (f"{ref_code}%",)
    ).fetchone()
    db.close()
    
    if not user:
        return jsonify({"valid": False, "message": "Invalid referral code"})
    
    return jsonify({
        "valid": True,
        "referrer_name": user.get("name", "ApplyBot user"),
        "bonus_tokens": 2
    })


@app.route("/api/redeem-referral", methods=["POST"])
def redeem_referral():
    """Award bonus tokens when someone signs up with a referral code."""
    data = request.json or {}
    ref_code = data.get("ref_code", "")
    new_user_id = data.get("user_id", "")
    
    if not ref_code or not new_user_id:
        return jsonify({"error": "ref_code and user_id required"}), 400
    
    db = get_db()
    
    # Find referrer
    referrer = db.execute(
        "SELECT id, tokens FROM users WHERE id LIKE ?",
        (f"{ref_code}%",)
    ).fetchone()
    
    if not referrer:
        db.close()
        return jsonify({"error": "Invalid referral code"}), 404
    
    # Award 2 bonus tokens to both referrer and new user
    db.execute("UPDATE users SET tokens = tokens + 2 WHERE id = ?", (referrer["id"],))
    db.execute("UPDATE users SET tokens = tokens + 2 WHERE id = ?", (new_user_id,))
    db.commit()
    db.close()
    
    log.info(f"Referral bonus: {ref_code} → {new_user_id} (+2 each)")
    
    return jsonify({
        "success": True,
        "bonus": 2,
        "message": "2 free applications added to your account!"
    })

# === RESUME PARSER ===
def extract_resume_text(path):
    try:
        import pymupdf
        doc = pymupdf.open(path)
        text = ""
        for page in doc:
            text += page.get_text()
        return text
    except:
        return ""

# ── AI RESUME ENHANCEMENT (S10 Keter — the crown jewel) ──

@app.route("/api/enhance-resume", methods=["POST"])
def api_enhance_resume():
    """Enhance a raw resume using AI. Optional target role/industry for optimization."""
    from resume_enhancer import enhance_resume
    
    data = request.json or {}
    raw_resume = data.get("resume_text", "")
    target_role = data.get("target_role", "")
    target_industry = data.get("target_industry", "")
    
    if not raw_resume:
        return jsonify({"error": "resume_text is required"}), 400
    
    try:
        result = enhance_resume(raw_resume, target_role, target_industry)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "fallback": raw_resume}), 500


@app.route("/api/tailor-resume", methods=["POST"])
def api_tailor_resume():
    """Tailor a resume to a specific job posting."""
    from resume_enhancer import tailor_to_job
    
    data = request.json or {}
    resume_text = data.get("resume_text", "")
    job_title = data.get("job_title", "")
    company = data.get("company", "")
    job_description = data.get("job_description", "")
    
    if not resume_text or not job_title:
        return jsonify({"error": "resume_text and job_title are required"}), 400
    
    try:
        result = tailor_to_job(resume_text, job_title, company, job_description)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/batch-tailor", methods=["POST"])
def api_batch_tailor():
    """
    Search for jobs matching criteria, enhance resume, and tailor for each job.
    Returns list of tailored resumes ready for submission.
    """
    from resume_enhancer import enhance_resume, tailor_to_job
    from search import search_jobs
    
    data = request.json or {}
    raw_resume = data.get("resume_text", "")
    location = data.get("location", "Remote")
    titles = data.get("titles", "")
    limit = data.get("limit", 5)
    
    if not raw_resume or not titles:
        return jsonify({"error": "resume_text and titles required"}), 400
    
    try:
        # Step 1: Search for jobs
        title_list = [t.strip() for t in titles.split(",")]
        all_jobs = []
        for title in title_list:
            jobs = search_jobs(title, location, limit=limit)
            all_jobs.extend(jobs)
        
        # Step 2: Enhance resume once
        enhanced = enhance_resume(raw_resume, target_role=title_list[0])
        enhanced_text = enhanced.get("enhanced_resume", raw_resume)
        
        # Step 3: Tailor for each job
        results = []
        for job in all_jobs[:limit]:
            tailored = tailor_to_job(
                enhanced_text,
                job.get("title", ""),
                job.get("company", ""),
                job.get("description", "")
            )
            results.append({
                "job": job,
                "tailored_resume": tailored.get("tailored_resume", enhanced_text),
                "match_score": tailored.get("match_score", 70),
                "keyword_matches": tailored.get("keyword_matches", []),
                "summary": tailored.get("summary_line", ""),
            })
        
        return jsonify({
            "enhanced_resume": enhanced_text,
            "changes": enhanced.get("changes_made", []),
            "improvement_score": enhanced.get("improvement_score", 6),
            "jobs_found": len(results),
            "tailored_applications": results,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# === STARTUP ===
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(host="0.0.0.0", port=5000, debug=True)
