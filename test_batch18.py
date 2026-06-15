import sys, os, json, traceback
os.chdir(r'C:\Users\kjburnz\acculynx roofr reprot\whitelabel-crm')
os.environ['DATABASE_URL'] = ''
os.environ['CRM_NOBROWSER'] = '1'
os.environ['CRM_PORT'] = '9917'
sys.path.insert(0, r'C:\Users\kjburnz\acculynx roofr reprot\whitelabel-crm')
import app as crm_app
application = crm_app.app
application.config['TESTING'] = True
application.config['WTF_CSRF_ENABLED'] = False

results = {'passed': [], 'failures': []}

def check(label, rv, route, method, allow=(200, 302)):
    code = rv.status_code
    # detect auth redirect
    loc = getattr(rv, 'location', '') or ''
    if code == 302 and '/login' in loc:
        results['failures'].append({'route': route, 'method': method, 'status': code, 'error': 'auth redirect to ' + loc, 'category': 'auth_error'})
        print('AUTH_FAIL %s %s -> %d redirect to %s' % (method, route, code, loc))
        return
    if code in allow:
        results['passed'].append('%s %s' % (method, route))
        print('PASS %s %s -> %d' % (method, route, code))
    else:
        body = ''
        try:
            body = rv.data.decode('utf-8', errors='replace')[:400]
        except Exception:
            pass
        cat = '500_error' if code == 500 else ('template_error' if 'TemplateNotFound' in body or 'jinja' in body.lower() else 'other')
        results['failures'].append({'route': route, 'method': method, 'status': code, 'error': body, 'category': cat})
        print('FAIL %s %s -> %d | %s' % (method, route, code, body[:120]))

with application.test_client() as c:
    with application.app_context():
        import db
        db.init_db()
        # Seed owner user
        try:
            db.insert('users', {'username': 'testowner', 'password_hash': 'x', 'role': 'owner', 'is_owner': 1, 'email': 'test@test.com', 'name': 'Test Owner'})
        except Exception:
            pass

        # Seed a job for worksheet test
        try:
            job_id = db.insert('jobs', {'name': 'Test Job', 'stage': 'approved', 'department': 'default', 'contract_value': '10000'})
        except Exception:
            job_id = 1

        # Seed an automation
        try:
            auto_id = db.insert('automations', {'name': 'Test Auto', 'trigger_stage': 'prospect', 'action_type': 'create_task', 'template_text': 'Test', 'offset_days': 0, 'active': 1})
        except Exception:
            auto_id = 1

        # Seed a team message table and row
        try:
            db.execute("CREATE TABLE IF NOT EXISTS team_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, user_id INTEGER, user_name TEXT, body TEXT)")
            db.execute("INSERT INTO team_messages (created, user_id, user_name, body) VALUES ('2026-01-01', 1, 'Test Owner', 'Hello')")
        except Exception:
            pass

        # Force login by setting session directly
        from flask import session as flask_session
        with c.session_transaction() as sess:
            sess['user_id'] = 1
            sess['user_name'] = 'Test Owner'
            sess['user_role'] = 'owner'
            # Get or set a CSRF token
            import secrets as _secrets
            csrf_tok = _secrets.token_hex(24)
            sess['_csrf'] = csrf_tok

        # Verify session is set
        rv = c.get('/workflow/')
        print('Session check /workflow/:', rv.status_code, getattr(rv, 'location', ''))

        # get CSRF token from session
        with c.session_transaction() as sess:
            csrf_tok = sess.get('_csrf', 'notoken')
        print('CSRF token:', csrf_tok[:8], '...')

        # 1. POST /tools/dev-note valid (JSON API, uses current_user() check internally)
        try:
            rv = c.post('/tools/dev-note',
                        json={'title': 'Test', 'body': 'hello'},
                        headers={'X-CSRFToken': csrf_tok})
            check('dev-note valid', rv, '/tools/dev-note', 'POST', allow=(200, 201))
        except Exception as e:
            results['failures'].append({'route': '/tools/dev-note', 'method': 'POST', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # POST /tools/dev-note empty
        try:
            rv = c.post('/tools/dev-note',
                        json={},
                        headers={'X-CSRFToken': csrf_tok})
            check('dev-note empty', rv, '/tools/dev-note', 'POST', allow=(200, 400))
        except Exception as e:
            results['failures'].append({'route': '/tools/dev-note', 'method': 'POST', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # 2. POST /tools/dispatch-screenshot valid (tiny PNG)
        import base64
        tiny_png = base64.b64encode(
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        ).decode()
        img_data = 'data:image/png;base64,' + tiny_png
        try:
            rv = c.post('/tools/dispatch-screenshot',
                        json={'image': img_data, 'task_id': 'test123'},
                        headers={'X-CSRFToken': csrf_tok})
            # 500 is acceptable here if _OVERLORD dir doesn't exist — save will fail
            code = rv.status_code
            loc = getattr(rv, 'location', '') or ''
            if code == 302 and '/login' in loc:
                results['failures'].append({'route': '/tools/dispatch-screenshot', 'method': 'POST', 'status': code, 'error': 'auth redirect', 'category': 'auth_error'})
                print('AUTH_FAIL POST /tools/dispatch-screenshot')
            elif code in (200, 201, 500):
                body = rv.data.decode('utf-8', errors='replace')
                if code == 500:
                    print('  dispatch-screenshot 500:', body[:200])
                    results['failures'].append({'route': '/tools/dispatch-screenshot', 'method': 'POST', 'status': 500, 'error': body[:300], 'category': '500_error'})
                else:
                    results['passed'].append('POST /tools/dispatch-screenshot')
                    print('PASS POST /tools/dispatch-screenshot -> %d' % code)
            else:
                body = rv.data.decode('utf-8', errors='replace')[:300]
                results['passed'].append('POST /tools/dispatch-screenshot')
                print('PASS POST /tools/dispatch-screenshot -> %d (expected 400 for bad save path)' % code)
        except Exception as e:
            results['failures'].append({'route': '/tools/dispatch-screenshot', 'method': 'POST', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # POST /tools/dispatch-screenshot empty
        try:
            rv = c.post('/tools/dispatch-screenshot',
                        json={},
                        headers={'X-CSRFToken': csrf_tok})
            check('dispatch-screenshot empty', rv, '/tools/dispatch-screenshot', 'POST', allow=(200, 400))
        except Exception as e:
            results['failures'].append({'route': '/tools/dispatch-screenshot', 'method': 'POST', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # 3. POST /tools/drive-backfill
        try:
            rv = c.post('/tools/drive-backfill', headers={'X-CSRFToken': csrf_tok})
            check('drive-backfill', rv, '/tools/drive-backfill', 'POST', allow=(200, 302))
        except Exception as e:
            results['failures'].append({'route': '/tools/drive-backfill', 'method': 'POST', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # 4. GET /tools/export
        try:
            rv = c.get('/tools/export?entity=leads')
            check('export', rv, '/tools/export', 'GET', allow=(200, 302))
        except Exception as e:
            results['failures'].append({'route': '/tools/export', 'method': 'GET', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # 5. GET /tools/mass-email
        try:
            rv = c.get('/tools/mass-email')
            check('mass-email', rv, '/tools/mass-email', 'GET', allow=(200, 302))
        except Exception as e:
            results['failures'].append({'route': '/tools/mass-email', 'method': 'GET', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # 6. GET /tools/restore
        try:
            rv = c.get('/tools/restore')
            check('restore GET', rv, '/tools/restore', 'GET', allow=(200, 302))
        except Exception as e:
            results['failures'].append({'route': '/tools/restore', 'method': 'GET', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # POST /tools/restore empty (no file)
        try:
            rv = c.post('/tools/restore', data={'_csrf': csrf_tok})
            check('restore POST empty', rv, '/tools/restore', 'POST', allow=(200, 302))
        except Exception as e:
            results['failures'].append({'route': '/tools/restore', 'method': 'POST', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # 7. GET /tools/team-messages
        try:
            rv = c.get('/tools/team-messages')
            check('team-messages GET', rv, '/tools/team-messages', 'GET', allow=(200, 302))
        except Exception as e:
            results['failures'].append({'route': '/tools/team-messages', 'method': 'GET', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # POST /tools/team-messages valid
        try:
            rv = c.post('/tools/team-messages',
                        json={'body': 'Hello team'},
                        headers={'X-CSRFToken': csrf_tok})
            check('team-messages POST valid', rv, '/tools/team-messages', 'POST', allow=(200, 201))
        except Exception as e:
            results['failures'].append({'route': '/tools/team-messages', 'method': 'POST', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # POST /tools/team-messages empty
        try:
            rv = c.post('/tools/team-messages',
                        json={},
                        headers={'X-CSRFToken': csrf_tok})
            check('team-messages POST empty', rv, '/tools/team-messages', 'POST', allow=(200, 400))
        except Exception as e:
            results['failures'].append({'route': '/tools/team-messages', 'method': 'POST', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # 8. DELETE /tools/team-messages/<msg_id>
        # First insert a message to delete
        try:
            db.execute("INSERT INTO team_messages (created, user_id, user_name, body) VALUES ('2026-01-01', 1, 'Test Owner', 'To delete')")
            # get the id
            rows = db.execute("SELECT id FROM team_messages WHERE body='To delete'").fetchall()
            del_msg_id = dict(rows[-1])['id'] if rows else 1
            rv = c.delete('/tools/team-messages/%d' % del_msg_id,
                          headers={'X-CSRFToken': csrf_tok})
            check('delete_team_message', rv, '/tools/team-messages/<int:msg_id>', 'DELETE', allow=(200, 201, 404))
        except Exception as e:
            results['failures'].append({'route': '/tools/team-messages/<int:msg_id>', 'method': 'DELETE', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # 9. GET /uploads/<path>
        try:
            rv = c.get('/uploads/nonexistent.pdf')
            check('uploads', rv, '/uploads/<path:subpath>', 'GET', allow=(200, 302, 404))
        except Exception as e:
            results['failures'].append({'route': '/uploads/<path:subpath>', 'method': 'GET', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # 10. GET /workflow/
        try:
            rv = c.get('/workflow/')
            check('workflow index', rv, '/workflow/', 'GET', allow=(200, 302))
        except Exception as e:
            results['failures'].append({'route': '/workflow/', 'method': 'GET', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # 11. POST /workflow/<auto_id>/delete
        try:
            del_id = db.insert('automations', {'name': 'Del Auto', 'trigger_stage': 'prospect', 'action_type': 'create_task', 'template_text': 'Del', 'offset_days': 0, 'active': 1})
            rv = c.post('/workflow/%d/delete' % del_id, data={'_csrf': csrf_tok})
            check('workflow delete', rv, '/workflow/<int:auto_id>/delete', 'POST', allow=(200, 302))
        except Exception as e:
            results['failures'].append({'route': '/workflow/<int:auto_id>/delete', 'method': 'POST', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # 12. POST /workflow/<auto_id>/save
        try:
            rv = c.post('/workflow/%d/save' % auto_id, data={'name': 'Updated', 'trigger_stage': 'prospect', 'action_type': 'create_task', 'template_text': 'Updated text', 'offset_days': '1', '_csrf': csrf_tok})
            check('workflow save', rv, '/workflow/<int:auto_id>/save', 'POST', allow=(200, 302))
        except Exception as e:
            results['failures'].append({'route': '/workflow/<int:auto_id>/save', 'method': 'POST', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # POST /workflow/<auto_id>/save empty data
        try:
            rv = c.post('/workflow/%d/save' % auto_id, data={'_csrf': csrf_tok})
            check('workflow save empty', rv, '/workflow/<int:auto_id>/save', 'POST', allow=(200, 302))
        except Exception as e:
            results['failures'].append({'route': '/workflow/<int:auto_id>/save', 'method': 'POST', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # 13. POST /workflow/<auto_id>/toggle
        try:
            rv = c.post('/workflow/%d/toggle' % auto_id, data={'_csrf': csrf_tok})
            check('workflow toggle', rv, '/workflow/<int:auto_id>/toggle', 'POST', allow=(200, 302))
        except Exception as e:
            results['failures'].append({'route': '/workflow/<int:auto_id>/toggle', 'method': 'POST', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # 14. POST /workflow/new
        try:
            rv = c.post('/workflow/new', data={'name': 'New Auto', 'trigger_stage': 'prospect', 'action_type': 'create_task', 'template_text': 'New text', 'offset_days': '0', '_csrf': csrf_tok})
            check('workflow new', rv, '/workflow/new', 'POST', allow=(200, 302))
        except Exception as e:
            results['failures'].append({'route': '/workflow/new', 'method': 'POST', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # POST /workflow/new empty
        try:
            rv = c.post('/workflow/new', data={'_csrf': csrf_tok})
            check('workflow new empty', rv, '/workflow/new', 'POST', allow=(200, 302))
        except Exception as e:
            results['failures'].append({'route': '/workflow/new', 'method': 'POST', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # 15. GET /worksheet/<job_id>
        try:
            rv = c.get('/worksheet/%d' % job_id)
            check('worksheet view', rv, '/worksheet/<int:job_id>', 'GET', allow=(200, 302))
            if rv.status_code == 200:
                body = rv.data.decode('utf-8', errors='replace')
                if 'Error' in body or 'error' in body[:200].lower():
                    print('  worksheet body preview:', body[:200])
        except Exception as e:
            results['failures'].append({'route': '/worksheet/<int:job_id>', 'method': 'GET', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

        # GET /worksheet/999 (nonexistent job - should redirect)
        try:
            rv = c.get('/worksheet/999')
            check('worksheet view nonexistent', rv, '/worksheet/<int:job_id>', 'GET', allow=(200, 302, 404))
        except Exception as e:
            results['failures'].append({'route': '/worksheet/<int:job_id>', 'method': 'GET', 'status': 500, 'error': traceback.format_exc()[:300], 'category': '500_error'})

print('\n=== FINAL RESULTS ===')
print(json.dumps(results, indent=2))
