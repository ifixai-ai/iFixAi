import yaml,os
for f in sorted(os.listdir('ifixai/fixtures/examples')):
    if not f.endswith('.yaml'): continue
    d=yaml.safe_load(open('ifixai/fixtures/examples/'+f))
    et=d.get('escalation_triggers') or []
    ec=d.get('expected_escalation_channels') or []
    print(f+': triggers='+str(len(et))+' channels='+str(len(ec)))
