"""
280-build permit packet regression test.
Runs every AHJ x system combination and reports PASS/FAIL.
A build PASSES if:
  - build_packet() returns the output path without raising
  - the output file is a valid PDF (starts with %PDF and is > 0 bytes)
  - the file has at least one page

Run:
  cd "...\\permit_packet_builder"
  python test_280.py
"""
import os, sys, tempfile, traceback, time

# -- path setup so we can import build.py from the same folder ----------
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from build import build_packet, list_ahjs, SYSTEMS

DUMMY_CLIENT = {
    'owner':   'Test Owner',
    'address': '123 Test St',
    'city':    'Boynton Beach',
    'state':   'FL',
    'zip':     '33426',
    'phone':   '561-000-0000',
    'pcn':     '',
    'legal':   '',
    'value':   '20000',
}

def is_valid_pdf(path):
    """Returns (ok, reason)."""
    if not os.path.exists(path):
        return False, 'file not created'
    size = os.path.getsize(path)
    if size == 0:
        return False, 'file is empty'
    with open(path, 'rb') as f:
        header = f.read(5)
    if header != b'%PDF-':
        return False, 'not a PDF (bad header: %r)' % header
    # quick page count via pypdf
    try:
        from pypdf import PdfReader
        r = PdfReader(path)
        if len(r.pages) == 0:
            return False, 'PDF has 0 pages'
    except Exception as e:
        return False, 'pypdf error: %s' % e
    return True, 'ok'

def run():
    ahjs = list_ahjs()
    systems = list(SYSTEMS.keys())
    total = len(ahjs) * len(systems)

    print(f'AHJs found: {len(ahjs)}  Systems: {len(systems)}  Total builds: {total}')
    print('-' * 60)

    passes = 0
    failures = []
    t0 = time.time()

    with tempfile.TemporaryDirectory() as tmpdir:
        for ahj in ahjs:
            for system in systems:
                label = f'{ahj} / {system}'
                out_path = os.path.join(tmpdir, f'{ahj}_{system}.pdf')
                try:
                    result = build_packet(
                        client=DUMMY_CLIENT,
                        ahj=ahj,
                        system=system,
                        attachment_paths=[],
                        out_path=out_path,
                        fetch_pa=False,   # skip live HTTP calls
                    )
                    ok, reason = is_valid_pdf(out_path)
                    if ok:
                        passes += 1
                    else:
                        failures.append((label, reason))
                except Exception as e:
                    failures.append((label, traceback.format_exc().strip().splitlines()[-1]))

    elapsed = time.time() - t0
    fail_count = len(failures)
    print(f'PASS: {passes}  FAIL: {fail_count}')
    if failures:
        print()
        for label, reason in failures:
            print(f'  FAIL  {label}')
            print(f'        {reason}')
    print(f'\n({elapsed:.1f}s for {total} builds)')

if __name__ == '__main__':
    run()
