# -*- coding: utf-8 -*-
"""Seed a few example jobs at different stages so the board isn't empty.
Run once: python seed.py  (skips if jobs already exist)."""
import db
import workflow

EXAMPLES = [
    # (job dict, how many times to advance from Lead)
    (dict(owner='Maria Gonzalez', phone='561-555-0142', email='maria.g@example.com',
          address='1420 SW 12th Ave', city='Boca Raton', zip='33486',
          pcn='06424736110010', legal='SANDALFOOT COVE SEC 4 LT 1 BLK 3',
          ahj='Boca_Raton', system='Tile', existing='Concrete Tile',
          area='2850', slope='5:12', mrh='16', exposure='C', value='42000',
          notes='Referred by neighbor. HOA approval on file.'), 0),
    (dict(owner='James Whitfield', phone='561-555-0188', email='jwhitfield@example.com',
          address='305 NE 7th St', city='Delray Beach', zip='33444',
          pcn='12434609010050', legal='OSCEOLA PARK LT 5 BLK 1',
          ahj='Delray_Beach', system='Shingle', existing='Asphalt Shingle',
          area='2100', slope='6:12', mrh='14', exposure='C', value='19500',
          notes='Storm damage; insurance claim in progress.'), 2),
    (dict(owner='Coastal Holdings LLC', phone='561-555-0210', email='ops@coastalholdings.example',
          address='880 Ocean Dr', city='Boynton Beach', zip='33435',
          pcn='08434527030120', legal='COQUINA COVE LT 12',
          ahj='Boynton_Beach', system='Metal', existing='Standing Seam Metal',
          area='3400', slope='3:12', mrh='18', exposure='D', value='68000',
          notes='Commercial flat-to-metal conversion. Permit ready.'), 4),
]


def run():
    if db.all_jobs():
        print('Jobs already exist - skipping seed.')
        return
    for data, steps in EXAMPLES:
        data['stage'] = 'Lead'
        jid = db.add_job(data)
        workflow.run_automation(jid, 'Lead')
        for _ in range(steps):
            workflow.advance(jid)
        j = db.get_job(jid)
        print('Seeded #%d %-22s -> %-16s packet=%s' % (
            jid, j['owner'], j['stage'], j['packet'] or '-'))


if __name__ == '__main__':
    db.init_db()
    run()
