from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, send_file, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from database import (mongo, get_user_by_email, get_user_by_username,
                      get_user_by_id, create_user, get_stats, update_stats,
                      create_file_record, get_file_by_id, get_file_by_dedup_key,
                      get_user_files, delete_file_record, log_action,
                      get_audit_logs, update_daily, get_daily_stats,
                      create_share_link, get_share_link, increment_share_access)
from deduplication import get_dedup_key, get_file_size_str, get_chunks
from encryption import encrypt_file, decrypt_file, encrypt_metadata, decrypt_metadata
from datetime import datetime, timedelta
from bson import ObjectId
from functools import wraps
from dotenv import load_dotenv
import os, io, secrets, json, traceback

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'clouddedupe_secret')
app.config['MONGO_URI'] = os.environ.get('MONGO_URI')
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_CONTENT_LENGTH', 52428800))

mongo.init_app(app)

# ── helpers ──────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def safe_decrypt(enc):
    try:
        return decrypt_metadata(enc)
    except Exception:
        return enc

def resolve_record(record):
    seen = set()
    while record and record.get('is_duplicate') and record.get('ref_file_id'):
        ref_id = record['ref_file_id']
        if ref_id in seen:
            break
        seen.add(ref_id)
        record = get_file_by_id(ref_id)
    return record

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
        user = get_user_by_email(email)
        if user and check_password_hash(user['password'], password):
            session['user_id']  = str(user['_id'])
            session['username'] = user['username']
            session['is_admin'] = user.get('is_admin', False)
            log_action(session['user_id'], 'LOGIN', ip=request.remote_addr)
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
        if get_user_by_email(email):
            error = 'Email already registered.'
        elif get_user_by_username(username):
            error = 'Username already taken.'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters.'
        else:
            user_id = create_user(username, email, generate_password_hash(password))
            session['user_id']  = user_id
            session['username'] = username
            session['is_admin'] = False
            return redirect(url_for('dashboard'))
    return render_template('register.html', error=error)

@app.route('/logout')
def logout():
    if 'user_id' in session:
        log_action(session['user_id'], 'LOGOUT', ip=request.remote_addr)
    session.clear()
    return redirect(url_for('login'))

# ── DASHBOARD ────────────────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    files   = get_user_files(user_id)
    stats   = get_stats(user_id)

    display_files = []
    for f in files:
        ver_count = mongo.db.files.count_documents({'parent_id': str(f['_id'])}) + 1
        display_files.append({
            'id':           str(f['_id']),
            'name':         safe_decrypt(f['original_name']),
            'size':         get_file_size_str(f['file_size']),
            'is_duplicate': f.get('is_duplicate', False),
            'file_type':    f.get('file_type', 'FILE'),
            'uploaded_at':  f['uploaded_at'].strftime('%d %b %Y, %I:%M %p'),
            'version':      f.get('version', 1),
            'ver_count':    ver_count,
            'md5':          f.get('md5_hash', ''),
            'sha256':       f.get('dedup_key', ''),
        })

    s          = stats or {}
    total_up   = s.get('total_uploaded',   0) or 0
    space_save = s.get('space_saved',      0) or 0
    actual     = s.get('actual_stored',    0) or 0
    dups       = s.get('duplicates_found', 0) or 0
    dedup_rate = round((space_save / total_up) * 100, 1) if total_up > 0 else 0

    chart_labels, chart_uploads, chart_saved = [], [], []
    for i in range(6, -1, -1):
        d   = (datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d')
        row = get_daily_stats(user_id, d)
        chart_labels.append((datetime.utcnow() - timedelta(days=i)).strftime('%d %b'))
        chart_uploads.append(int(row.get('uploads', 0)) if row else 0)
        chart_saved.append(round(float(row.get('space_saved', 0)) / 1024, 1) if row else 0)

    logs = get_audit_logs(user_id)

    return render_template('dashboard.html',
        username       = session['username'],
        is_admin       = session.get('is_admin', False),
        files          = display_files,
        total_uploaded = get_file_size_str(total_up),
        actual_stored  = get_file_size_str(actual),
        space_saved    = get_file_size_str(space_save),
        duplicates     = dups,
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

        # Check duplicate
        existing = get_file_by_dedup_key(dedup_key)
        update_stats(user_id, total_uploaded=file_size)

        if existing and existing.get('user_id') == user_id:
            log_action(user_id, 'DUPLICATE_DETECTED', original_name,
                      f'Size: {get_file_size_str(file_size)}', request.remote_addr)
            update_stats(user_id, duplicates_found=1, space_saved=file_size)
            update_daily(user_id, uploads=1, duplicates=1, space_saved=file_size)
            return jsonify({
                'status':  'duplicate',
                'message': f'"{original_name}" is an exact duplicate. Reference created.',
                'md5':     md5_hash,
                'sha256':  dedup_key,
                'saved':   get_file_size_str(file_size),
                'chunks':  len(chunks),
            })

        # Encrypt and store
        encrypted_bytes = encrypt_file(file_bytes)
        safe_fn         = f"{dedup_key[:16]}_{original_name}.enc"
        stored_path     = os.path.join(app.config['UPLOAD_FOLDER'], safe_fn)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        with open(stored_path, 'wb') as fout:
            fout.write(encrypted_bytes)

        enc_name = encrypt_metadata(original_name)

        # Check for existing version
        existing_name = None
        for fr in get_user_files(user_id):
            if safe_decrypt(fr['original_name']) == original_name:
                existing_name = fr
                break

        if existing_name:
            new_version = mongo.db.files.count_documents(
                {'parent_id': str(existing_name['_id'])}) + 2
            create_file_record({
                'user_id': user_id, 'original_name': enc_name,
                'file_size': file_size, 'md5_hash': md5_hash,
                'dedup_key': dedup_key, 'is_duplicate': False,
                'stored_path': stored_path, 'file_type': file_type,
                'version': new_version, 'parent_id': str(existing_name['_id']),
                'uploaded_at': datetime.utcnow(), 'ref_file_id': None
            })
            update_stats(user_id, actual_stored=file_size)
            update_daily(user_id, uploads=1)
            log_action(user_id, 'VERSION_UPLOADED', original_name,
                      f'v{new_version}', request.remote_addr)
            return jsonify({
                'status':  'versioned',
                'message': f'New version v{new_version} of "{original_name}" saved.',
                'md5': md5_hash, 'sha256': dedup_key,
                'size': get_file_size_str(file_size),
                'version': new_version, 'chunks': len(chunks),
            })

        # Brand new file
        create_file_record({
            'user_id': user_id, 'original_name': enc_name,
            'file_size': file_size, 'md5_hash': md5_hash,
            'dedup_key': dedup_key, 'is_duplicate': False,
            'stored_path': stored_path, 'file_type': file_type,
            'version': 1, 'parent_id': None, 'ref_file_id': None,
            'uploaded_at': datetime.utcnow()
        })
        update_stats(user_id, actual_stored=file_size)
        update_daily(user_id, uploads=1)
        log_action(user_id, 'FILE_UPLOADED', original_name,
                  f'Size: {get_file_size_str(file_size)}, Chunks: {len(chunks)}',
                  request.remote_addr)
        return jsonify({
            'status':  'uploaded',
            'message': f'"{original_name}" encrypted and stored successfully.',
            'md5': md5_hash, 'sha256': dedup_key,
            'size': get_file_size_str(file_size), 'chunks': len(chunks),
        })

    except Exception as e:
        print("UPLOAD ERROR:", traceback.format_exc())
        return jsonify({'error': f'Upload failed: {str(e)}'}), 500

# ── DOWNLOAD ─────────────────────────────────────────────────────────────────

@app.route('/download/<file_id>')
@login_required
def download(file_id):
    record = get_file_by_id(file_id)
    if not record:
        abort(404)
    if record['user_id'] != session['user_id']:
        abort(403)
    real = resolve_record(record)
    if not real or not real.get('stored_path'):
        abort(404)
    with open(real['stored_path'], 'rb') as f:
        data = f.read()
    decrypted = decrypt_file(data)
    filename  = safe_decrypt(record['original_name'])
    log_action(session['user_id'], 'FILE_DOWNLOADED', filename,
              ip=request.remote_addr)
    return send_file(io.BytesIO(decrypted), as_attachment=True,
                    download_name=filename)

# ── DELETE ───────────────────────────────────────────────────────────────────

@app.route('/delete/<file_id>', methods=['POST'])
@login_required
def delete(file_id):
    record = get_file_by_id(file_id)
    if not record:
        return jsonify({'error': 'Not found'}), 404
    if record['user_id'] != session['user_id']:
        return jsonify({'error': 'Unauthorized'}), 403

    name = safe_decrypt(record['original_name'])

    # Delete children
    for child in mongo.db.files.find({'parent_id': file_id}):
        if child.get('stored_path') and os.path.exists(child['stored_path']):
            os.remove(child['stored_path'])
        delete_file_record(str(child['_id']))

    # Delete actual file
    if not record.get('is_duplicate') and record.get('stored_path'):
        if os.path.exists(record['stored_path']):
            os.remove(record['stored_path'])
        update_stats(session['user_id'], actual_stored=-record['file_size'])
    else:
        update_stats(session['user_id'],
                    duplicates_found=-1,
                    space_saved=-record['file_size'])

    update_stats(session['user_id'], total_uploaded=-record['file_size'])
    log_action(session['user_id'], 'FILE_DELETED', name, ip=request.remote_addr)
    delete_file_record(file_id)
    return jsonify({'status': 'deleted'})

# ── VERSIONS ─────────────────────────────────────────────────────────────────

@app.route('/versions/<file_id>')
@login_required
def versions(file_id):
    record = get_file_by_id(file_id)
    if not record or record['user_id'] != session['user_id']:
        abort(403)
    all_versions = [record] + list(
        mongo.db.files.find({'parent_id': file_id}).sort('version', 1))
    return jsonify([{
        'id':      str(v['_id']),
        'version': v.get('version', 1),
        'size':    get_file_size_str(v['file_size']),
        'date':    v['uploaded_at'].strftime('%d %b %Y, %I:%M %p'),
        'md5':     v.get('md5_hash', '')
    } for v in all_versions])

# ── SHARE ─────────────────────────────────────────────────────────────────────

@app.route('/share/<file_id>', methods=['POST'])
@login_required
def create_share(file_id):
    record = get_file_by_id(file_id)
    if not record or record['user_id'] != session['user_id']:
        return jsonify({'error': 'Unauthorized'}), 403
    data       = request.get_json() or {}
    token      = secrets.token_urlsafe(32)
    expires_in = int(data.get('expires_hours', 24))
    password   = data.get('password', '').strip() or None
    create_share_link({
        'file_id':      file_id,
        'token':        token,
        'password':     generate_password_hash(password) if password else None,
        'expires_at':   datetime.utcnow() + timedelta(hours=expires_in),
        'created_at':   datetime.utcnow(),
        'access_count': 0
    })
    name = safe_decrypt(record['original_name'])
    log_action(session['user_id'], 'SHARE_CREATED', name,
              f'Expires in {expires_in}h', request.remote_addr)
    share_url = request.host_url.rstrip('/') + url_for('shared_download', token=token)
    return jsonify({'url': share_url, 'expires_in': expires_in, 'token': token})

@app.route('/s/<token>', methods=['GET', 'POST'])
def shared_download(token):
    link = get_share_link(token)
    if not link:
        abort(404)
    if link.get('expires_at') and datetime.utcnow() > link['expires_at']:
        return render_template('shared.html', error='This link has expired.')
    if link.get('password'):
        if request.method == 'GET':
            return render_template('shared.html', token=token, needs_password=True)
        pwd = request.form.get('password', '')
        if not check_password_hash(link['password'], pwd):
            return render_template('shared.html', token=token,
                                  needs_password=True, error='Wrong password.')
    record = get_file_by_id(link['file_id'])
    if not record:
        abort(404)
    real = resolve_record(record)
    if not real or not real.get('stored_path'):
        abort(404)
    with open(real['stored_path'], 'rb') as f:
        data = f.read()
    decrypted = decrypt_file(data)
    filename  = safe_decrypt(record['original_name'])
    increment_share_access(token)
    return send_file(io.BytesIO(decrypted), as_attachment=True,
                    download_name=filename)

# ── VERIFY ───────────────────────────────────────────────────────────────────

@app.route('/verify/<file_id>')
@login_required
def verify_integrity(file_id):
    record = get_file_by_id(file_id)
    if not record or record['user_id'] != session['user_id']:
        return jsonify({'error': 'Unauthorized'}), 403
    real = resolve_record(record)
    if not real or not real.get('stored_path') or not os.path.exists(real['stored_path']):
        return jsonify({'status': 'missing', 'ok': False})
    try:
        with open(real['stored_path'], 'rb') as f:
            enc_data = f.read()
        decrypted = decrypt_file(enc_data)
        _, recomputed = get_dedup_key(decrypted)
        ok = recomputed == real['dedup_key']
        log_action(session['user_id'], 'INTEGRITY_CHECK',
                  safe_decrypt(record['original_name']),
                  'PASS' if ok else 'FAIL', request.remote_addr)
        return jsonify({'status': 'ok' if ok else 'tampered', 'ok': ok,
                       'stored_hash': real['dedup_key'], 'recomputed': recomputed})
    except Exception as e:
        return jsonify({'status': 'error', 'ok': False, 'error': str(e)})

# ── ADMIN ─────────────────────────────────────────────────────────────────────

@app.route('/admin')
@login_required
def admin():
    if not session.get('is_admin'):
        abort(403)
    users      = list(mongo.db.users.find())
    all_logs   = list(mongo.db.audit_logs.find().sort('timestamp', -1).limit(50))
    total_files  = mongo.db.files.count_documents({})
    total_dups   = mongo.db.files.count_documents({'is_duplicate': True})
    stats_agg    = list(mongo.db.storage_stats.aggregate([
        {'$group': {'_id': None,
                    'total_saved':   {'$sum': '$space_saved'},
                    'total_stored':  {'$sum': '$actual_stored'}}}
    ]))
    agg = stats_agg[0] if stats_agg else {}
    return render_template('admin.html',
        users=users, logs=all_logs,
        total_files=total_files, total_dups=total_dups,
        total_saved=get_file_size_str(agg.get('total_saved', 0)),
        total_stored=get_file_size_str(agg.get('total_stored', 0)),
    )

@app.route('/admin/make_admin/<user_id>', methods=['POST'])
@login_required
def make_admin(user_id):
    if not session.get('is_admin'):
        abort(403)
    user = get_user_by_id(user_id)
    new_status = not user.get('is_admin', False)
    mongo.db.users.update_one(
        {'_id': ObjectId(user_id)},
        {'$set': {'is_admin': new_status}}
    )
    return jsonify({'status': 'ok', 'is_admin': new_status})

if __name__ == '__main__':
    app.run(debug=True)