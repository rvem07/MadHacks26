"""Read physical Unity Catalog outputs; no local/CSV source is used."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    from afd.watchstore import DeltaDocuments
    parser = argparse.ArgumentParser()
    parser.add_argument('--warehouse-id', required=True)
    parser.add_argument('--catalog', default='workspace')
    parser.add_argument('--schema', default='sentinel_gold')
    args = parser.parse_args()
    db = DeltaDocuments(args.warehouse_id, args.catalog, args.schema)
    active = db.get('active_pipeline')
    batch = active['batch_id']
    stats = db.pipeline_rows('pipeline_stats', batch)[0]
    nodes = db.pipeline_rows('nodes', batch)
    edges = db.pipeline_rows('edges', batch)
    quarantine = db.pipeline_rows('quarantine', batch)
    people = [n for n in nodes if n['type'] == 'person']
    raw = db.pipeline_rows('bronze_org_chart', batch)
    responsibilities = db.pipeline_rows('bronze_responsibilities', batch)
    context = db.pipeline_rows('person_context', batch)
    assert (len(raw),len(people),stats['duplicates_merged'],stats['quarantined_count']) == (12,10,1,4)
    assert len(responsibilities) == 10 and len(context) == 25
    assert len(quarantine) == 6 and len({(q['reason'],q['group_id']) for q in quarantine}) == 4
    assert {q['reason'] for q in quarantine} == {'multiple_managers','reporting_cycle','missing_manager','unowned_topic'}
    review = {p['id'] for p in people if p['properties']['needs_review']}
    assert len(review) == 4
    routes = db.pipeline_rows('routing_chains', batch)
    assert not review & {r['person_email'] for r in routes}
    assert not review & {r['owner_email'] for r in routes}
    assert [r['person_email'].split('.')[0] for r in sorted(routes,key=lambda r:r['depth']) if r['topic'] == 'security incident'] == ['ben','nadia','elena']
    ids = {n['id'] for n in nodes}
    assert all(e['src'] in ids and e['dst'] in ids for e in edges)
    assert {e['type'] for e in edges} == {'reports_to','member_of','responsible_for'}
    assert any(r['email'] == ' Kofi.Mensah@wildguardlabs.org ' for r in raw)
    print(json.dumps({'result':'passed','storage':'Unity Catalog Delta','batch_id':batch,
                      'raw_people':len(raw),'unique_people':len(people),'duplicates_merged':stats['duplicates_merged'],
                      'quarantined_issues':stats['quarantined_count'],'quarantined_records':len(quarantine),
                      'quarantine_reasons':sorted({q['reason'] for q in quarantine}),
                      'nodes':len(nodes),'edges':len(edges),'context_rows':len(context)},indent=2))


if __name__ == '__main__':
    main()
