from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, send_file
from flask_cors import CORS
import os
import requests
from datetime import datetime
import time
from werkzeug.utils import secure_filename
from functools import wraps
import logging
import ssl
import tempfile
import uuid
import threading
import shutil


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')
CORS(app)

SLACK_CLIENT_ID = os.environ.get('SLACK_CLIENT_ID')
SLACK_CLIENT_SECRET = os.environ.get('SLACK_CLIENT_SECRET')
SLACK_REDIRECT_URI = os.environ.get('SLACK_REDIRECT_URI')
HACKCLUB_CDN_TOKEN = os.environ.get('HACKCLUB_CDN_TOKEN')
AIRTABLE_BASE_ID = os.environ.get('AIRTABLE_BASE_ID')
AIRTABLE_TABLE_NAME=os.environ.get('AIRTABLE_TABLE_NAME')
AIRTABLE_API_KEY = os.environ.get('AIRTABLE_API_KEY')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mov', 'avi', 'webm'}

TEMP_DIR = tempfile.gettempdir()
temp_files = {} 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def cleanup_temp_file(file_id, delay=300):
    """Clean up temporary file after delay (5 minutes by default)"""
    def cleanup():
        time.sleep(delay)
        if file_id in temp_files:
            file_path = temp_files[file_id]
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Cleaned up temporary file: {file_path}")
            del temp_files[file_id]
    
    thread = threading.Thread(target=cleanup)
    thread.daemon = True
    thread.start()

@app.route('/temp/<file_id>')
def serve_temp_file(file_id):
    """Serve temporary files for CDN upload"""
    if file_id in temp_files:
        file_path = temp_files[file_id]
        if os.path.exists(file_path):
            return send_file(file_path)
    return "File not found", 404

def create_temp_file_url(file_path):
    """Create a temporary accessible URL for the file using our own server"""
    try:
        file_id = str(uuid.uuid4())
        
        temp_file_path = os.path.join(TEMP_DIR, f"temp_{file_id}_{os.path.basename(file_path)}")
        
        shutil.copy2(file_path, temp_file_path)
        
        temp_files[file_id] = temp_file_path
        
        base_url = request.url_root if request else "http://localhost:5000/"
        temp_url = f"{base_url}temp/{file_id}"
        
        logger.info(f"Created temp URL: {temp_url}")
        
        cleanup_temp_file(file_id)
        
        return temp_url
        
    except Exception as e:
        logger.error(f"Error creating temp file URL: {str(e)}")
        return None

def upload_to_hackclub_cdn(file_path):
    """Upload file to Hack Club CDN using the V3 API"""
    try:
        temp_url = create_temp_file_url(file_path)
        if not temp_url:
            logger.error("Failed to create temporary URL for file")
            return None
        
        url = 'https://cdn.hackclub.com/api/v3/new'
        headers = {
            'Authorization': f'Bearer {HACKCLUB_CDN_TOKEN}',
            'Content-Type': 'application/json'
        }
        
        payload = [temp_url]
        
        logger.info(f"Uploading to CDN: {os.path.basename(file_path)}")
        logger.info(f"Using temp URL: {temp_url}")
        
        response = requests.post(url, headers=headers, json=payload)
        
        logger.info(f"CDN response status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            logger.info(f"CDN response: {data}")
            
            if 'files' in data and len(data['files']) > 0:
                deployed_url = data['files'][0]['deployedUrl']
                original_filename = os.path.basename(file_path)
                cdn_filename = data['files'][0].get('file', 'unknown')
                
                logger.info(f"Successfully uploaded: {original_filename} -> {cdn_filename}")
                logger.info(f"Deployed URL: {deployed_url}")
                
                return deployed_url
            else:
                logger.error(f"Unexpected response format: {data}")
                return None
        else:
            logger.error(f"CDN upload failed with status {response.status_code}: {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Error uploading to CDN: {str(e)}")
        return None

def upload_file_to_cdn_alternative(file_path):
    """
    Alternative approach: Upload to a temporary hosting service first,
    then use that URL with the Hack Club CDN.
    """
    try:
        with open(file_path, 'rb') as f:
            files = {'file': f}
            
            temp_response = requests.post('https://tmpfiles.org/api/v1/upload', files=files)
            
            if temp_response.status_code == 200:
                temp_data = temp_response.json()
                if temp_data.get('status') == 'success':
                    temp_url = temp_data['data']['url'].replace('tmpfiles.org/', 'tmpfiles.org/dl/')
                    
                    logger.info(f"Uploaded to tmpfiles.org: {temp_url}")
                    
                    cdn_url = 'https://cdn.hackclub.com/api/v3/new'
                    headers = {
                        'Authorization': f'Bearer {HACKCLUB_CDN_TOKEN}',
                        'Content-Type': 'application/json'
                    }
                    
                    payload = [temp_url]
                    cdn_response = requests.post(cdn_url, headers=headers, json=payload)
                    
                    if cdn_response.status_code == 200:
                        cdn_data = cdn_response.json()
                        if 'files' in cdn_data and len(cdn_data['files']) > 0:
                            deployed_url = cdn_data['files'][0]['deployedUrl']
                            logger.info(f"Successfully uploaded to CDN: {deployed_url}")
                            return deployed_url
                    else:
                        logger.error(f"CDN upload failed: {cdn_response.text}")
                        return None
                else:
                    logger.error(f"Temporary upload failed: {temp_data}")
                    return None
            else:
                logger.error(f"Temporary upload failed with status {temp_response.status_code}")
                return None
                
    except Exception as e:
        logger.error(f"Error in alternative upload: {str(e)}")
        return None

def save_to_airtable(log_data):
    """Save dev log entry to Airtable using Personal Access Token"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
        headers = {
            'Authorization': f'Bearer {AIRTABLE_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        data = {
            'fields': {
                'User ID': log_data['user_id'],
                'User Name': log_data['user_name'],
                'Project Name': log_data['project_name'],
                'Project Tag': log_data['project_tag'],
                'Description': log_data['description'],
                'What I Did': log_data['what_did'],
                'Could Have Done Better': log_data['could_improve'],
                'What I Can Improve': log_data['can_improve'],
                'Next Steps': log_data['next_steps'],
                'Time Spent (minutes)': log_data['time_spent'],
                'Media URL': log_data.get('media_url', ''),
                'Created At': log_data['created_at']
            }
        }
        
        response = requests.post(url, headers=headers, json=data)
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Airtable save failed: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error saving to Airtable: {str(e)}")
        return None

@app.route('/api/logs')
@login_required
def get_logs():
    """Get user's dev logs from Airtable using Personal Access Token"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        params = {
            'filterByFormula': f"{{User ID}} = '{session['user_id']}'",
            'sort[0][field]': 'Created At',
            'sort[0][direction]': 'desc'
        }
        
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            data = response.json()
            return jsonify(data['records'])
        else:
            logger.error(f"Airtable fetch failed: {response.text}")
            return jsonify([])
            
    except Exception as e:
        logger.error(f"Error fetching logs: {str(e)}")
        return jsonify([])

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('dashboard.html', user=session)

@app.route('/login')
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    
    slack_auth_url = f"https://slack.com/oauth/v2/authorize?client_id={SLACK_CLIENT_ID}&scope=users:read&redirect_uri={SLACK_REDIRECT_URI}"
    return render_template('login.html', auth_url=slack_auth_url)

@app.route('/auth/callback')
def auth_callback():
    code = request.args.get('code')
    if not code:
        flash('Authentication failed. Please try again.', 'error')
        return redirect(url_for('login'))
    
    try:
        response = requests.post('https://slack.com/api/oauth.v2.access', data={
            'client_id': SLACK_CLIENT_ID,
            'client_secret': SLACK_CLIENT_SECRET,
            'code': code,
            'redirect_uri': SLACK_REDIRECT_URI
        })
        
        auth_data = response.json()
        
        if auth_data.get('ok'):
            user_id = auth_data['authed_user']['id']
            
            user_info_response = requests.get(
                'https://slack.com/api/users.info',
                headers={'Authorization': f'Bearer {auth_data["access_token"]}'},
                params={'user': user_id}
            )
            
            user_info = user_info_response.json()
            
            if user_info.get('ok'):
                session['user_id'] = user_info['user']['id']
                session['user_name'] = user_info['user']['real_name']
                session['access_token'] = auth_data['access_token']
                
                flash(f'Welcome, {session["user_name"]}!', 'success')
                return redirect(url_for('index'))
        
        flash('Authentication failed. Please try again.', 'error')
        return redirect(url_for('login'))
        
    except Exception as e:
        logger.error(f"Auth callback error: {str(e)}")
        flash('Authentication error. Please try again.', 'error')
        return redirect(url_for('login'))

@app.route('/logout')
@login_required
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/create-log', methods=['GET', 'POST'])
@login_required
def create_log():
    if request.method == 'POST':
        try:
            project_tag = request.form.get('project_tag')
            project_name = request.form.get('project_name')
            description = request.form.get('description')
            what_did = request.form.get('what_did')
            could_improve = request.form.get('could_improve')
            can_improve = request.form.get('can_improve')
            next_steps = request.form.get('next_steps')
            time_spent = int(request.form.get('time_spent', 0))
            
            media_url = ''
            if 'media_file' in request.files:
                file = request.files['media_file']
                if file and file.filename and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    timestamp = str(int(time.time()))
                    filename = f"{timestamp}_{filename}"
                    
                    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1]) as temp_file:
                        file.save(temp_file.name)
                        temp_file_path = temp_file.name
                    
                    try:
                        media_url = upload_file_to_cdn_alternative(temp_file_path)
                        
                        if not media_url:
                            media_url = upload_to_hackclub_cdn(temp_file_path)
                        
                        if not media_url:
                            flash('Failed to upload media file. Continuing without media.', 'warning')
                            
                    finally:
                        if os.path.exists(temp_file_path):
                            os.remove(temp_file_path)
            
            log_data = {
                'user_id': session['user_id'],
                'user_name': session['user_name'],
                'project_name': project_name,
                'project_tag': project_tag,
                'description': description,
                'what_did': what_did,
                'could_improve': could_improve,
                'can_improve': can_improve,
                'next_steps': next_steps,
                'time_spent': time_spent,
                'media_url': media_url,
                'created_at': datetime.now().isoformat()
            }
            
            # Save to Airtable :sob
            airtable_result = save_to_airtable(log_data)
            
            if airtable_result:
                flash('Dev log created successfully!', 'success')
                return redirect(url_for('index'))
            else:
                flash('Error saving dev log. Please try again.', 'error')
                
        except Exception as e:
            logger.error(f"Error creating log: {str(e)}")
            flash('Error creating dev log. Please try again.', 'error')
    
    return render_template('create_log.html')

if __name__ == '__main__':
    
    print("\n" + "=" * 50)
    print("HTTPS CONFIGURATION")
    print("=" * 50)
    
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        
        # Check if certificates exist trust me this is needed yu will die if this is not here :skull
        cert_file = 'cert.pem'
        key_file = 'privkey.pem'
        
        if os.path.exists(cert_file) and os.path.exists(key_file):
            context.load_cert_chain(cert_file, key_file)
            print("✓ SSL certificates found")
            print(f"🚀 Starting HTTPS server at https://localhost:5000")
            app.run(debug=True, port=5000, ssl_context=context)
        else:
            print("⚠️  SSL certificates not found!")
            print("To generate self-signed certificates, run:")
            print("openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes")
            print("\nAlternatively, running on HTTP for now...")
            print(f"🚀 Starting HTTP server at http://localhost:5000")
            app.run(debug=True, port=5000)
            
    except Exception as e:
        print(f"❌ SSL setup failed: {e}")
        print("🚀 Starting HTTP server at http://localhost:5000")
        app.run(debug=True, port=5000)