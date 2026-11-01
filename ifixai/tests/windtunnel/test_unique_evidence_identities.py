"""A resealed ambiguous bundle must not shadow a lost detection."""
import json

import pytest

from ifixai.windtunnel.defects import CATALOGUE
from ifixai.windtunnel.evidence import canonical_digest, create_bundle, load_bundle
from ifixai.windtunnel.measure import InspectionOutcome, WindTunnelReport


def _pair():
    profile = CATALOGUE["over_refusing"]
    report = WindTunnelReport(profile.name, (InspectionOutcome("B01", "detected", 1.0, 0.0, 1.0, True),))
    return profile, report


@pytest.mark.parametrize("duplicate", ["profile", "inspection"])
def test_loading_resealed_duplicate_identities_is_refused(tmp_path, duplicate):
    raw = create_bundle((_pair(),), "customer_support").model_dump(mode="json")
    if duplicate == "profile":
        raw["results"].append(raw["results"][0])
    else:
        raw["results"][0]["outcomes"].append(raw["results"][0]["outcomes"][0])
    raw["integrity_digest"] = canonical_digest({key: value for key, value in raw.items() if key != "integrity_digest"})
    path = tmp_path / "ambiguous.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="duplicate"):
        load_bundle(path)


def test_creation_refuses_duplicate_profiles():
    with pytest.raises(ValueError, match="duplicate"):
        create_bundle((_pair(), _pair()), "customer_support")


def test_creation_refuses_duplicate_inspections():
    profile, report = _pair()
    repeated = WindTunnelReport(profile.name, report.outcomes * 2)
    with pytest.raises(ValueError, match="duplicate"):
        create_bundle(((profile, repeated),), "customer_support")
