from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, send_file, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from database import db, User, FileRecord, StorageStats, ShareLink, AuditLog, DailyStats
from deduplication import get_dedup_key, get_file_size_str, get_chunks
from encryption import encrypt_file, decrypt_file, encrypt_metadata, decrypt_metadata
from datetime import datetime, timedelta
from functools import wraps
import os, io, secrets, json, traceback

import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'clouddedupe_master_2024_secret')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///clouddedupe.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_CONTENT_LENGTH', 52428800))

db.init_app(app)
with app.app_context():
    db.create_all()

# ── helpers ──────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = User.query.get(session['user_id'])
        if not user or not user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated

def log_action(action, filename=None, details=None):
    entry = AuditLog(
        user_id    = session.get('user_id'),
        action     = action,
        filename   = filename,
        details    = details,
        ip_address = request.remote_addr
    )
    db.session.add(entry)

def update_daily(user_id, uploads=0, duplicates=0, space_saved=0):
    today = datetime.utcnow().strftime('%Y-%m-%d')
    row = DailyStats.query.filter_by(user_id=user_id, date=today).first()
    if not row:
        row = DailyStats(
            user_id=user_id,
            date=today,
            uploads=0,
            duplicates=0,
            space_saved=0
        )
        db.session.add(row)
        db.session.flush()
    row.uploads     = (row.uploads     or 0) + uploads
    row.duplicates  = (row.duplicates  or 0) + duplicates
    row.space_saved = (row.space_saved or 0) + space_saved

def safe_decrypt(enc):
    try:
        return decrypt_metadata(enc)
    except Exception:
        return enc

def resolve_record(record):
    seen = set()
    while record.is_duplicate and record.ref_file_id:
        if record.ref_file_id in seen:
            break
        seen.add(record.ref_file_id)
        record = FileRecord.query.get(record.ref_file_id)
        if not record:
            return None
    return record

def fix_stats(stats):
    """Ensure no None values in stats fields."""
    stats.total_uploaded   = stats.total_uploaded   or 0
    stats.actual_stored    = stats.actual_stored    or 0
    stats.duplicates_found = stats.duplicates_found or 0
    stats.space_saved      = stats.space_saved      or 0
    return stats

# ── AUTH ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            session['user_id']  = user.id
            session['username'] = user.username
            session['is_admin'] = user.is_admin
            log_action('LOGIN')
            db.session.commit()
            return redirect(url_for('dashboard'))
        error = 'Invalid email or password.'
    return render_template('login.html', error=error)

@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        if User.query.filter_by(email=email).first():
            error = 'Email already registered.'
        elif User.query.filter_by(username=username).first():
            error = 'Username already taken.'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters.'
        else:
            user = User(username=username, email=email,
                        password=generate_password_hash(password))
            db.session.add(user)
            db.session.flush()
            db.session.add(StorageStats(
                user_id=user.id,
                total_uploaded=0,
                actual_stored=0,
                duplicates_found=0,
                space_saved=0
            ))
            db.session.commit()
            session['user_id']  = user.id
            session['username'] = user.username
            session['is_admin'] = False
            return redirect(url_for('dashboard'))
    return render_template('register.html', error=error)

@app.route('/logout')
def logout():
    if 'user_id' in session:
        log_action('LOGOUT')
        db.session.commit()
    session.clear()
    return redirect(url_for('login'))

# ── DASHBOARD ────────────────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    files   = (FileRecord.query
               .filter_by(user_id=user_id, parent_id=None)
               .order_by(FileRecord.uploaded_at.desc()).all())
    stats = StorageStats.query.filter_by(user_id=user_id).first()
    if not stats:
        stats = StorageStats(user_id=user_id, total_uploaded=0,
                             actual_stored=0, duplicates_found=0, space_saved=0)
        db.session.add(stats)
        db.session.commit()
    stats = fix_stats(stats)

    display_files = []
    for f in files:
        ver_count = FileRecord.query.filter_by(parent_id=f.id).count() + 1
        display_files.append({
            'id':           f.id,
            'name':         safe_decrypt(f.original_name),
            'size':         get_file_size_str(f.file_size),
            'size_bytes':   f.file_size,
            'is_duplicate': f.is_duplicate,
            'file_type':    f.file_type or 'FILE',
            'uploaded_at':  f.uploaded_at.strftime('%d %b %Y, %I:%M %p'),
            'version':      f.version,
            'ver_count':    ver_count,
            'md5':          f.md5_hash,
            'sha256':       f.dedup_key,
        })

    dedup_rate = round((stats.space_saved / stats.total_uploaded) * 100, 1) if stats.total_uploaded > 0 else 0

    chart_labels, chart_uploads, chart_saved = [], [], []
    for i in range(6, -1, -1):
        d = (datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d')
        row = DailyStats.query.filter_by(user_id=user_id, date=d).first()
        chart_labels.append((datetime.utcnow() - timedelta(days=i)).strftime('%d %b'))
        chart_uploads.append(int(row.uploads or 0) if row else 0)
        chart_saved.append(round(float(row.space_saved or 0) / 1024, 1) if row else 0)
        logs = (AuditLog.query.filter_by(user_id=user_id)
                .order_by(AuditLog.timestamp.desc()).limit(8).all())

    return render_template('dashboard.html',
        username       = session['username'],
        is_admin       = session.get('is_admin', False),
        files          = display_files,
        total_uploaded = get_file_size_str(stats.total_uploaded),
        actual_stored  = get_file_size_str(stats.actual_stored),
        space_saved    = get_file_size_str(stats.space_saved),
        duplicates     = stats.duplicates_found,
        dedup_rate     = dedup_rate,
        chart_labels   = json.dumps(chart_labels),
        chart_uploads  = json.dumps(chart_uploads),
        chart_saved    = json.dumps(chart_saved),
        audit_logs     = logs,
    )

# ── UPLOAD ───────────────────────────────────────────────────────────────────

@app.route('/upload', methods=['POST'])
@login_required
def upload():
    try:
        user_id = session['user_id']
        file    = request.files.get('file')
        if not file or file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        file_bytes    = file.read()
        file_size     = len(file_bytes)
        raw_name      = file.filename or 'upload'
        original_name = secure_filename(raw_name) or 'upload'
        file_type     = original_name.rsplit('.', 1)[-1].upper() if '.' in original_name else 'FILE'

        md5_hash, dedup_key = get_dedup_key(file_bytes)
        chunks = get_chunks(file_bytes)

        # Auto-create stats row if missing
        stats = StorageStats.query.filter_by(user_id=user_id).first()
        if not stats:
            stats = StorageStats(user_id=user_id, total_uploaded=0,
                                 actual_stored=0, duplicates_found=0, space_saved=0)
            db.session.add(stats)
            db.session.flush()
        stats = fix_stats(stats)

        # Check for existing version (same name, same user)
        existing_name_record = None
        for fr in FileRecord.query.filter_by(user_id=user_id, parent_id=None).all():
            try:
                if safe_decrypt(fr.original_name) == original_name:
                    existing_name_record = fr
                    break
            except Exception:
                continue

        existing = FileRecord.query.filter_by(dedup_key=dedup_key).first()
        stats.total_uploaded += file_size

        if existing and existing.user_id == user_id and existing.dedup_key == dedup_key:
            # Exact duplicate
            log_action('DUPLICATE_DETECTED', original_name, f'Size: {get_file_size_str(file_size)}')
            stats.duplicates_found += 1
            stats.space_saved      += file_size
            update_daily(user_id, uploads=1, duplicates=1, space_saved=file_size)
            db.session.commit()
            return jsonify({
                'status':  'duplicate',
                'message': f'"{original_name}" is an exact duplicate. Reference created — no storage used.',
                'md5':     md5_hash,
                'sha256':  dedup_key,
                'saved':   get_file_size_str(file_size),
                'chunks':  len(chunks),
            })

        # Encrypt and store the file
        encrypted_bytes = encrypt_file(file_bytes)
        safe_fn         = f"{dedup_key[:16]}_{original_name}.enc"
        stored_path     = os.path.join(app.config['UPLOAD_FOLDER'], safe_fn)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        with open(stored_path, 'wb') as fout:
            fout.write(encrypted_bytes)

        enc_name = encrypt_metadata(original_name)

        if existing_name_record:
            # New version of existing file
            new_version = FileRecord.query.filter_by(parent_id=existing_name_record.id).count() + 2
            new_record  = FileRecord(
                user_id=user_id, original_name=enc_name, file_size=file_size,
                md5_hash=md5_hash, dedup_key=dedup_key, is_duplicate=False,
                stored_path=stored_path, file_type=file_type,
                version=new_version, parent_id=existing_name_record.id
            )
            db.session.add(new_record)
            stats.actual_stored += file_size
            update_daily(user_id, uploads=1)
            log_action('VERSION_UPLOADED', original_name, f'v{new_version}')
            db.session.commit()
            return jsonify({
                'status':  'versioned',
                'message': f'New version v{new_version} of "{original_name}" saved.',
                'md5':     md5_hash,
                'sha256':  dedup_key,
                'size':    get_file_size_str(file_size),
                'version': new_version,
                'chunks':  len(chunks),
            })

        # Brand new file
        new_record = FileRecord(
            user_id=user_id, original_name=enc_name, file_size=file_size,
            md5_hash=md5_hash, dedup_key=dedup_key, is_duplicate=False,
            stored_path=stored_path, file_type=file_type, version=1
        )
        db.session.add(new_record)
        stats.actual_stored += file_size
        update_daily(user_id, uploads=1)
        log_action('FILE_UPLOADED', original_name, f'Size: {get_file_size_str(file_size)}, Chunks: {len(chunks)}')
        db.session.commit()
        return jsonify({
            'status':  'uploaded',
            'message': f'"{original_name}" encrypted and stored successfully.',
            'md5':     md5_hash,
            'sha256':  dedup_key,
            'size':    get_file_size_str(file_size),
            'chunks':  len(chunks),
        })

    except Exception as e:
        db.session.rollback()
        print("UPLOAD ERROR:", traceback.format_exc())
        return jsonify({'error': f'Upload failed: {str(e)}'}), 500

# ── DOWNLOAD ─────────────────────────────────────────────────────────────────

@app.route('/download/<int:file_id>')
@login_required
def download(file_id):
    record = FileRecord.query.get_or_404(file_id)
    if record.user_id != session['user_id']:
        abort(403)
    real = resolve_record(record)
    if not real or not real.stored_path:
        abort(404)
    with open(real.stored_path, 'rb') as f:
        data = f.read()
    decrypted = decrypt_file(data)
    filename  = safe_decrypt(record.original_name)
    log_action('FILE_DOWNLOADED', filename)
    db.session.commit()
    return send_file(io.BytesIO(decrypted), as_attachment=True, download_name=filename)

# ── DELETE ───────────────────────────────────────────────────────────────────

@app.route('/delete/<int:file_id>', methods=['POST'])
@login_required
def delete(file_id):
    record = FileRecord.query.get_or_404(file_id)
    if record.user_id != session['user_id']:
        return jsonify({'error': 'Unauthorized'}), 403
    stats = StorageStats.query.filter_by(user_id=session['user_id']).first()
    if stats:
        stats = fix_stats(stats)
    name = safe_decrypt(record.original_name)

    for child in FileRecord.query.filter_by(parent_id=record.id).all():
        if child.stored_path and os.path.exists(child.stored_path):
            refs = FileRecord.query.filter_by(ref_file_id=child.id).count()
            if refs == 0:
                os.remove(child.stored_path)
        db.session.delete(child)

    if not record.is_duplicate and record.stored_path:
        refs = FileRecord.query.filter_by(ref_file_id=record.id).count()
        if refs == 0 and os.path.exists(record.stored_path):
            os.remove(record.stored_path)

    if stats:
        if record.is_duplicate:
            stats.duplicates_found = max(0, stats.duplicates_found - 1)
            stats.space_saved      = max(0, stats.space_saved - record.file_size)
        else:
            stats.actual_stored = max(0, stats.actual_stored - record.file_size)
        stats.total_uploaded = max(0, stats.total_uploaded - record.file_size)

    log_action('FILE_DELETED', name)
    db.session.delete(record)
    db.session.commit()
    return jsonify({'status': 'deleted'})

# ── VERSIONS ─────────────────────────────────────────────────────────────────

@app.route('/versions/<int:file_id>')
@login_required
def versions(file_id):
    record = FileRecord.query.get_or_404(file_id)
    if record.user_id != session['user_id']:
        abort(403)
    all_versions = [record] + FileRecord.query.filter_by(parent_id=record.id).order_by(FileRecord.version).all()
    result = [{'id': v.id, 'version': v.version,
               'size': get_file_size_str(v.file_size),
               'date': v.uploaded_at.strftime('%d %b %Y, %I:%M %p'),
               'md5':  v.md5_hash} for v in all_versions]
    return jsonify(result)

# ── SHARE LINKS ──────────────────────────────────────────────────────────────

@app.route('/share/<int:file_id>', methods=['POST'])
@login_required
def create_share(file_id):
    record = FileRecord.query.get_or_404(file_id)
    if record.user_id != session['user_id']:
        return jsonify({'error': 'Unauthorized'}), 403
    data       = request.get_json() or {}
    token      = secrets.token_urlsafe(32)
    expires_in = int(data.get('expires_hours', 24))
    password   = data.get('password', '').strip() or None
    link = ShareLink(
        file_id    = file_id,
        token      = token,
        password   = generate_password_hash(password) if password else None,
        expires_at = datetime.utcnow() + timedelta(hours=expires_in)
    )
    db.session.add(link)
    name = safe_decrypt(record.original_name)
    log_action('SHARE_CREATED', name, f'Expires in {expires_in}h')
    db.session.commit()
    share_url = request.host_url.rstrip('/') + url_for('shared_download', token=token)
    return jsonify({'url': share_url, 'expires_in': expires_in, 'token': token})

@app.route('/s/<token>', methods=['GET', 'POST'])
def shared_download(token):
    link = ShareLink.query.filter_by(token=token).first_or_404()
    if link.expires_at and datetime.utcnow() > link.expires_at:
        return render_template('shared.html', error='This link has expired.')
    if link.password:
        if request.method == 'GET':
            return render_template('shared.html', token=token, needs_password=True)
        pwd = request.form.get('password', '')
        if not check_password_hash(link.password, pwd):
            return render_template('shared.html', token=token, needs_password=True, error='Wrong password.')
    record = FileRecord.query.get_or_404(link.file_id)
    real   = resolve_record(record)
    if not real or not real.stored_path:
        abort(404)
    with open(real.stored_path, 'rb') as f:
        data = f.read()
    decrypted = decrypt_file(data)
    filename  = safe_decrypt(record.original_name)
    link.access_count += 1
    log_action(None, filename, f'Shared download via token {token[:8]}')
    db.session.commit()
    return send_file(io.BytesIO(decrypted), as_attachment=True, download_name=filename)

# ── ADMIN PANEL ──────────────────────────────────────────────────────────────

@app.route('/admin')
@admin_required
def admin():
    users        = User.query.all()
    all_logs     = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(50).all()
    total_files  = FileRecord.query.count()
    total_dups   = FileRecord.query.filter_by(is_duplicate=True).count()
    total_saved  = db.session.query(db.func.sum(StorageStats.space_saved)).scalar()  or 0
    total_stored = db.session.query(db.func.sum(StorageStats.actual_stored)).scalar() or 0
    return render_template('admin.html',
        users=users, logs=all_logs,
        total_files=total_files, total_dups=total_dups,
        total_saved=get_file_size_str(total_saved),
        total_stored=get_file_size_str(total_stored),
    )

@app.route('/admin/make_admin/<int:user_id>', methods=['POST'])
@admin_required
def make_admin(user_id):
    user = User.query.get_or_404(user_id)
    user.is_admin = not user.is_admin
    db.session.commit()
    return jsonify({'status': 'ok', 'is_admin': user.is_admin})

# ── INTEGRITY CHECK ──────────────────────────────────────────────────────────

@app.route('/verify/<int:file_id>')
@login_required
def verify_integrity(file_id):
    record = FileRecord.query.get_or_404(file_id)
    if record.user_id != session['user_id']:
        return jsonify({'error': 'Unauthorized'}), 403
    real = resolve_record(record)
    if not real or not real.stored_path or not os.path.exists(real.stored_path):
        return jsonify({'status': 'missing', 'ok': False})
    with open(real.stored_path, 'rb') as f:
        enc_data = f.read()
    try:
        decrypted = decrypt_file(enc_data)
        _, recomputed_key = get_dedup_key(decrypted)
        ok = recomputed_key == real.dedup_key
        log_action('INTEGRITY_CHECK', safe_decrypt(record.original_name), 'PASS' if ok else 'FAIL')
        db.session.commit()
        return jsonify({'status': 'ok' if ok else 'tampered', 'ok': ok,
                        'stored_hash': real.dedup_key, 'recomputed': recomputed_key})
    except Exception as e:
        return jsonify({'status': 'error', 'ok': False, 'error': str(e)})

# ── ANALYTICS API ────────────────────────────────────────────────────────────

@app.route('/api/chart-data')
@login_required
def chart_data():
    user_id = session['user_id']
    labels, uploads, saved = [], [], []
    for i in range(6, -1, -1):
        d   = (datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d')
        row = DailyStats.query.filter_by(user_id=user_id, date=d).first()
        labels.append((datetime.utcnow() - timedelta(days=i)).strftime('%d %b'))
        uploads.append(row.uploads if row else 0)
        saved.append(round((row.space_saved or 0) / 1024, 1) if row else 0)
    return jsonify({'labels': labels, 'uploads': uploads, 'saved': saved})

if __name__ == '__main__':
    app.run(debug=True)
    