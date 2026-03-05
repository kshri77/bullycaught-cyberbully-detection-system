from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
from flask_cors import CORS
from flask_mail import Mail, Message
import os
import random
import string
from models.cyberbully_detector import CyberbullyDetector

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})  # Restrict CORS in production
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-please-change-in-production")

# MongoDB
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"))
db = client.get_database("cyberbully_app")
users_collection = db["users"]
comments_collection = db["comments"]

# Flask-Mail
app.config.update(
    MAIL_SERVER='smtp.gmail.com',
    MAIL_PORT=587,
    MAIL_USE_TLS=True,
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_DEFAULT_SENDER=os.getenv("MAIL_DEFAULT_SENDER"),
    MAIL_USE_SSL=False
)
mail = Mail(app)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
AUTHORIZED_PERSON_EMAIL = os.getenv("AUTHORIZED_PERSON_EMAIL", "moderator@example.com")

# Load AI model
detector = CyberbullyDetector()
try:
    detector.load_model()
    print("✅ AI model loaded successfully.")
except Exception as e:
    print(f"❌ Model not loaded: {e}")
    raise RuntimeError("AI model failed to load. Exiting.")

# ---------------- ROUTES ----------------
@app.route('/')
def home():
    if 'username' in session:
        return render_template('index.html')
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username, password = request.form['username'], request.form['password']
        user = users_collection.find_one({"username": username})
        if user and check_password_hash(user['password'], password):
            session['username'] = username
            flash("Login successful!", "success")
            return redirect(url_for('home'))
        flash("Invalid credentials", "danger")
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        captcha_text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        session['captcha_text'] = captcha_text
        return render_template('register.html', captcha_text=captcha_text)
    username, email = request.form['username'], request.form['email']
    password, confirm = request.form['password'], request.form['confirm_password']
    captcha = request.form['captcha']
    if captcha != session.pop('captcha_text', None):
        flash("Invalid CAPTCHA", "danger")
        return redirect(url_for('register'))
    if password != confirm:
        flash("Passwords do not match!", "danger")
        return redirect(url_for('register'))
    if users_collection.find_one({"username": username}):
        flash("Username already exists", "danger")
        return redirect(url_for('register'))
    hashed_password = generate_password_hash(password)
    users_collection.insert_one({"username": username, "email": email, "password": hashed_password})
    flash("Registration successful! Please log in.", "success")
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.pop('username', None)
    flash("Logged out.", "info")
    return redirect(url_for('login'))

@app.route('/analyze', methods=['POST'])
def analyze():
    if 'username' not in session:
        flash("Login required.", "warning")
        return redirect(url_for('login'))
    text = request.form.get('text', '').strip()
    platform = request.form.get('platform', 'Unknown')
    if not text:
        return render_template('result.html', error="Please enter some text")
    result = detector.predict(text)
    result['platform'] = platform
    result['original_text'] = text
    result['interpretation'] = (
        "⚠️ High risk of cyberbullying." if result.get('risk_level') == 'high'
        else "⚡ Moderate risk." if result.get('risk_level') == 'medium'
        else "✅ Safe content."
    )
    comments_collection.insert_one({
        "username": session['username'],
        "text": text,
        "platform": platform,
        "risk_level": result.get('risk_level', 'unknown'),
        "alert_message": result.get('interpretation'),
        "risk_score": result.get('risk_score'),
        "confidence": result.get('confidence')
    })
    # 🚨 Email notifications
    user = users_collection.find_one({"username": session['username']})
    user_email = user.get("email") if user else None
    try:
        if result.get('risk_level') == 'medium' and user_email:
            msg = Message("⚡ Cyberbullying Warning", recipients=[user_email])
            msg.body = (
                f"Dear {session['username']},\n\n"
                f"Your recent comment may contain harmful language:\n\n"
                f"Platform: {platform}\n"
                f"Text: \"{text}\"\n\n"
                f"⚠️ Please be mindful of respectful communication."
            )
            mail.send(msg)
            print("✅ Medium-risk warning sent to user.")
        elif result.get('risk_level') == 'high':
            if user_email:
                msg_user = Message("🚨 High-Risk Cyberbullying Alert", recipients=[user_email])
                msg_user.body = (
                    f"Dear {session['username']},\n\n"
                    f"Our system flagged your comment as HIGH RISK cyberbullying:\n\n"
                    f"Platform: {platform}\n"
                    f"Text: \"{text}\"\n\n"
                    f"This behavior is not acceptable. Authorities may review this."
                )
                mail.send(msg_user)
                print("✅ High-risk alert sent to user.")
            msg_admin = Message("🚨 Cyberbullying Incident Detected", recipients=[ADMIN_EMAIL])
            msg_admin.body = (
                f"A HIGH RISK cyberbullying comment was detected.\n\n"
                f"User: {session['username']}\n"
                f"Platform: {platform}\n"
                f"Text: \"{text}\"\n\n"
                f"Risk Score: {result.get('risk_score')}\n"
                f"Confidence: {result.get('confidence')}\n"
            )
            mail.send(msg_admin)
            print("✅ High-risk alert sent to admin.")

            # 🔥 NEW: Send email to authorized person
            msg_authorized = Message(
                "🚨 High-Risk Cyberbullying Incident",
                recipients=[AUTHORIZED_PERSON_EMAIL]
            )
            msg_authorized.body = (
                f"A HIGH RISK cyberbullying incident was detected.\n\n"
                f"User: {session['username']}\n"
                f"Platform: {platform}\n"
                f"Text: \"{text}\"\n\n"
                f"Risk Score: {result.get('risk_score')}\n"
                f"Confidence: {result.get('confidence')}\n\n"
                f"Action Required: Review and take appropriate steps."
            )
            mail.send(msg_authorized)
            print("✅ High-risk alert sent to authorized person.")

    except Exception as e:
        print(f"❌ Email failed: {e}")
        app.logger.error(f"Email failed: {e}")
        flash("Comment analyzed, but notification email failed to send.", "warning")
    return render_template('result.html', result=result)

# ---------------- DASHBOARD ----------------
@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        flash("Please log in to access the dashboard", "warning")
        return redirect(url_for('login'))
    comments = list(comments_collection.find({}, {"_id": 0, "risk_level": 1, "platform": 1}))
    risk_counts = {"high": 0, "medium": 0, "low": 0}
    platform_counts = {}
    for comment in comments:
        risk_counts[comment.get("risk_level", "low")] += 1
        platform = comment.get("platform", "Unknown")
        platform_counts[platform] = platform_counts.get(platform, 0) + 1
    return render_template("dashboard.html", risk_counts=risk_counts, platform_counts=platform_counts)

# ---------------- PROFILE ----------------
@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'username' not in session:
        flash("Please log in to view your profile", "warning")
        return redirect(url_for('login'))
    user = users_collection.find_one({"username": session['username']})
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for('login'))
    if request.method == 'POST':
        current_password = request.form.get('currentPassword')
        new_password = request.form.get('newPassword')
        confirm_password = request.form.get('confirmPassword')
        if not all([current_password, new_password, confirm_password]):
            flash("All password fields are required.", "danger")
            return redirect(url_for('profile'))
        if not check_password_hash(user['password'], current_password):
            flash("Current password is incorrect.", "danger")
        elif new_password != confirm_password:
            flash("New passwords do not match.", "danger")
        else:
            hashed_password = generate_password_hash(new_password)
            users_collection.update_one({"username": session['username']}, {"$set": {"password": hashed_password}})
            flash("Password changed successfully!", "success")
            return redirect(url_for('profile'))
    comments = list(comments_collection.find({"username": session['username'], "risk_level": "high"},
                                     {"_id": 0, "text": 1, "alert_message": 1}))
    return render_template('profile.html', user=user, comments=comments)

# ---------------- API ----------------
@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({"error": "Missing 'text' in request body"}), 400
    text = data['text']
    platform = data.get('platform', 'Unknown')
    result = detector.predict(text)
    result['platform'] = platform
    result['original_text'] = text
    result['interpretation'] = (
        "⚠️ High risk of cyberbullying." if result.get('risk_level') == 'high'
        else "⚡ Moderate risk." if result.get('risk_level') == 'medium'
        else "✅ Safe content."
    )
    if 'username' in session:
        comments_collection.insert_one({
            "username": session['username'],
            "text": text,
            "platform": platform,
            "risk_level": result.get('risk_level', 'unknown'),
            "alert_message": result.get('interpretation'),
            "risk_score": result.get('risk_score'),
            "confidence": result.get('confidence')
        })

    # 🚨 Send email to authorized person if high risk
    if result.get('risk_level') == 'high':
        try:
            msg_authorized = Message(
                "🚨 High-Risk Cyberbullying Incident (API)",
                recipients=[AUTHORIZED_PERSON_EMAIL]
            )
            msg_authorized.body = (
                f"A HIGH RISK cyberbullying incident was detected via API.\n\n"
                f"User: {session.get('username', 'Anonymous')}\n"
                f"Platform: {platform}\n"
                f"Text: \"{text}\"\n\n"
                f"Risk Score: {result.get('risk_score')}\n"
                f"Confidence: {result.get('confidence')}\n\n"
                f"Action Required: Review and take appropriate steps."
            )
            mail.send(msg_authorized)
            print("✅ High-risk alert sent to authorized person (API).")
        except Exception as e:
            print(f"❌ Email to authorized person failed: {e}")
            app.logger.error(f"Email to authorized person failed: {e}")

    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True, port=5000)  # Set debug=False in production!

