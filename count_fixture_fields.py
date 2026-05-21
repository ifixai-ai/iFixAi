import yaml, os
examples = 'ifixai/fixtures/examples'
for fname in sorted(os.listdir(examples)):
    if not fname.endswith('.yaml'):
        continue
    d = yaml.safe_load(open(os.path.join(examples, fname)))
    et = d.get('escalation_triggers') or []
    ec = d.get('expected_escalation_channels') or []
    hr = d.get('high_risk_actions') or []
    sc = d.get('sensitive_data_classes') or []
    print(fname + ': triggers=' + str(len(et)) + ' channels=' + str(len(ec)) + ' high_risk=' + str(len(hr)) + ' sensitive=' + str(len(sc)))
