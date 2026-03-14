from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password      = db.Column(db.String(256), nullable=False)
    is_admin      = db.Column(db.Boolean, default=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    files         = db.relationship('FileRecord', backref='owner', lazy=True)

class FileRecord(db.Model):
    __tablename__ = 'files'
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    original_name   = db.Column(db.String(256), nullable=False)
    file_size       = db.Column(db.Integer, nullable=False)
    md5_hash        = db.Column(db.String(32), nullable=False)
    dedup_key       = db.Column(db.String(64), nullable=False)
    is_duplicate    = db.Column(db.Boolean, default=False)
    stored_path     = db.Column(db.String(512), nullable=True)
    ref_file_id     = db.Column(db.Integer, db.ForeignKey('files.id'), nullable=True)
    uploaded_at     = db.Column(db.DateTime, default=datetime.utcnow)
    file_type       = db.Column(db.String(50), nullable=True)
    version         = db.Column(db.Integer, default=1)
    parent_id       = db.Column(db.Integer, db.ForeignKey('files.id'), nullable=True)
    versions        = db.relationship('FileRecord', foreign_keys='FileRecord.parent_id',
                                      backref=db.backref('parent', remote_side=[id]), lazy=True)

class ShareLink(db.Model):
    __tablename__ = 'share_links'
    id           = db.Column(db.Integer, primary_key=True)
    file_id      = db.Column(db.Integer, db.ForeignKey('files.id'), nullable=False)
    token        = db.Column(db.String(64), unique=True, nullable=False)
    password     = db.Column(db.String(256), nullable=True)
    expires_at   = db.Column(db.DateTime, nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    access_count = db.Column(db.Integer, default=0)

class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    action     = db.Column(db.String(64), nullable=False)
    filename   = db.Column(db.String(256), nullable=True)
    details    = db.Column(db.String(512), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    timestamp  = db.Column(db.DateTime, default=datetime.utcnow)

class StorageStats(db.Model):
    __tablename__ = 'storage_stats'
    id               = db.Column(db.Integer, primary_key=True)
    user_id          = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    total_uploaded   = db.Column(db.Integer, default=0)
    actual_stored    = db.Column(db.Integer, default=0)
    duplicates_found = db.Column(db.Integer, default=0)
    space_saved      = db.Column(db.Integer, default=0)

class DailyStats(db.Model):
    __tablename__ = 'daily_stats'
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    date        = db.Column(db.String(12), nullable=False)
    uploads     = db.Column(db.Integer, default=0)
    duplicates  = db.Column(db.Integer, default=0)
    space_saved = db.Column(db.Integer, default=0)
