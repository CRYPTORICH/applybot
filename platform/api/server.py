"""
SiteLaunch Platform API
Industry-agnostic lead management server.
Deploy on Render free tier or run locally.
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='..')
CORS(app)

BASE_DIR = Path(__file__).resolve().parent.parent
LEADS_DIR = BASE_DIR / 'lead-sources'
CONFIG_DIR = BASE_DIR / 'config'

# ── In-memory lead store (backed by JSON files) ──
lead_cache = {}

def load_leads(industry_id):
    """Load leads for an industry from JSON file."""
    if industry_id in lead_cache:
        return lead_cache[industry_id]

    paths = [
        LEADS_DIR / f'{industry_id}-leads.json',
        LEADS_DIR / f'{industry_id}_leads.json',
        BASE_DIR / f'{industry_id}-leads.json',
    ]
    for p in paths:
        if p.exists():
            try:
                with open(p) as f:
                    data = json.load(f)
                lead_cache[industry_id] = data
                return data
            except (json.JSONDecodeError, OSError):
                continue

    lead_cache[industry_id] = []
    return []

def save_leads(industry_id, leads):
    """Save leads to JSON file."""
    path = LEADS_DIR / f'{industry_id}-leads.json'
    LEADS_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(leads, f, indent=2, default=str)
    lead_cache[industry_id] = leads


# ── API Routes ──

@app.route('/')
def index():
    """Serve the CRM."""
    return send_from_directory(str(BASE_DIR), 'index.html')

@app.route('/api/industries')
def get_industries():
    """Return all industry configurations."""
    path = CONFIG_DIR / 'industries.json'
    if path.exists():
        with open(path) as f:
            return jsonify(json.load(f))
    return jsonify({}), 404

@app.route('/api/industries/<industry_id>')
def get_industry(industry_id):
    """Return a single industry configuration."""
    path = CONFIG_DIR / 'industries.json'
    if path.exists():
        with open(path) as f:
            industries = json.load(f)
        for name, config in industries.items():
            if config.get('id') == industry_id:
                return jsonify(config)
        return jsonify({'error': 'Industry not found'}), 404
    return jsonify({'error': 'Config not found'}), 404

@app.route('/api/leads/<industry_id>')
def get_leads(industry_id):
    """Get all leads for an industry."""
    leads = load_leads(industry_id)

    # Filtering
    status = request.args.get('status')
    lead_type = request.args.get('lead_type')
    zip_code = request.args.get('zip')
    search = request.args.get('search', '').lower()
    limit = request.args.get('limit', type=int)
    offset = request.args.get('offset', type=int, default=0)

    filtered = leads
    if status:
        filtered = [l for l in filtered if l.get('status', 'new') == status]
    if lead_type:
        filtered = [l for l in filtered if l.get('lead_type', '') == lead_type]
    if zip_code:
        filtered = [l for l in filtered if str(l.get('zip', '')) == zip_code]
    if search:
        filtered = [l for l in filtered if
                    search in (l.get('name', '') + l.get('address', '') + l.get('phone', '') + l.get('city', '')).lower()]

    total = len(filtered)

    if limit:
        filtered = filtered[offset:offset + limit]

    return jsonify({
        'total': total,
        'offset': offset,
        'limit': limit,
        'leads': filtered
    })

@app.route('/api/leads/<industry_id>', methods=['POST'])
def create_lead(industry_id):
    """Create one or more leads."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    leads_data = data if isinstance(data, list) else [data]
    existing = load_leads(industry_id)

    created = []
    for lead in leads_data:
        lead['id'] = lead.get('id', str(uuid.uuid4())[:8])
        lead['created_at'] = lead.get('created_at', datetime.now(timezone.utc).isoformat())
        lead['status'] = lead.get('status', 'new')
        lead['industry'] = industry_id
        existing.append(lead)
        created.append(lead)

    save_leads(industry_id, existing)
    return jsonify({'created': len(created), 'leads': created}), 201

@app.route('/api/leads/<industry_id>/<lead_id>', methods=['PATCH'])
def update_lead(industry_id, lead_id):
    """Update lead status, notes, or callback."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    leads = load_leads(industry_id)
    for lead in leads:
        if lead.get('id') == lead_id or lead.get('name') == lead_id:
            for key in ('status', 'notes', 'callback', 'est_value', 'phone'):
                if key in data:
                    lead[key] = data[key]
            lead['updated_at'] = datetime.now(timezone.utc).isoformat()
            save_leads(industry_id, leads)
            return jsonify(lead)

    return jsonify({'error': 'Lead not found'}), 404

@app.route('/api/leads/<industry_id>/<lead_id>', methods=['DELETE'])
def delete_lead(industry_id, lead_id):
    """Delete a lead."""
    leads = load_leads(industry_id)
    original_len = len(leads)
    leads = [l for l in leads if l.get('id') != lead_id and l.get('name') != lead_id]

    if len(leads) == original_len:
        return jsonify({'error': 'Lead not found'}), 404

    save_leads(industry_id, leads)
    return jsonify({'deleted': True})

@app.route('/api/leads/<industry_id>/import', methods=['POST'])
def import_leads(industry_id):
    """Bulk import leads — accepts JSON array or pipe-delimited text."""
    content_type = request.headers.get('Content-Type', '')
    data = request.get_json(silent=True)
    leads_data = []

    if data:
        leads_data = data if isinstance(data, list) else [data]
    else:
        # Try pipe-delimited text import
        raw = request.get_data(as_text=True)
        if raw:
            for line in raw.strip().split('\n'):
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 2:
                    leads_data.append({
                        'name': parts[0],
                        'phone': parts[1],
                        'lead_type': parts[2] if len(parts) > 2 else 'cold',
                        'address': parts[3] if len(parts) > 3 else '',
                        'est_value': parts[4] if len(parts) > 4 else '',
                        'city': parts[5] if len(parts) > 5 else '',
                        'state': parts[6] if len(parts) > 6 else '',
                        'zip': parts[7] if len(parts) > 7 else ''
                    })

    if not leads_data:
        return jsonify({'error': 'No data to import'}), 400

    existing = load_leads(industry_id)
    created = []
    for lead in leads_data:
        lead['id'] = lead.get('id', str(uuid.uuid4())[:8])
        lead['created_at'] = lead.get('created_at', datetime.now(timezone.utc).isoformat())
        lead['status'] = lead.get('status', 'new')
        lead['industry'] = industry_id
        existing.append(lead)
        created.append(lead)

    save_leads(industry_id, existing)
    return jsonify({'imported': len(created), 'total': len(existing)}), 201

@app.route('/api/stats/<industry_id>')
def get_stats(industry_id):
    """Get lead statistics for an industry."""
    leads = load_leads(industry_id)
    pipeline_counts = {}
    lead_type_counts = {}
    zip_counts = {}

    for l in leads:
        status = l.get('status', 'new')
        pipeline_counts[status] = pipeline_counts.get(status, 0) + 1

        lt = l.get('lead_type', l.get('category', 'unknown'))
        lead_type_counts[lt] = lead_type_counts.get(lt, 0) + 1

        zp = str(l.get('zip', ''))
        if zp:
            zip_counts[zp] = zip_counts.get(zp, 0) + 1

    return jsonify({
        'total': len(leads),
        'pipeline': pipeline_counts,
        'lead_types': lead_type_counts,
        'zips': zip_counts
    })

@app.route('/health')
def health():
    """Health check for Render/uptime monitoring."""
    return jsonify({'ok': True, 'status': 'live', 'time': datetime.now(timezone.utc).isoformat()})


# ── Entry Point ──
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
