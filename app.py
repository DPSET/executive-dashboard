from flask import Flask, render_template, jsonify, request, redirect, session, url_for
from flask_cors import CORS
import requests
import os
from datetime import datetime, timedelta
import json

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
CORS(app)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
QBO_CLIENT_ID     = os.environ.get('QBO_CLIENT_ID')
QBO_CLIENT_SECRET = os.environ.get('QBO_CLIENT_SECRET')
QBO_REDIRECT_URI  = os.environ.get('QBO_REDIRECT_URI', 'http://localhost:5000/qbo/callback')
QBO_REALM_ID      = os.environ.get('QBO_REALM_ID')

TEKMETRIC_API_KEY = os.environ.get('TEKMETRIC_API_KEY')
TEKMETRIC_SHOP_ID_DCS  = os.environ.get('TEKMETRIC_SHOP_ID_DCS')
TEKMETRIC_SHOP_ID_EURO = os.environ.get('TEKMETRIC_SHOP_ID_EURO')

# Google Sheets (existing, as fallback until APIs are fully connected)
DCS_CSV_URL     = os.environ.get('DCS_CSV_URL', '')
EURO_CSV_URL    = os.environ.get('EURO_CSV_URL', '')
PAYROLL_CSV_URL = os.environ.get('PAYROLL_CSV_URL', '')

# ─── ROUTES ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/login')
def login():
    return render_template('login.html')

# ─── API ENDPOINTS ────────────────────────────────────────────────────────────

@app.route('/api/status')
def status():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'qbo_configured': bool(QBO_CLIENT_ID),
        'tekmetric_configured': bool(TEKMETRIC_API_KEY),
        'sheets_configured': bool(DCS_CSV_URL)
    })

@app.route('/api/dcs')
def dcs_data():
    """DCS revenue data from Google Sheets"""
    if not DCS_CSV_URL:
        return jsonify({'error': 'DCS CSV URL not configured'}), 500
    try:
        r = requests.get(DCS_CSV_URL, timeout=10)
        r.raise_for_status()
        return jsonify({'csv': r.text, 'source': 'google_sheets'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/euro')
def euro_data():
    """EuroTech revenue data from Google Sheets"""
    if not EURO_CSV_URL:
        return jsonify({'error': 'Euro CSV URL not configured'}), 500
    try:
        r = requests.get(EURO_CSV_URL, timeout=10)
        r.raise_for_status()
        return jsonify({'csv': r.text, 'source': 'google_sheets'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/payroll')
def payroll_data():
    """Payroll data from Google Sheets"""
    if not PAYROLL_CSV_URL:
        return jsonify({'error': 'Payroll CSV URL not configured'}), 500
    try:
        r = requests.get(PAYROLL_CSV_URL, timeout=10)
        r.raise_for_status()
        return jsonify({'csv': r.text, 'source': 'google_sheets'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─── QBO OAUTH ────────────────────────────────────────────────────────────────

@app.route('/qbo/connect')
def qbo_connect():
    """Start QBO OAuth flow"""
    auth_url = (
        f"https://appcenter.intuit.com/connect/oauth2"
        f"?client_id={QBO_CLIENT_ID}"
        f"&redirect_uri={QBO_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=com.intuit.quickbooks.accounting"
        f"&state=cadm_dashboard"
    )
    return redirect(auth_url)

@app.route('/qbo/callback')
def qbo_callback():
    """Handle QBO OAuth callback"""
    code = request.args.get('code')
    realm_id = request.args.get('realmId')
    if not code:
        return jsonify({'error': 'No code received'}), 400
    try:
        token_response = requests.post(
            'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer',
            auth=(QBO_CLIENT_ID, QBO_CLIENT_SECRET),
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': QBO_REDIRECT_URI
            }
        )
        tokens = token_response.json()
        session['qbo_access_token'] = tokens.get('access_token')
        session['qbo_refresh_token'] = tokens.get('refresh_token')
        session['qbo_realm_id'] = realm_id
        return redirect('/?qbo=connected')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/qbo/pnl')
def qbo_pnl():
    """Get P&L from QuickBooks Online"""
    access_token = session.get('qbo_access_token')
    realm_id = session.get('qbo_realm_id') or QBO_REALM_ID
    if not access_token or not realm_id:
        return jsonify({'error': 'QBO not connected', 'connect_url': '/qbo/connect'}), 401
    try:
        today = datetime.now()
        start = today.replace(day=1).strftime('%Y-%m-%d')
        end = today.strftime('%Y-%m-%d')
        r = requests.get(
            f"https://quickbooks.api.intuit.com/v3/company/{realm_id}/reports/ProfitAndLoss",
            params={'start_date': start, 'end_date': end, 'minorversion': 65},
            headers={
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json'
            }
        )
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─── TEKMETRIC ────────────────────────────────────────────────────────────────

@app.route('/api/tekmetric/<shop>')
def tekmetric_data(shop):
    """Get shop data from Tekmetric"""
    if not TEKMETRIC_API_KEY:
        return jsonify({'error': 'Tekmetric API not configured yet'}), 503
    shop_id = TEKMETRIC_SHOP_ID_DCS if shop == 'dcs' else TEKMETRIC_SHOP_ID_EURO
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        month_start = datetime.now().replace(day=1).strftime('%Y-%m-%d')
        r = requests.get(
            f"https://api.tekmetric.com/api/v1/jobs",
            params={
                'shopId': shop_id,
                'startDate': month_start,
                'endDate': today,
                'status': 'CLOSED'
            },
            headers={'Authorization': f'Bearer {TEKMETRIC_API_KEY}'}
        )
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─── RUN ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
