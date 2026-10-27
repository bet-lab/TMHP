"""Validate intake metadata and optional payload originals; never run a model or publish."""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
PURPOSES = {'D', 'E_eta_v', 'E_eta_ov', 'E_f_loss', 'A', 'V'}
STATES = {'direct', 'conditional', 'not-applicable', 'unresolved'}

def read_csv(name):
    with (BASE / name).open(newline='') as stream:
        return list(csv.DictReader(stream))

def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path)
    parser.add_argument('--catalog-checkout', type=Path)
    args = parser.parse_args()
    sources = read_csv('sources.csv')
    models = read_csv('models.csv')
    eligibility = read_csv('eligibility.csv')
    fields = read_csv('field-dictionary.csv')
    source_map = {r['source_id']: r for r in sources}
    model_map = {r['record_key']: r for r in models}
    assert len(source_map) == len(sources), 'duplicate source IDs'
    assert len(model_map) == len(models), 'duplicate source-model-condition keys'
    for m in models:
        assert m['source_id'] in source_map, f"missing source: {m['source_id']}"
        assert m['dataset_id'] == source_map[m['source_id']]['dataset_id'], m['record_key']
        assert m['evidence_locator'] and m['verification'], m['record_key']
        if m['verification'] == 'legacy-project-extraction-not-reverified':
            assert not m['capacity_value_kw'] and not m['capacity_band_id'], m['record_key']
    seen = collections.defaultdict(set)
    for r in eligibility:
        assert r['record_key'] in model_map and r['eligibility'] in STATES, r
        assert r['purpose'] in PURPOSES and r['purpose'] not in seen[r['record_key']], r
        seen[r['record_key']].add(r['purpose'])
        assert r['reason_ko'] and r['evidence_locator'], r
        m = model_map[r['record_key']]
        for key in ['source_id', 'dataset_id', 'model_id', 'condition_set_id']:
            assert r[key] == m[key], (key, r['record_key'])
    assert all(seen[k] == PURPOSES for k in model_map), 'incomplete purposes'
    schemes = {s['id']: s for s in json.loads((BASE / 'capacity-bands.json').read_text())['schemes']}
    counts = collections.Counter()
    for scheme in schemes.values():
        b = scheme['boundaries']
        assert b[-1] is None and all(x < y for x, y in zip(b[:-2], b[1:-1]))
    for m in models:
        if not m['capacity_band_id']:
            assert not m['capacity_band_scheme_id'], m['record_key']
            continue
        s = schemes[m['capacity_band_scheme_id']]
        assert m['capacity_value_kw'] and m['rating_condition_id'] == s['rating_condition_id']
        assert m['capacity_basis'] == s['capacity_basis'] and m['equipment_type'] == s['equipment_type']
        if s['rating_speed_rps'] is not None:
            assert float(m['rating_speed_rps']) == s['rating_speed_rps']
        q = float(m['capacity_value_kw'])
        candidates = [f'[{lo},{hi if hi is not None else "inf"})' for lo, hi in zip(s['boundaries'], s['boundaries'][1:]) if q >= lo and (hi is None or q < hi)]
        assert candidates == [m['capacity_band_id']], m['record_key']
        counts[(s['id'], m['capacity_band_id'])] += 1
    for r in read_csv('capacity-coverage.csv'):
        assert int(r['rating_records']) == counts[(r['capacity_band_scheme_id'], r['capacity_band_id'])]
        assert r['independent_designs_confirmed'] == '' and r['independent_measured_operating_points'] == ''
    for f in fields:
        assert f['source_id'] in source_map and f['dataset_id'] == source_map[f['source_id']]['dataset_id']
        assert f['evidence_locator'] and f['reviewed_scope'] and len(f['source_sha256']) == 64
    summary = json.loads((BASE / 'summary.json').read_text())
    assert summary['sources'] == len(sources) and summary['source_model_condition_records'] == len(models)
    assert summary['eligibility_rows'] == len(eligibility) and summary['field_dictionary_rows'] == len(fields)
    result = dict(sources=len(sources), model_condition_records=len(models), purpose_rows=len(eligibility), field_rows=len(fields), metadata_relationships='passed')
    if bool(args.payload) != bool(args.catalog_checkout):
        parser.error('--payload and --catalog-checkout are required together')
    if args.payload:
        total_files = total_bytes = datasets = 0
        dataset_ids = {r['dataset_id'] for r in sources if r.get('local_payload_root')}
        for dataset_id in sorted(dataset_ids):
            d = args.catalog_checkout / 'energy-systems' / dataset_id
            data = json.loads((d / 'dataset.json').read_text())
            payload = args.payload / data['path']
            rows = [json.loads(line) for line in (d / 'files.jsonl').read_text().splitlines()]
            actual = {str(p.relative_to(payload)) for p in (payload / 'files').rglob('*') if p.is_file()}
            assert actual == {r['path'] for r in rows}, f'extra/missing files: {dataset_id}'
            tree = hashlib.sha256()
            for r in rows:
                f = payload / r['path']
                assert not f.is_symlink() and f.stat().st_size == r['bytes'] and sha(f) == r['sha256'], f
                tree.update(f"{r['path']}\0{r['bytes']}\0{r['sha256']}\n".encode())
                if r.get('role') == 'extracted-copy':
                    assert r['source_archive'] in actual, r
            assert tree.hexdigest() == data['tree_sha256'], dataset_id
            assert len(rows) == data['file_count'] and sum(r['bytes'] for r in rows) == data['bytes']
            total_files += len(rows)
            total_bytes += data['bytes']
            datasets += 1
        assert (datasets, total_files, total_bytes) == (summary['new_handoff_datasets'], summary['new_handoff_files'], summary['new_handoff_bytes'])
        result.update(payload_root=str(args.payload), payload_datasets=datasets, payload_files=total_files, payload_bytes=total_bytes, payload_hashes='passed')
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
