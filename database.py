from flask_pymongo import PyMongo
from datetime import datetime

mongo = PyMongo()

def get_user_by_email(email):
    return mongo.db.users.find_one({'email': email})

def get_user_by_username(username):
    return mongo.db.users.find_one({'username': username})

def get_user_by_id(user_id):
    from bson import ObjectId
    return mongo.db.users.find_one({'_id': ObjectId(user_id)})

def create_user(username, email, password_hash):
    user = {
        'username':   username,
        'email':      email,
        'password':   password_hash,
        'is_admin':   False,
        'created_at': datetime.utcnow()
    }
    result = mongo.db.users.insert_one(user)
    create_stats(str(result.inserted_id))
    return str(result.inserted_id)

def create_stats(user_id):
    mongo.db.storage_stats.insert_one({
        'user_id':         user_id,
        'total_uploaded':  0,
        'actual_stored':   0,
        'duplicates_found':0,
        'space_saved':     0
    })

def get_stats(user_id):
    stats = mongo.db.storage_stats.find_one({'user_id': user_id})
    if not stats:
        create_stats(user_id)
        stats = mongo.db.storage_stats.find_one({'user_id': user_id})
    return stats

def update_stats(user_id, **kwargs):
    mongo.db.storage_stats.update_one(
        {'user_id': user_id},
        {'$inc': kwargs}
    )

def create_file_record(data):
    result = mongo.db.files.insert_one(data)
    return str(result.inserted_id)

def get_file_by_id(file_id):
    from bson import ObjectId
    return mongo.db.files.find_one({'_id': ObjectId(file_id)})

def get_file_by_dedup_key(dedup_key):
    return mongo.db.files.find_one({'dedup_key': dedup_key})

def get_user_files(user_id):
    return list(mongo.db.files.find(
        {'user_id': user_id, 'parent_id': None}
    ).sort('uploaded_at', -1))

def delete_file_record(file_id):
    from bson import ObjectId
    mongo.db.files.delete_one({'_id': ObjectId(file_id)})

def log_action(user_id, action, filename=None, details=None, ip=None):
    mongo.db.audit_logs.insert_one({
        'user_id':    user_id,
        'action':     action,
        'filename':   filename,
        'details':    details,
        'ip_address': ip,
        'timestamp':  datetime.utcnow()
    })

def get_audit_logs(user_id, limit=8):
    return list(mongo.db.audit_logs.find(
        {'user_id': user_id}
    ).sort('timestamp', -1).limit(limit))

def update_daily(user_id, uploads=0, duplicates=0, space_saved=0):
    today = datetime.utcnow().strftime('%Y-%m-%d')
    mongo.db.daily_stats.update_one(
        {'user_id': user_id, 'date': today},
        {'$inc': {
            'uploads':     uploads,
            'duplicates':  duplicates,
            'space_saved': space_saved
        }},
        upsert=True
    )

def get_daily_stats(user_id, date):
    return mongo.db.daily_stats.find_one({'user_id': user_id, 'date': date})

def create_share_link(data):
    result = mongo.db.share_links.insert_one(data)
    return str(result.inserted_id)

def get_share_link(token):
    return mongo.db.share_links.find_one({'token': token})

def increment_share_access(token):
    mongo.db.share_links.update_one(
        {'token': token},
        {'$inc': {'access_count': 1}}
    )