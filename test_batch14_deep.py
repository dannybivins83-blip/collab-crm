"""Deeper inspection - follow redirects and check rendered content for errors."""
import sys, os, json, traceback
os.chdir(r'C:\Users\kjburnz\acculynx roofr reprot\whitelabel-crm')
os.environ['DATABASE_URL'] = ''
os.environ['CRM_NOBROWSER'] = '1'
os.environ['CRM_PORT'] = '9913'
sys.path.insert(0, r'C:\Users\kjburnz\acculynx roofr reprot\whitelabel-crm')
import app as crm_app
application = crm_app.app
application.config['TESTING'] = True
application.config['WTF_CSRF_ENABLED'] = False

results = {'passed': [], 'failures': []}

ERROR_MARKERS = [
    b'Internal Server Error',
    b'TemplateSyntaxError',
    b'TemplateNotFound',
    b'UndefinedError',
    b'BuildError',
    b'Traceback (most recent call last)',
    b'jinja2.exceptions',
    b'werkzeug.exceptions',
    b'500',
]

def has_error(body):
    for marker in ERROR_MARKERS:
        if marker in body:
            return marker.decode('utf-8', errors='replace')
    return None

with application.test_client() as c:
    with application.app_context():
        import db
        db.init_db()
        from werkzeug.security import generate_password_hash
        ph = generate_password_hash('testpass123')

        try:
            uid = db.insert('users', {'password_hash': ph, 'role': 'admin', 'is_owner': 1,
                                      'email': 'testadmin@test.com', 'name': 'Test Admin', 'active': 1})
        except Exception:
            rows = db.all_rows('users', 'email=?', ('testadmin@test.com',))
            if rows:
                uid = rows[0]['id']
                db.update('users', uid, password_hash=ph, active=1, role='admin')
            else:
                rows2 = db.all_rows('users')
                uid = rows2[-1]['id'] if rows2 else 1
                db.update('users', uid, password_hash=ph, email='testadmin@test.com', active=1, role='admin')

        try:
            jid = db.insert('jobs', {'name': 'Test Job', 'address': '123 Main St', 'city': 'Miami',
                                     'state': 'FL', 'zip': '33101', 'portal_token': 'testtoken123'})
        except Exception:
            rows = db.all_rows('jobs', 'portal_token=?', ('testtoken123',))
            jid = rows[0]['id'] if rows else 1

        try:
            pid = db.insert('signup_packets', {
                'created': db.now(), 'job_id': jid, 'system': 'shingle',
                'status': 'sent', 'customer_name': 'Test Customer', 'responses': '{}'
            })
        except Exception:
            rows = db.all_rows('signup_packets', 'job_id=?', (jid,))
            pid = rows[0]['id'] if rows else 1

        c.post('/login', data={'email': 'testadmin@test.com', 'password': 'testpass123'})

        def test_follow(route, method, data=None, expected_statuses=(200, 302)):
            tag = f'{method} {route}'
            try:
                if method == 'GET':
                    r = c.get(route, follow_redirects=True)
                else:
                    r = c.post(route, data=data or {}, follow_redirects=True)
                status = r.status_code
                body = r.data
                err = has_error(body)
                if err and status != 500:
                    # Check more carefully - "500" text may be in URLs or versions
                    if b'Internal Server Error' in body or b'TemplateSyntaxError' in body or b'UndefinedError' in body:
                        snippet = body.decode('utf-8', errors='replace')[:800]
                        results['failures'].append({'route': route, 'method': method, 'status': status,
                                                    'error': f'Error marker ({err}) in rendered body: {snippet[:300]}',
                                                    'category': 'template_error'})
                        print(f'TEMPLATE ERROR {tag}: {err}')
                        return
                if status == 500:
                    snippet = body.decode('utf-8', errors='replace')[:600]
                    results['failures'].append({'route': route, 'method': method, 'status': 500,
                                                'error': snippet, 'category': '500_error'})
                    print(f'500 ERROR {tag}')
                    return
                if status not in expected_statuses:
                    results['failures'].append({'route': route, 'method': method, 'status': status,
                                                'error': f'Unexpected status {status}', 'category': 'other'})
                    print(f'UNEXPECTED {tag}: {status}')
                    return
                results['passed'].append(tag)
                print(f'OK {tag}: {status} ({len(body)} bytes)')
            except Exception as e:
                tb = traceback.format_exc()
                print(f'EXCEPTION {tag}: {tb}')
                results['failures'].append({'route': route, 'method': method, 'status': 500,
                                            'error': tb, 'category': '500_error'})

        # Follow all redirects to see final rendered output
        test_follow('/roof-reports/new', 'GET')
        test_follow('/search/suggest?q=te', 'GET')
        test_follow('/settings/', 'GET')
        settings_data = {
            'name': 'Test CRM', 'legal_name': '', 'tagline': '', 'license': '',
            'qualifier': '', 'address': '', 'city': '', 'state': '', 'zip': '',
            'phone': '', 'email': '', 'website': '',
            'color_masthead': '', 'color_primary': '', 'color_accent': '',
            'color_warn': '', 'color_danger': '', 'default_county': '',
            'departments': '', 'terms': '', 'photo_app_url': '', 'tutorials': ''
        }
        test_follow('/settings/', 'POST', settings_data)
        test_follow('/settings/department', 'POST', {'department': 'sales'})
        test_follow('/settings/logo/clear', 'POST', {})
        test_follow('/settings/users/new', 'POST', {
            'name': 'New User2', 'email': 'newuser3@test.com', 'role': 'sales', 'password': 'pass123'
        })
        test_follow(f'/signups/job/{jid}/create', 'POST', {'system': 'shingle'})
        test_follow(f'/signups/portal/testtoken123/{pid}', 'GET')
        test_follow('/sitecam/', 'GET')
        test_follow('/sso/apps', 'GET')
        test_follow('/sso/token/sitecam', 'GET', expected_statuses=(200, 503))
        test_follow('/sync/', 'GET')

print(json.dumps(results, indent=2))
