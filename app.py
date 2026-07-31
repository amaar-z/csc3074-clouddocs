from flask import Flask, render_template, request, redirect, url_for, flash
import boto3
import pymysql
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = 'clouddocs-secret-2026'

# ── Load config from .env file ─────────────────────────────
S3_BUCKET = os.getenv('S3_BUCKET')
S3_REGION = os.getenv('S3_REGION', 'us-east-1')
RDS_HOST  = os.getenv('RDS_HOST')
RDS_USER  = os.getenv('RDS_USER')
RDS_PASS  = os.getenv('RDS_PASS')
RDS_DB    = os.getenv('RDS_DB')
print("===== ENVIRONMENT VARIABLES =====")
print("S3_BUCKET:", S3_BUCKET)
print("RDS_HOST:", RDS_HOST)
print("RDS_USER:", RDS_USER)
print("RDS_DB:", RDS_DB)
print("================================")

def get_db():
    """Open a connection to RDS MySQL"""
    return pymysql.connect(
        host=RDS_HOST,
        user=RDS_USER,
        password=RDS_PASS,
        database=RDS_DB,
        cursorclass=pymysql.cursors.DictCursor
    )

def get_s3():
    """Return S3 client — uses EC2 IAM role automatically (no keys needed)"""
    return boto3.client('s3', region_name=S3_REGION)

# ── Route: Homepage ────────────────────────────────────────
@app.route('/')
def index():
    """Show all uploaded files from the database"""
    try:
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM files ORDER BY upload_date DESC")
            files = cur.fetchall()
        db.close()
    except Exception as e:
        flash(f'Database error: {e}')
        files = []
    return render_template('index.html', files=files)

# ── Route: Upload File ─────────────────────────────────────
@app.route('/upload', methods=['POST'])
def upload():
    """Upload file to S3 and save metadata to RDS"""
    if 'file' not in request.files:
        flash('No file attached.')
        return redirect(url_for('index'))

    file = request.files['file']
    desc = request.form.get('description', '')

    if file.filename == '':
        flash('Please choose a file first.')
        return redirect(url_for('index'))

    try:
        # Build a unique S3 key with timestamp
        ts     = datetime.now().strftime('%Y%m%d_%H%M%S')
        s3_key = f'uploads/{ts}_{file.filename}'

        # Upload to S3 with AES-256 encryption
        get_s3().upload_fileobj(
            file, S3_BUCKET, s3_key,
            ExtraArgs={'ServerSideEncryption': 'AES256'}
        )

        # Save metadata to MySQL
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO files (filename, description, s3_key, upload_date)"
                " VALUES (%s, %s, %s, %s)",
                (file.filename, desc, s3_key, datetime.now())
            )
        db.commit()
        db.close()
        flash('✅ File uploaded successfully!')

    except Exception as e:
        flash(f'Upload failed: {e}')

    return redirect(url_for('index'))

# ── Route: Delete File ─────────────────────────────────────
@app.route('/delete/<int:file_id>', methods=['POST'])
def delete(file_id):
    """Delete file from S3 and remove metadata from RDS"""
    try:
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "SELECT s3_key FROM files WHERE id = %s", (file_id,)
            )
            row = cur.fetchone()
            if row:
                get_s3().delete_object(
                    Bucket=S3_BUCKET, Key=row['s3_key']
                )
                cur.execute(
                    "DELETE FROM files WHERE id = %s", (file_id,)
                )
        db.commit()
        db.close()
        flash('🗑️ File deleted.')
    except Exception as e:
        flash(f'Delete failed: {e}')
    return redirect(url_for('index'))

# ── Route: Health Check (for Load Balancer) ────────────────
@app.route('/health')
def health():
    """ALB pings this to check if EC2 is alive — must return 200"""
    return {'status': 'healthy', 'service': 'CloudDocs'}, 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=False)