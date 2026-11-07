import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('qa_evidence', ROOT/'tools/xr_options_evidence/collect.py')
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def test_ledger_has_116_unique_rows_and_no_unsupported_pass_claims():
    ledger = json.loads((ROOT/'docs/xr-options-build-coverage.json').read_text())
    assert len(ledger['rows']) == ledger['inventory_count'] == 116
    assert len({r['id'] for r in ledger['rows']}) == 116
    for row in ledger['rows']:
        assert (ROOT/row['source']['file']).is_file()
        assert row['implementation_status'] in {'pending_integration', 'verified', 'blocked'}
        if row['implementation_status'] == 'verified':
            assert row['evidence']
    assert any(g['id'] == 'maximize_v2' for g in ledger['gates'])


def test_artifacts_cannot_be_html_mockups_or_renamed_html(tmp_path):
    for name in ['mock.html', 'mock.png', 'mock.mp4']:
        path = tmp_path/name; path.write_text('<html>design only</html>')
        with pytest.raises(ValueError): evidence.artifact(path)


def test_artifact_hash_is_integrity_only(tmp_path):
    path = tmp_path/'test.png'; path.write_bytes(b'\x89PNG\r\n\x1a\n' + b'fixture')
    result = evidence.artifact(path)
    assert len(result['sha256']) == 64
    assert 'unverified' in result['provenance']
