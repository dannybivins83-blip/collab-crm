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

with application.test_client() as c:
    with application.app_context():
        import db
        db.init_db()
        from werkzeug.security import generate_password_hash
        ph = generate_password_hash('testpass123')

        # Insert admin user
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

        # Insert a second user to test deletion
        try:
            uid2 = db.insert('users', {'password_hash': ph, 'role': 'sales',
                                       'email': 'del@test.com', 'name': 'Del User', 'active': 1})
        except Exception:
            uid2 = uid + 1

        # Seed lead
        try:
            lid = db.insert('leads', {'name': 'Test Lead', 'address': '123 Main St', 'city': 'Miami',
                                      'state': 'FL', 'zip': '33101', 'stage': 'new',
                                      'email': 'lead@test.com', 'phone': '5551234567'})
        except Exception:
            rows = db.all_rows('leads')
            lid = rows[0]['id'] if rows else 1

        # Seed job with portal_token
        try:
            jid = db.insert('jobs', {'name': 'Test Job', 'address': '123 Main St', 'city': 'Miami',
                                     'state': 'FL', 'zip': '33101', 'portal_token': 'testtoken123'})
        except Exception:
            rows = db.all_rows('jobs', 'portal_token=?', ('testtoken123',))
            if rows:
                jid = rows[0]['id']
            else:
                rows2 = db.all_rows('jobs')
                jid = rows2[0]['id'] if rows2 else 1

        # Seed signup packet for the job
        try:
            pid = db.insert('signup_packets', {
                'created': db.now(), 'job_id': jid, 'system': 'shingle',
                'status': 'sent', 'customer_name': 'Test Customer', 'responses': '{}'
            })
        except Exception:
            rows = db.all_rows('signup_packets', 'job_id=?', (jid,))
            pid = rows[0]['id'] if rows else 1

        print(f'uid={uid}, uid2={uid2}, lid={lid}, jid={jid}, pid={pid}')

        # Log in as admin
        c.post('/login', data={'email': 'testadmin@test.com', 'password': 'testpass123'})
        with c.session_transaction() as sess:
            print('Session role:', sess.get('user_role'), 'uid:', sess.get('user_id'))

        def test(route, method, data=None, label=None, follow=False, expected=(200, 302, 303)):
            tag = label or f'{method} {route}'
            try:
                if method == 'GET':
                    r = c.get(route, follow_redirects=follow)
                else:
                    r = c.post(route, data=data or {}, follow_redirects=follow)

                status = r.status_code
                body = r.data
                err_body = body.decode('utf-8', errors='replace')

                if status == 500 or b'Internal Server Error' in body:
                    snippet = err_body[:600]
                    results['failures'].append({'route': route, 'method': method, 'status': 500,
                                                'error': snippet, 'category': '500_error'})
                elif (b'TemplateSyntaxError' in body or b'TemplateNotFound' in body
                      or b'UndefinedError' in body or b'jinja2' in body.lower()):
                    snippet = err_body[:600]
                    results['failures'].append({'route': route, 'method': method, 'status': status,
                                                'error': snippet, 'category': 'template_error'})
                elif status not in expected:
                    loc = r.headers.get('Location', '')
                    results['failures'].append({'route': route, 'method': method, 'status': status,
                                                'error': f'Got {status}, expected {expected}. Location={loc}',
                                                'category': 'other'})
                else:
                    results['passed'].append(tag)
                print(f'{tag}: {status} {r.headers.get("Location", "")}')
            except Exception as e:
                tb = traceback.format_exc()
                print(f'EXCEPTION {tag}: {tb}')
                results['failures'].append({'route': route, 'method': method, 'status': 500,
                                            'error': tb, 'category': '500_error'})

        # 1. GET /roof-reports/new — engine not configured, redirects to index
        test('/roof-reports/new', 'GET')

        # 2. POST /roof-reports/new — engine not configured, redirects
        test('/roof-reports/new', 'POST', {})

        # 3. GET /search/suggest
        test('/search/suggest?q=te', 'GET')

        # 4. GET /settings/
        test('/settings/', 'GET')

        # 5. POST /settings/
        settings_data = {
            'name': 'Test CRM', 'legal_name': '', 'tagline': '', 'license': '',
            'qualifier': '', 'address': '', 'city': '', 'state': '', 'zip': '',
            'phone': '', 'email': '', 'website': '',
            'color_masthead': '', 'color_primary': '', 'color_accent': '',
            'color_warn': '', 'color_danger': '', 'default_county': '',
            'departments': '', 'terms': '', 'photo_app_url': '', 'tutorials': ''
        }
        test('/settings/', 'POST', settings_data)

        # 6. POST /settings/department
        test('/settings/department', 'POST', {'department': 'sales'})

        # 7. POST /settings/logo/clear
        test('/settings/logo/clear', 'POST', {})

        # 8. POST /settings/users/<user_id>/delete
        test(f'/settings/users/{uid2}/delete', 'POST', {})

        # 9. POST /settings/users/new
        test('/settings/users/new', 'POST', {
            'name': 'New User', 'email': 'newuser2@test.com', 'role': 'sales', 'password': 'pass123'
        })

        # 10. POST /signups/job/<job_id>/create
        test(f'/signups/job/{jid}/create', 'POST', {'system': 'shingle'})

        # 11. GET /signups/portal/<token>/<packet_id>
        test(f'/signups/portal/testtoken123/{pid}', 'GET', expected=(200, 302, 404))

        # 12. POST /signups/portal/<token>/<packet_id>/complete
        test(f'/signups/portal/testtoken123/{pid}/complete', 'POST', {}, expected=(200, 302, 303, 404))

        # 13. GET /sitecam/
        test('/sitecam/', 'GET')

        # 14. POST /sitecam/gallery — requires HMAC auth, expect 401
        test('/sitecam/gallery', 'POST', {}, expected=(200, 302, 401))

        # 15. GET /sso/apps
        test('/sso/apps', 'GET')

        # 16. GET /sso/token/<app_id>
        test('/sso/token/sitecam', 'GET', expected=(200, 302, 503))

        # 17. GET /sync/
        test('/sync/', 'GET')

print(json.dumps(results, indent=2))
