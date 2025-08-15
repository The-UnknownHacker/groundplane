from flask import jsonify, request, session, redirect, url_for, flash
from functools import wraps
import requests
import os
import logging

AIRTABLE_BASE_ID = os.environ.get('AIRTABLE_BASE_ID')
AIRTABLE_TABLE_NAME = os.environ.get('AIRTABLE_TABLE_NAME')
AIRTABLE_PROJECTS_TABLE = os.environ.get('AIRTABLE_PROJECTS_TABLE')
AIRTABLE_API_KEY = os.environ.get('AIRTABLE_API_KEY')

logger = logging.getLogger(__name__)

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