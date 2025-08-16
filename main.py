from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, send_file, after_this_request
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
import json

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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
AIRTABLE_USER_SETTINGS_TABLE = os.environ.get('AIRTABLE_USER_SETTINGS_TABLE', 'UserSettings')
AIRTABLE_USERS_TABLE = os.environ.get('AIRTABLE_USERS_TABLE', 'Users')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mov', 'avi', 'webm'}

TEMP_DIR = tempfile.gettempdir()
temp_files = {} 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        
        if not session.get('is_admin', False) and not is_admin(session['user_id']):
            flash('You do not have permission to access this page.', 'error')
            return redirect(url_for('index'))
            
        if 'is_admin' not in session:
            session['is_admin'] = True
            
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
                'Project Tag': log_data.get('project_tag', ''),
                'Title': log_data.get('title', ''),
                'What I Did': log_data.get('what_did', ''),
                'Next Steps': log_data.get('next_steps', ''),
                'Time Spent (minutes)': log_data['time_spent'],
                'Media URL': log_data.get('media_url', ''),
                'Created At': log_data['created_at'],
                'Issues Faced': log_data.get('issues_faced', ''),
                'Status': log_data.get('status', 'Pending')  
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
        user_settings = get_user_settings(session['user_id'])
        use_static_props = user_settings.get('use_static_props', False)
        
        if use_static_props and 'logs_cache' in session:
            logger.info("Using cached logs data")
            return jsonify(session['logs_cache'])
        
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
            records = data['records']
            
            if use_static_props:
                session['logs_cache'] = records
                logger.info("Cached logs data")
            
            return jsonify(records)
        else:
            logger.error(f"Airtable fetch failed: {response.text}")
            return jsonify([])
            
    except Exception as e:
        logger.error(f"Error fetching logs: {str(e)}")
        return jsonify([])

@app.route('/api/logs/<record_id>', methods=['DELETE'])
@login_required
def delete_log(record_id):
    """Delete a dev log entry from Airtable"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}/{record_id}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch log for deletion verification: {response.text}")
            return jsonify({"success": False, "message": "Log not found"}), 404
            
        log_data = response.json()
        if log_data.get('fields', {}).get('User ID') != session['user_id']:
            return jsonify({"success": False, "message": "Unauthorized"}), 403
        
        delete_response = requests.delete(url, headers=headers)
        
        if delete_response.status_code == 200:
            if 'logs_cache' in session:
                session.pop('logs_cache')
                logger.info("Cleared logs cache after deleting log")
            return jsonify({"success": True, "message": "Log deleted successfully"})
        else:
            logger.error(f"Airtable delete failed: {delete_response.text}")
            return jsonify({"success": False, "message": "Failed to delete log"}), 500
            
    except Exception as e:
        logger.error(f"Error deleting log: {str(e)}")
        return jsonify({"success": False, "message": "An error occurred"}), 500

@app.route('/api/logs/<record_id>', methods=['GET'])
@login_required
def get_log(record_id):
    """Get a specific dev log entry from Airtable"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}/{record_id}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            log_data = response.json()
            if log_data.get('fields', {}).get('User ID') != session['user_id']:
                return jsonify({"success": False, "message": "Unauthorized"}), 403
                
            return jsonify(log_data)
        else:
            logger.error(f"Airtable fetch failed: {response.text}")
            return jsonify({"success": False, "message": "Log not found"}), 404
            
    except Exception as e:
        logger.error(f"Error fetching log: {str(e)}")
        return jsonify({"success": False, "message": "An error occurred"}), 500

@app.route('/api/logs/<record_id>', methods=['PATCH'])
@login_required
def update_log(record_id):
    """Update a dev log entry in Airtable"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}/{record_id}"
        headers = {
            'Authorization': f'Bearer {AIRTABLE_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch log for update verification: {response.text}")
            return jsonify({"success": False, "message": "Log not found"}), 404
            
        log_data = response.json()
        if log_data.get('fields', {}).get('User ID') != session['user_id']:
            return jsonify({"success": False, "message": "Unauthorized"}), 403
        
        update_data = request.json
        
        fields = {
            'Project Name': update_data.get('project_name'),
            'Title': update_data.get('title'),
            'What I Did': update_data.get('what_did'),
            'Issues Faced': update_data.get('issues_faced'),
            'Next Steps': update_data.get('next_steps'),
            'Time Spent (minutes)': update_data.get('time_spent'),
            'Status': update_data.get('status')  
        }
        
        fields = {k: v for k, v in fields.items() if v is not None}
        
        update_payload = {
            'fields': fields
        }
        
        update_response = requests.patch(url, headers=headers, json=update_payload)
        
        if update_response.status_code == 200:
            if 'logs_cache' in session:
                session.pop('logs_cache')
                logger.info("Cleared logs cache after updating log")
            return jsonify({"success": True, "message": "Log updated successfully", "data": update_response.json()})
        else:
            logger.error(f"Airtable update failed: {update_response.text}")
            return jsonify({"success": False, "message": "Failed to update log"}), 500
            
    except Exception as e:
        logger.error(f"Error updating log: {str(e)}")
        return jsonify({"success": False, "message": "An error occurred"}), 500

import re

AIRTABLE_PROJECTS_TABLE = os.environ.get('AIRTABLE_PROJECTS_TABLE', 'Projects')

def save_project_to_airtable(project_data):
    """Save project to Airtable using Personal Access Token"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROJECTS_TABLE}"
        headers = {
            'Authorization': f'Bearer {AIRTABLE_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        cover_image_url = project_data.get('cover_image_url')
        if cover_image_url and cover_image_url.startswith('/'):
            cover_image_url = request.url_root.rstrip('/') + cover_image_url
        
        data = {
            'fields': {
                'User ID': project_data['user_id'],
                'User Name': project_data['user_name'],
                'Project Name': project_data['project_name'],
                'Description': project_data['description'],
                'Github Link': project_data['github_link'],
                'Cover Image URL': cover_image_url,
                'Created At': project_data['created_at']
            }
        }
        
        response = requests.post(url, headers=headers, json=data)
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Airtable project save failed: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error saving project to Airtable: {str(e)}")
        return None

@app.route('/api/projects')
@login_required
def get_projects():
    """Get user's projects from Airtable"""
    try:
        user_settings = get_user_settings(session['user_id'])
        use_static_props = user_settings.get('use_static_props', False)
        
        if use_static_props and 'projects_cache' in session:
            logger.info("Using cached projects data")
            return jsonify(session['projects_cache'])
        
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROJECTS_TABLE}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        params = {
            'filterByFormula': f"{{User ID}} = '{session['user_id']}'",
            'sort[0][field]': 'Created At',
            'sort[0][direction]': 'desc'
        }
        
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            data = response.json()
            records = data['records']
            
            if use_static_props:
                session['projects_cache'] = records
                logger.info("Cached projects data")
            
            return jsonify(records)
        else:
            logger.error(f"Airtable fetch failed: {response.text}")
            return jsonify([])
            
    except Exception as e:
        logger.error(f"Error fetching projects: {str(e)}")
        return jsonify([])

@app.route('/api/projects', methods=['POST'])
@login_required
def create_project():
    """Create a new project"""
    try:
        data = request.json
        
        client_timestamp = data.get('client_timestamp')
        created_at = client_timestamp if client_timestamp else datetime.now().isoformat()
        
        project_data = {
            'user_id': session['user_id'],
            'user_name': session['user_name'],
            'project_name': data.get('project_name'),
            'description': data.get('description'),
            'github_link': data.get('github_link'),
            'created_at': created_at
        }
        
        result = save_project_to_airtable(project_data)
        
        if result:
            if 'projects_cache' in session:
                session.pop('projects_cache')
                logger.info("Cleared projects cache after creating new project")
            return jsonify({"success": True, "message": "Project created successfully", "data": result})
        else:
            return jsonify({"success": False, "message": "Failed to create project"}), 500
            
    except Exception as e:
        logger.error(f"Error creating project: {str(e)}")
        return jsonify({"success": False, "message": "An error occurred"}), 500

@app.route('/api/projects/<record_id>', methods=['GET'])
@login_required
def get_project(record_id):
    """Get a specific project from Airtable"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROJECTS_TABLE}/{record_id}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            project_data = response.json()
            if project_data.get('fields', {}).get('User ID') != session['user_id']:
                return jsonify({"success": False, "message": "Unauthorized"}), 403
                
            return jsonify(project_data)
        else:
            logger.error(f"Airtable fetch failed: {response.text}")
            return jsonify({"success": False, "message": "Project not found"}), 404
            
    except Exception as e:
        logger.error(f"Error fetching project: {str(e)}")
        return jsonify({"success": False, "message": "An error occurred"}), 500

@app.route('/api/projects/<record_id>', methods=['PATCH'])
@login_required
def update_project(record_id):
    """Update a project in Airtable"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROJECTS_TABLE}/{record_id}"
        headers = {
            'Authorization': f'Bearer {AIRTABLE_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch project for update verification: {response.text}")
            return jsonify({"success": False, "message": "Project not found"}), 404
            
        project_data = response.json()
        if project_data.get('fields', {}).get('User ID') != session['user_id']:
            return jsonify({"success": False, "message": "Unauthorized"}), 403
        
        update_data = request.json
        
        fields = {
            'Project Name': update_data.get('project_name'),
            'Description': update_data.get('description'),
            'Github Link': update_data.get('github_link'),
            'Cover Image URL': update_data.get('cover_image_url')
        }
        
        fields = {k: v for k, v in fields.items() if v is not None}
        
        update_payload = {
            'fields': fields
        }
        
        update_response = requests.patch(url, headers=headers, json=update_payload)
        
        if update_response.status_code == 200:
            if 'projects_cache' in session:
                session.pop('projects_cache')
                logger.info("Cleared projects cache after updating project")
            return jsonify({"success": True, "message": "Project updated successfully", "data": update_response.json()})
        else:
            logger.error(f"Airtable update failed: {update_response.text}")
            return jsonify({"success": False, "message": "Failed to update project"}), 500
            
    except Exception as e:
        logger.error(f"Error updating project: {str(e)}")
        return jsonify({"success": False, "message": "An error occurred"}), 500

@app.route('/api/projects/<record_id>', methods=['DELETE'])
@login_required
def delete_project(record_id):
    """Delete a project from Airtable"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROJECTS_TABLE}/{record_id}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch project for deletion verification: {response.text}")
            return jsonify({"success": False, "message": "Project not found"}), 404
            
        project_data = response.json()
        if project_data.get('fields', {}).get('User ID') != session['user_id']:
            return jsonify({"success": False, "message": "Unauthorized"}), 403
        
        delete_response = requests.delete(url, headers=headers)
        
        if delete_response.status_code == 200:
            if 'projects_cache' in session:
                session.pop('projects_cache')
                logger.info("Cleared projects cache after deleting project")
            return jsonify({"success": True, "message": "Project deleted successfully"})
        else:
            logger.error(f"Airtable delete failed: {delete_response.text}")
            return jsonify({"success": False, "message": "Failed to delete project"}), 500
            
    except Exception as e:
        logger.error(f"Error deleting project: {str(e)}")
        return jsonify({"success": False, "message": "An error occurred"}), 500

@app.route('/create-project', methods=['GET', 'POST'])
@login_required
def create_project_page():
    """Render the create project page"""
    if request.method == 'POST':
        try:
            project_name = request.form.get('project_name')
            description = request.form.get('description')
            github_link = request.form.get('github_link')
            client_timestamp = request.form.get('client_timestamp')
            
            cover_image_url = request.url_root.rstrip('/') + '/default_cover.png'
            
            if 'cover_image' in request.files:
                file = request.files['cover_image']
                if file and file.filename and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    timestamp = str(int(time.time()))
                    filename = f"{timestamp}_{filename}"
                    logger.info(f"Cover image URL being saved to Airtable: {cover_image_url}")

                    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1]) as temp_file:
                        file.save(temp_file.name)
                        temp_file_path = temp_file.name
                    
                    try:
                        media_url = upload_file_to_cdn_alternative(temp_file_path)
                        
                        if not media_url:
                            media_url = upload_to_hackclub_cdn(temp_file_path)
                        
                        if media_url:
                            cover_image_url = media_url
                        else:
                            flash('Failed to upload cover image. Using default image.', 'warning')
                            
                    finally:
                        if os.path.exists(temp_file_path):
                            os.remove(temp_file_path)
            
            created_at = client_timestamp if client_timestamp else datetime.now().isoformat()
            
            project_data = {
                'user_id': session['user_id'],
                'user_name': session['user_name'],
                'project_name': project_name,
                'description': description,
                'github_link': github_link,
                'cover_image_url': cover_image_url,
                'created_at': created_at
            }
            
            airtable_result = save_project_to_airtable(project_data)
            
            if airtable_result:
                if 'projects_cache' in session:
                    session.pop('projects_cache')
                    logger.info("Cleared projects cache after creating new project via form")
                flash('Project created successfully!', 'success')
                return redirect(url_for('index'))
            else:
                flash('Error saving project. Please try again.', 'error')
                
        except Exception as e:
            logger.error(f"Error creating project: {str(e)}")
            flash('Error creating project. Please try again.', 'error')
    
    return render_template('create_project.html')

@app.route('/edit-project')
@login_required
def edit_project_page():
    """Render the edit project page"""
    record_id = request.args.get('id')
    if not record_id:
        flash('No project ID provided', 'error')
        return redirect(url_for('index'))
        
    return render_template('edit_project.html')

@app.route('/project/<project_id>')
@login_required
def project_detail(project_id):
    """Render the project detail page"""
    return render_template('project_detail.html', project_id=project_id)


def get_user_settings(user_id):
    """Get user settings from session or create default settings"""
    if 'user_settings' in session:
        return session['user_settings']
    
    default_settings = {
        'record_id': None,
        'enable_animations': True,
        'reduced_motion': False,
        'project_reminders': True,
        'use_static_props': False,
        'last_refreshed': None
    }
    
    session['user_settings'] = default_settings
    logger.info("Created default user settings in local cache")
    return default_settings

def save_user_settings(user_id, settings):
    """Save user settings to session only (local cache)"""
    session_settings = {
        'record_id': None,
        'enable_animations': settings.get('enable_animations', True),
        'reduced_motion': settings.get('reduced_motion', False),
        'project_reminders': settings.get('project_reminders', True),
        'use_static_props': settings.get('use_static_props', False),
        'last_refreshed': settings.get('last_refreshed', None)
    }
    
    session['user_settings'] = session_settings
    logger.info("Saved user settings to local cache")
    return True

def get_status_class(status):
    """Return the appropriate CSS class for a status badge"""
    if status == 'Approved':
        return 'bg-success'
    elif status == 'Rejected':
        return 'bg-danger'
    elif status == 'Pending':
        return 'bg-warning'
    elif status == 'In Review':
        return 'bg-info'
    else:
        return 'bg-secondary'

@app.context_processor
def inject_user_settings():
    """Inject user settings into all templates"""
    context = {}
    if 'user_id' in session:
        context['user_settings'] = get_user_settings(session['user_id'])
        context['is_admin'] = session.get('is_admin', False)
    else:
        context['user_settings'] = {'enable_animations': True, 'reduced_motion': False, 'project_reminders': True, 'use_static_props': False, 'last_refreshed': None}
        context['is_admin'] = False
    
    context['get_status_class'] = get_status_class
    return context

def get_user_from_airtable(user_id):
    """Get user from Airtable Users table"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_USERS_TABLE}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        params = {
            'filterByFormula': f"{{User ID}} = '{user_id}'"
        }
        
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('records') and len(data['records']) > 0:
                return data['records'][0]
            else:
                return None
        else:
            logger.error(f"Airtable user fetch failed: {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Error fetching user: {str(e)}")
        return None

def get_all_users():
    """Get all users from Airtable Users table"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_USERS_TABLE}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            return data.get('records', [])
        else:
            logger.error(f"Airtable users fetch failed: {response.text}")
            return []
            
    except Exception as e:
        logger.error(f"Error fetching users: {str(e)}")
        return []

def save_user_to_airtable(user_data):
    """Save user to Airtable Users table"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_USERS_TABLE}"
        headers = {
            'Authorization': f'Bearer {AIRTABLE_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        data = {
            'fields': user_data
        }
        
        response = requests.post(url, headers=headers, json=data)
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Airtable user save failed: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error saving user: {str(e)}")
        return None

def update_user_in_airtable(record_id, user_data):
    """Update user in Airtable Users table"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_USERS_TABLE}/{record_id}"
        headers = {
            'Authorization': f'Bearer {AIRTABLE_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        data = {
            'fields': user_data
        }
        
        response = requests.patch(url, headers=headers, json=data)
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Airtable user update failed: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error updating user: {str(e)}")
        return None

def is_admin(user_id):
    """Check if user is an admin"""
    user = get_user_from_airtable(user_id)
    admin_value = user.get('fields', {}).get('Is Admin', 'false') if user else 'false'
    return admin_value is True or admin_value == True or admin_value == 'true'

@app.route('/admin')
@admin_required
def admin_dashboard():
    """Admin dashboard home page"""
    return render_template('admin/dashboard.html')

@app.route('/admin/users')
@admin_required
def admin_users():
    """Admin users management page"""
    users = get_all_users()
    return render_template('admin/users.html', users=users)

@app.route('/admin/users/<record_id>/projects')
@admin_required
def admin_user_projects(record_id):
    """Admin user projects page"""
    try:
        user_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_USERS_TABLE}/{record_id}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        user_response = requests.get(user_url, headers=headers)
        
        if user_response.status_code != 200:
            logger.error(f"Failed to fetch user: {user_response.text}")
            flash('User not found', 'error')
            return redirect(url_for('admin_users'))
        
        user_data = user_response.json()
        user_name = user_data['fields']['User Name']
        
        projects_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROJECTS_TABLE}"
        
        params = {
            'filterByFormula': f"{{User Name}} = '{user_name}'",
            'sort[0][field]': 'Created At',
            'sort[0][direction]': 'desc'
        }
        
        projects_response = requests.get(projects_url, headers=headers, params=params)
        
        if projects_response.status_code == 200:
            projects_data = projects_response.json()
            projects = projects_data.get('records', [])
            return render_template('admin/user_projects.html', user=user_data, projects=projects)
        else:
            logger.error(f"Airtable projects fetch failed: {projects_response.text}")
            flash('Failed to fetch user projects', 'warning')
            return render_template('admin/user_projects.html', user=user_data, projects=[])
    except Exception as e:
        logger.error(f"Error fetching user projects: {str(e)}")
        flash('An error occurred while fetching user projects', 'error')
        return redirect(url_for('admin_users'))

@app.route('/admin/users/<record_id>/toggle-admin', methods=['POST'])
@admin_required
def admin_toggle_user_admin(record_id):
    """Toggle admin status for a user"""
    try:
        is_admin_value = request.form.get('is_admin')
        
        is_admin_str = 'true' if is_admin_value == 'True' else 'false'
        
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_USERS_TABLE}/{record_id}"
        headers = {
            'Authorization': f'Bearer {AIRTABLE_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        update_payload = {
            'fields': {
                'Is Admin': is_admin_str
            }
        }
        
        response = requests.patch(url, headers=headers, json=update_payload)
        
        if response.status_code == 200:
            flash('User admin status updated successfully', 'success')
        else:
            logger.error(f"Airtable user update failed: {response.text}")
            flash('Failed to update user admin status', 'error')
            
    except Exception as e:
        logger.error(f"Error updating user admin status: {str(e)}")
        flash('An error occurred while updating user admin status', 'error')
        
    return redirect(url_for('admin_users'))

@app.route('/admin/projects')
@admin_required
def admin_projects():
    """Admin projects management page"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROJECTS_TABLE}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            projects = data.get('records', [])
            return render_template('admin/projects.html', projects=projects)
        else:
            logger.error(f"Airtable projects fetch failed: {response.text}")
            flash('Failed to fetch projects', 'error')
            return render_template('admin/projects.html', projects=[])
            
    except Exception as e:
        logger.error(f"Error fetching projects: {str(e)}")
        flash('An error occurred while fetching projects', 'error')
        return render_template('admin/projects.html', projects=[])

@app.route('/admin/projects/<record_id>')
@admin_required
def admin_project_detail(record_id):
    """Admin project detail page"""
    try:
        project_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROJECTS_TABLE}/{record_id}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        project_response = requests.get(project_url, headers=headers)
        
        if project_response.status_code != 200:
            logger.error(f"Failed to fetch project: {project_response.text}")
            flash('Project not found', 'error')
            return redirect(url_for('admin_projects'))
        
        project_data = project_response.json()
        project_name = project_data['fields']['Project Name']
        
        logs_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
        
        params = {
            'filterByFormula': f"{{Project Name}} = '{project_name}'",
            'sort[0][field]': 'Created At',
            'sort[0][direction]': 'desc'
        }
        
        logs_response = requests.get(logs_url, headers=headers, params=params)
        
        if logs_response.status_code == 200:
            logs_data = logs_response.json()
            logs = logs_data.get('records', [])
            return render_template('admin/project_detail.html', project=project_data, logs=logs)
        else:
            logger.error(f"Airtable logs fetch failed: {logs_response.text}")
            flash('Failed to fetch project logs', 'warning')
            return render_template('admin/project_detail.html', project=project_data, logs=[])
    except Exception as e:
        logger.error(f"Error fetching project details: {str(e)}")
        flash('An error occurred while fetching project details', 'error')
        return redirect(url_for('admin_projects'))

@app.route('/api/admin/projects/<project_id>/log-count', methods=['GET'])
@admin_required
def api_admin_project_log_count(project_id):
    """API endpoint to get the count of logs for a specific project."""
    try:
        logs_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        project_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROJECTS_TABLE}/{project_id}"
        project_response = requests.get(project_url, headers=headers)
        
        if project_response.status_code != 200:
            logger.error(f"Failed to fetch project: {project_response.text}")
            return jsonify({'count': 0})
        
        project_data = project_response.json()
        project_name = project_data['fields']['Project Name']
        
        params = {
            'filterByFormula': f"{{Project Name}} = '{project_name}'"
        }
        
        logs_response = requests.get(logs_url, headers=headers, params=params)
        
        if logs_response.status_code == 200:
            logs_data = logs_response.json()
            logs = logs_data.get('records', [])
            return jsonify({'count': len(logs)})
        else:
            logger.error(f"Airtable logs fetch failed: {logs_response.text}")
            return jsonify({'count': 0})
            
    except Exception as e:
        logger.error(f"Error fetching log count: {str(e)}")
        return jsonify({'count': 0})

@app.route('/api/admin/recent-logs', methods=['GET'])
@admin_required
def api_admin_recent_logs():
    """API endpoint to get recent logs for the admin dashboard."""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        params = {
            'sort[0][field]': 'Created At',
            'sort[0][direction]': 'desc',
            'maxRecords': 10
        }
        
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            data = response.json()
            return jsonify(data.get('records', []))
        else:
            logger.error(f"Airtable fetch failed: {response.text}")
            return jsonify([])
            
    except Exception as e:
        logger.error(f"Error fetching recent logs: {str(e)}")
        return jsonify([])

@app.route('/admin/logs/<record_id>', methods=['GET'])
@admin_required
def admin_log_detail(record_id):
    """Admin log detail page"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}/{record_id}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            log_data = response.json()
            
            project_name = log_data['fields'].get('Project Name', 'Unknown Project')
            
            project_id = None
            if 'Project' in log_data['fields'] and isinstance(log_data['fields']['Project'], list) and log_data['fields']['Project']:
                project_id = log_data['fields']['Project'][0]
            
            what_did = log_data['fields'].get('What I Did', '')
            issues_faced = log_data['fields'].get('Issues Faced', '')
            next_steps = log_data['fields'].get('Next Steps', '')
            
            content = f"<h4>What I Did</h4><p>{what_did}</p>"
            
            if issues_faced:
                content += f"<h4>Issues Faced</h4><p>{issues_faced}</p>"
                
            if next_steps:
                content += f"<h4>Next Steps</h4><p>{next_steps}</p>"
            
            log_data['fields']['Content'] = content
            
            if 'Media URL' in log_data['fields'] and log_data['fields']['Media URL'] and 'Media' not in log_data['fields']:
                media_url = log_data['fields']['Media URL']
                log_data['fields']['Media'] = [{'url': media_url, 'filename': 'media.jpg'}]
                logger.info(f"Added Media field from Media URL: {media_url}")
            
            logger.info(f"Log data fields: {log_data['fields'].keys()}")
            if 'Media' in log_data['fields']:
                logger.info(f"Media field: {log_data['fields']['Media']}")
            if 'Media URL' in log_data['fields']:
                logger.info(f"Media URL field: {log_data['fields']['Media URL']}")
                
            logger.info(f"Complete log data: {log_data}")
            
            return render_template('admin/log_detail.html', log=log_data, project_name=project_name, project_id=project_id)
        else:
            logger.error(f"Airtable fetch failed: {response.text}")
            flash('Log not found', 'error')
            return redirect(url_for('admin_dashboard'))
            
    except Exception as e:
        logger.error(f"Error fetching log: {str(e)}")
        flash('An error occurred while fetching log details', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/logs/<record_id>/update', methods=['POST'])
@admin_required
def admin_update_log(record_id):
    """Update log status as admin"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}/{record_id}"
        headers = {
            'Authorization': f'Bearer {AIRTABLE_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        status = request.form.get('status')
        
        if not status:
            flash('Status is required', 'error')
            return redirect(url_for('admin_log_detail', record_id=record_id))
        
        update_payload = {
            'fields': {
                'Status': status
            }
        }
        
        update_response = requests.patch(url, headers=headers, json=update_payload)
        
        if update_response.status_code == 200:
            flash('Log updated successfully', 'success')
            return redirect(url_for('admin_log_detail', record_id=record_id))
        else:
            logger.error(f"Airtable update failed: {update_response.text}")
            flash('Failed to update log', 'error')
            return redirect(url_for('admin_log_detail', record_id=record_id))
            
    except Exception as e:
        logger.error(f"Error updating log: {str(e)}")
        flash('An error occurred while updating log', 'error')
        return redirect(url_for('admin_log_detail', record_id=record_id))

@app.route('/admin/logs/<record_id>/update-time', methods=['POST'])
@admin_required
def admin_update_log_time(record_id):
    """Update log time spent as admin"""
    try:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}/{record_id}"
        headers = {
            'Authorization': f'Bearer {AIRTABLE_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        time_spent = request.form.get('time_spent')
        
        if not time_spent:
            flash('Time spent is required', 'error')
            return redirect(url_for('admin_log_detail', record_id=record_id))
        
        try:
            time_spent = int(time_spent)
            if time_spent < 0:
                raise ValueError("Time spent cannot be negative")
        except ValueError:
            flash('Time spent must be a positive number', 'error')
            return redirect(url_for('admin_log_detail', record_id=record_id))
        
        update_payload = {
            'fields': {
                'Time Spent (minutes)': time_spent
            }
        }
        
        update_response = requests.patch(url, headers=headers, json=update_payload)
        
        if update_response.status_code == 200:
            flash('Log time updated successfully', 'success')
            return redirect(url_for('admin_log_detail', record_id=record_id))
        else:
            logger.error(f"Airtable update failed: {update_response.text}")
            flash('Failed to update log time', 'error')
            return redirect(url_for('admin_log_detail', record_id=record_id))
            
    except Exception as e:
        logger.error(f"Error updating log time: {str(e)}")
        flash('An error occurred while updating log time', 'error')
        return redirect(url_for('admin_log_detail', record_id=record_id))

@app.route('/')
def index():
    if 'user_id' not in session:
        return render_template('landing.html')
    return render_template('dashboard.html', user=session)

@app.route('/settings')
@login_required
def settings_page():
    """Render the settings page"""
    user_settings = get_user_settings(session['user_id'])
    return render_template('settings.html', user=session, user_settings=user_settings)

@app.route('/settings/save', methods=['POST'])
@login_required
def save_settings():
    """Save user settings"""
    enable_animations = 'enable_animations' in request.form
    reduced_motion = 'reduced_motion' in request.form
    project_reminders = 'project_reminders' in request.form
    
    use_static_props_value = request.form.get('use_static_props', 'off')
    use_static_props = (use_static_props_value == 'on')
    
    logger.info(f"Form data: {dict(request.form)}")
    logger.info(f"use_static_props value: {use_static_props_value}")
    logger.info(f"use_static_props boolean: {use_static_props}")
    
    current_settings = get_user_settings(session['user_id'])
    last_refreshed = current_settings.get('last_refreshed')
    
    settings = {
        'enable_animations': enable_animations,
        'reduced_motion': reduced_motion,
        'project_reminders': project_reminders,
        'use_static_props': use_static_props,
        'last_refreshed': last_refreshed
    }
    
    logger.info(f"Settings to save: {settings}")
    
    success = save_user_settings(session['user_id'], settings)
    
    if success:
        flash('Settings saved successfully!', 'success')
    else:
        flash('Failed to save settings. Please try again.', 'error')
    
    return redirect(url_for('settings_page'))

@app.route('/settings/refresh-data', methods=['POST'])
@login_required
def refresh_data():
    """Refresh data from Airtable and update last_refreshed timestamp"""
    try:
        if 'projects_cache' in session:
            session.pop('projects_cache')
        if 'logs_cache' in session:
            session.pop('logs_cache')
        
        current_settings = get_user_settings(session['user_id'])
        current_settings['last_refreshed'] = datetime.now().isoformat()
        
        success = save_user_settings(session['user_id'], current_settings)
        
        if success:
            return jsonify({"success": True, "message": "Data refreshed successfully", "timestamp": current_settings['last_refreshed']})
        else:
            return jsonify({"success": False, "message": "Failed to update refresh timestamp"}), 500
    except Exception as e:
        logger.error(f"Error refreshing data: {str(e)}")
        return jsonify({"success": False, "message": "An error occurred while refreshing data"}), 500

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
                slack_user = user_info['user']
                session['user_id'] = slack_user['id']
                session['user_name'] = slack_user['real_name']
                session['access_token'] = auth_data['access_token']
                
                existing_user = get_user_from_airtable(slack_user['id'])
                
                if not existing_user:
                    user_data = {
                        'User ID': slack_user['id'],
                        'User Name': slack_user['real_name'],
                        'Email': slack_user.get('profile', {}).get('email', ''),
                        'Avatar URL': slack_user.get('profile', {}).get('image_192', ''),
                        'Slack Team ID': slack_user.get('team_id', ''),
                        'Is Admin': 'false',  # Default to non-admin (string value for Airtable)
                        'Created At': datetime.now().isoformat()
                    }
                    save_user_to_airtable(user_data)
                    logger.info(f"Created new user: {slack_user['id']}")
                else:
                    record_id = existing_user['id']
                    user_data = {
                        'User Name': slack_user['real_name'],
                        'Email': slack_user.get('profile', {}).get('email', ''),
                        'Avatar URL': slack_user.get('profile', {}).get('image_192', ''),
                        'Last Login': datetime.now().isoformat()
                    }
                    update_user_in_airtable(record_id, user_data)
                    logger.info(f"Updated existing user: {slack_user['id']}")
                    
                    admin_value = existing_user.get('fields', {}).get('Is Admin', 'false')
                    session['is_admin'] = admin_value == True or admin_value == 'true'
                
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
    project_name = request.args.get('project', '')
    project_tag = request.args.get('project_tag', '')
    
    if request.method == 'POST':
        try:
            project_tag = request.form.get('project_tag')
            project_name = request.form.get('project_name')
            title = request.form.get('title')
            what_did = request.form.get('what_did')
            issues_faced = request.form.get('issues_faced')
            next_steps = request.form.get('next_steps')
            time_spent = int(request.form.get('time_spent', 0))
            client_timestamp = request.form.get('client_timestamp')
            
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
            
            created_at = client_timestamp if client_timestamp else datetime.now().isoformat()
            
            log_data = {
                'user_id': session['user_id'],
                'user_name': session['user_name'],
                'project_name': project_name,
                'project_tag': project_tag,
                'title': title,
                'what_did': what_did,
                'issues_faced': issues_faced,
                'next_steps': next_steps,
                'time_spent': time_spent,
                'media_url': media_url,
                'created_at': created_at,
                'status': 'Pending'  # Set default status to Pending cause project pending yk
            }
            
            # Save to Airtable :sob
            airtable_result = save_to_airtable(log_data)
            
            if airtable_result:
                if 'logs_cache' in session:
                    session.pop('logs_cache')
                    logger.info("Cleared logs cache after creating log")
                flash('Dev log created successfully!', 'success')
                return redirect(url_for('index'))
            else:
                flash('Error saving dev log. Please try again.', 'error')
                
        except Exception as e:
            logger.error(f"Error creating log: {str(e)}")
            flash('Error creating dev log. Please try again.', 'error')
    
    return render_template('create_log.html', project_name=project_name, project_tag=project_tag)

@app.route('/edit-log')
@login_required
def edit_log():
    """Render the edit log page"""
    record_id = request.args.get('id')
    if not record_id:
        flash('No log ID provided', 'error')
        return redirect(url_for('index'))
        
    return render_template('edit_log.html')

@app.route('/api/projects/<project_id>/logs')
@login_required
def get_project_logs(project_id):
    try:
        logger.info(f"Fetching logs for project ID: {project_id}")
        
        project_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}/{project_id}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        project_response = requests.get(project_url, headers=headers)
        
        if project_response.status_code != 200:
            logger.error(f"Failed to fetch project: {project_response.text}")
            return jsonify([])
        
        project_data = project_response.json()
        project_name = project_data['fields']['Project Name']
        logger.info(f"Project name: {project_name}")
        
        logs_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
        
        params = {
            'filterByFormula': f"AND({{User ID}} = '{session['user_id']}', {{Project Name}} = '{project_name}', {{What I Did}} != '')",
            'sort[0][field]': 'Created At',
            'sort[0][direction]': 'desc'
        }
        
        logger.info(f"Filter formula: {params['filterByFormula']}")
        
        logs_response = requests.get(logs_url, headers=headers, params=params)
        
        if logs_response.status_code == 200:
            data = logs_response.json()
            logger.info(f"Found {len(data['records'])} logs for project {project_name}")
            
            return jsonify(data['records'])
        else:
            logger.error(f"Airtable logs fetch failed: {logs_response.text}")
            return jsonify([])
            
    except Exception as e:
        logger.error(f"Error fetching project logs: {str(e)}")
        return jsonify([])

@app.route('/default_cover.png')
def serve_default_cover():
    """Serve the default cover image"""
    return send_file('default_cover.png')


@app.route('/api/projects/<record_id>/export-markdown')
@login_required
def export_project_markdown(record_id):
    """Export project details and logs as a markdown file"""
    try:
        project_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROJECTS_TABLE}/{record_id}"
        headers = {'Authorization': f'Bearer {AIRTABLE_API_KEY}'}
        
        project_response = requests.get(project_url, headers=headers)
        
        if project_response.status_code != 200:
            logger.error(f"Failed to fetch project for markdown export: {project_response.text}")
            return jsonify({"success": False, "message": "Project not found"}), 404
            
        project_data = project_response.json()
        if project_data.get('fields', {}).get('User ID') != session['user_id']:
            return jsonify({"success": False, "message": "Unauthorized"}), 403
        
        project_fields = project_data['fields']
        project_name = project_fields.get('Project Name', 'Unnamed Project')
        project_description = project_fields.get('Description', '')
        github_link = project_fields.get('Github Link', '')
        cover_image_url = project_fields.get('Cover Image URL', '')
        created_at = project_fields.get('Created At', '')
        
        project_date = ''
        try:
            if created_at:
                date_obj = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                project_date = date_obj.strftime('%B %d, %Y')
        except Exception as e:
            logger.error(f"Error formatting project date: {str(e)}")
        
        logs_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
        
        params = {
            'filterByFormula': f"AND({{User ID}} = '{session['user_id']}', {{Project Name}} = '{project_name}')",
            'sort[0][field]': 'Created At',
            'sort[0][direction]': 'asc'
        }
        
        logs_response = requests.get(logs_url, headers=headers, params=params)
        
        if logs_response.status_code != 200:
            logger.error(f"Failed to fetch logs for markdown export: {logs_response.text}")
            logs = []
        else:
            logs_data = logs_response.json()
            logs = logs_data.get('records', [])
        
        total_logs = len(logs)
        total_time_spent = sum(int(log.get('fields', {}).get('Time Spent (minutes)', 0)) for log in logs)
        hours_spent = total_time_spent // 60
        minutes_spent = total_time_spent % 60
        
        start_date = ''
        end_date = ''
        if logs:
            try:
                first_log = logs[0]
                first_log_created = first_log.get('fields', {}).get('Created At', '')
                if first_log_created:
                    date_obj = datetime.fromisoformat(first_log_created.replace('Z', '+00:00'))
                    start_date = date_obj.strftime('%b %d, %Y')
                
                last_log = logs[-1]
                last_log_created = last_log.get('fields', {}).get('Created At', '')
                if last_log_created:
                    date_obj = datetime.fromisoformat(last_log_created.replace('Z', '+00:00'))
                    end_date = date_obj.strftime('%b %d, %Y')
            except Exception as e:
                logger.error(f"Error determining date range: {str(e)}")
        
        date_range = ''
        if start_date and end_date:
            if start_date == end_date:
                date_range = f" ({start_date})"
            else:
                date_range = f" ({start_date} - {end_date})"
        
        markdown = f"# {project_name}\n\n"
        
        markdown += "## Project Overview\n\n"
        
        if project_date:
            markdown += f"**Started:** {project_date}  \n"
            
        if end_date:
            markdown += f"**Finished:** {end_date}  \n"
            
        if total_logs > 0:
            markdown += f"**Total Logs:** {total_logs}  \n"
            markdown += f"**Time Invested:** {hours_spent} hours {minutes_spent} minutes  \n"
        
        if github_link:
            markdown += f"**GitHub:** [{github_link}]({github_link})  \n"
            
        markdown += "\n"
        
        if project_description:
            markdown += f"### Description\n\n{project_description}\n\n"
        
        if cover_image_url:
            markdown += f"![Project Cover]({cover_image_url})\n\n"
        
        if total_logs > 0:
            markdown += "## Table of Contents\n\n"
            for i, log in enumerate(logs):
                log_fields = log.get('fields', {})
                title = log_fields.get('Title', 'Untitled Log')
                created_at = log_fields.get('Created At', '')
                
                try:
                    if created_at:
                        date_obj = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        short_date = date_obj.strftime('%Y-%m-%d')
                    else:
                        short_date = 'Unknown'
                except Exception:
                    short_date = 'Unknown'
                    
                markdown += f"{i+1}. [{title} ({short_date})](#{title.lower().replace(' ', '-')}-{short_date})\n"
            
            markdown += "\n"
        
        markdown += "## Development Logs\n\n"
        
        for log in logs:
            log_fields = log.get('fields', {})
            title = log_fields.get('Title', 'Untitled Log')
            created_at = log_fields.get('Created At', '')
            what_did = log_fields.get('What I Did', '')
            issues_faced = log_fields.get('Issues Faced', '')
            next_steps = log_fields.get('Next Steps', '')
            time_spent = log_fields.get('Time Spent (minutes)', '')
            media_url = log_fields.get('Media URL', '')
            
            try:
                if created_at:
                    date_obj = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    formatted_date = date_obj.strftime('%B %d, %Y')
                    short_date = date_obj.strftime('%Y-%m-%d')
                else:
                    formatted_date = 'Unknown Date'
                    short_date = 'unknown'
            except Exception as e:
                logger.error(f"Error formatting date: {str(e)}")
                formatted_date = 'Unknown Date'
                short_date = 'unknown'
            
            anchor = f"{title.lower().replace(' ', '-')}-{short_date}"
            markdown += f"### {title} - {formatted_date} <a id=\"{anchor}\"></a>\n\n"
            
            if time_spent:
                hours = int(time_spent) // 60
                minutes = int(time_spent) % 60
                time_display = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
                markdown += f"**Time Spent:** {time_display}  "
            
                
            markdown += "\n\n"
                
            if what_did:
                markdown += f"#### What I Did\n\n{what_did}\n\n"
                
            if issues_faced:
                markdown += f"#### Issues Faced\n\n{issues_faced}\n\n"
                
            if next_steps:
                markdown += f"#### Next Steps\n\n{next_steps}\n\n"
                
            if media_url:
                markdown += f"#### Media\n\n![Log Media]({media_url})\n\n"
                
            markdown += "---\n\n"
        
        export_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        markdown += f"\n\n*Exported from Grounded Tracker on {export_time}*\n"
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.md') as temp_file:
            temp_file.write(markdown.encode('utf-8'))
            temp_file_path = temp_file.name
        
        @after_this_request
        def cleanup(response):
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            return response
        
        safe_project_name = re.sub(r'[^\w\-_\. ]', '_', project_name)
        filename = f"{safe_project_name}_devlogs.md"
        
        return send_file(
            temp_file_path,
            as_attachment=True,
            download_name=filename,
            mimetype='text/markdown'
        )
            
    except Exception as e:
        logger.error(f"Error exporting project markdown: {str(e)}")
        return jsonify({"success": False, "message": "An error occurred"}), 500


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Run the Grounded Tracker application')
    parser.add_argument('--port', type=int, default=5000, help='Port to run the server on')
    args = parser.parse_args()
    
    port = args.port
    
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
            print(f"🚀 Starting HTTPS server at https://127.0.0.1:{port}")
            app.run(debug=True, port=port, ssl_context=context)
        else:
            print("⚠️  SSL certificates not found!")
            print("To generate self-signed certificates, run:")
            print("openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes")
            print("\nAlternatively, running on HTTP for now...")
            print(f"🚀 Starting HTTP server at http://localhost:{port}")
            app.run(debug=True, port=port)
            
    except Exception as e:
        print(f"❌ SSL setup failed: {e}")
        print(f"🚀 Starting HTTP server at http://localhost:{port}")
        app.run(debug=True, port=port)
        
        