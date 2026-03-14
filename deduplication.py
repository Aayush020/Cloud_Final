import hashlib

CHUNK_SIZE = 4 * 1024  # 4KB chunks

def generate_md5(file_bytes):
    md5 = hashlib.md5()
    md5.update(file_bytes)
    return md5.hexdigest()

def generate_sha256_of_md5(md5_hash):
    sha256 = hashlib.sha256()
    sha256.update(md5_hash.encode('utf-8'))
    return sha256.hexdigest()

def get_dedup_key(file_bytes):
    """Full hybrid pipeline: File -> MD5 -> SHA256(MD5)"""
    md5 = generate_md5(file_bytes)
    dedup_key = generate_sha256_of_md5(md5)
    return md5, dedup_key

def get_chunks(file_bytes):
    """Split file into chunks and return list of (chunk_index, chunk_hash)"""
    chunks = []
    for i in range(0, len(file_bytes), CHUNK_SIZE):
        chunk = file_bytes[i:i + CHUNK_SIZE]
        chunk_md5 = generate_md5(chunk)
        chunk_key  = generate_sha256_of_md5(chunk_md5)
        chunks.append({'index': i // CHUNK_SIZE, 'md5': chunk_md5, 'key': chunk_key, 'size': len(chunk)})
    return chunks

def get_file_size_str(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
